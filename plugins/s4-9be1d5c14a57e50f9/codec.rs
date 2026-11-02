// Counted per-column dictionary/run mode over whole-block fallback.
// T1 = NUL column streams; T2 = whole-block byte planes + lengths;
// T3 = per-column raw-vs-plane mix; T4 (TAG_DICT) = per-column raw-vs-dict
// mix for columns with many repeated lexemes; T5 (TAG_AFFIX) = per-column
// raw-vs-affix mix factoring one common byte prefix + one common suffix per
// column (e.g. shared quotes/padding/exponent marks) with sparse middles.
// Affix stores prefix/suffix once and NUL-joined exact middles; lengths,
// quotes, padding, exponents, whitespace and line endings round-trip exactly.
// Local affix choice uses raw byte size; global winner uses actual zstd
// bytes, so old modes stay when affix costs more. No numeric conversion.
use crate::{Codec, CodecError};
use crate::toolkit::{zstd_compress, zstd_decompress};
use std::collections::HashMap;

pub struct Candidate;

const TAG_RAW: u8 = 0;
const TAG_COL: u8 = 1;
const TAG_PLANE: u8 = 2;
const TAG_MIX: u8 = 3;
const TAG_DICT: u8 = 4;
const TAG_AFFIX: u8 = 5;

fn put_varint(out: &mut Vec<u8>, mut v: u64) {
    while v >= 0x80 {
        out.push((v as u8) | 0x80);
        v >>= 7;
    }
    out.push(v as u8);
}

fn get_varint(b: &[u8], mut p: usize) -> Option<(u64, usize)> {
    let mut s = 0u32;
    let mut v = 0u64;
    loop {
        let x = *b.get(p)?;
        p += 1;
        v |= ((x & 0x7f) as u64).checked_shl(s)? as u64;
        s = s.checked_add(7)?;
        if x & 0x80 == 0 {
            return Some((v, p));
        }
        if s > 70 {
            return None;
        }
    }
}

fn varint_len(mut v: u64) -> usize {
    let mut n = 1usize;
    while v >= 0x80 {
        v >>= 7;
        n += 1;
    }
    n
}

fn raw_fallback(input: &[u8]) -> Result<Vec<u8>, CodecError> {
    let mut c = zstd_compress(input, 22).map_err(|e| e.to_string())?;
    let mut o = Vec::with_capacity(c.len().checked_add(1).ok_or("len")?);
    o.push(TAG_RAW);
    o.append(&mut c);
    if o.len() > crate::MAX_PAYLOAD {
        return Err("payload".into());
    }
    Ok(o)
}

fn parse_grid(input: &[u8]) -> Option<(Vec<(u32, u32)>, Vec<u8>, usize)> {
    if input.is_empty() || input.contains(&0) {
        return None;
    }
    let mut lines: Vec<(u32, u32)> = Vec::new();
    let mut ends: Vec<u8> = Vec::new();
    let mut s = 0usize;
    let mut i = 0usize;
    while i < input.len() {
        if input[i] == b'\r' {
            let e = if i + 1 < input.len() && input[i + 1] == b'\n' {
                i += 2;
                1u8
            } else {
                i += 1;
                2u8
            };
            lines.push((s as u32, (if e == 1 { i - 2 } else { i - 1 }) as u32));
            ends.push(e);
            s = i;
        } else if input[i] == b'\n' {
            lines.push((s as u32, i as u32));
            ends.push(0u8);
            i += 1;
            s = i;
        } else {
            i += 1;
        }
    }
    if s < input.len() {
        lines.push((s as u32, input.len() as u32));
        ends.push(3u8);
    } else if s == input.len() && (lines.is_empty()) {
        return None;
    }
    if lines.len() < 2 {
        return None;
    }
    let mut ncols: Option<usize> = None;
    for (a, b) in lines.iter() {
        let mut n = 1usize;
        for k in (*a as usize)..(*b as usize) {
            if input[k] == b',' {
                n = n.checked_add(1)?;
            }
        }
        match ncols {
            None => ncols = Some(n),
            Some(c) if c != n => return None,
            Some(_) => {}
        }
    }
    let nc = ncols?;
    if nc < 2 || nc > 1024 || lines.len() > 200_000 {
        return None;
    }
    let toks = (lines.len() as u64).checked_mul(nc as u64)?;
    if toks > 4_000_000 {
        return None;
    }
    Some((lines, ends, nc))
}

fn token_spans(input: &[u8], lines: &[(u32, u32)], ncols: usize) -> Option<Vec<(u32, u32)>> {
    let n = lines.len().checked_mul(ncols)?;
    let mut t = Vec::with_capacity(n);
    for (a, b) in lines.iter() {
        let mut f = *a as usize;
        for _ in 0..ncols {
            let mut e = f;
            while e < (*b as usize) && input[e] != b',' {
                e += 1;
            }
            if e - f > 8192 {
                return None;
            }
            t.push((f as u32, e as u32));
            f = e + 1;
        }
    }
    Some(t)
}

fn build_meta(nrows: usize, ncols: usize, ends: &[u8]) -> Vec<u8> {
    let mut m = Vec::with_capacity(32 + ends.len());
    put_varint(&mut m, nrows as u64);
    put_varint(&mut m, ncols as u64);
    m.extend_from_slice(ends);
    m
}

fn build_mix(
    input: &[u8],
    toks: &[(u32, u32)],
    nrows: usize,
    ncols: usize,
    meta: &[u8],
) -> Option<Vec<u8>> {
    if ncols > 64 {
        return None;
    }
    let mode_len = ncols.checked_add(7)?.checked_div(8)?;
    let mut segs: Vec<Vec<u8>> = Vec::with_capacity(ncols);
    let mut modes = vec![0u8; mode_len];
    for c in 0..ncols {
        let mut lens = Vec::with_capacity(nrows);
        let mut mx = 0usize;
        let mut raw_len = 0usize;
        for r in 0..nrows {
            let (a, b) = toks[r * ncols + c];
            let l = (b - a) as usize;
            lens.push(l);
            if l > mx {
                mx = l;
            }
            raw_len = raw_len.checked_add(l.checked_add(1)?)?;
        }
        let mut raw_c = Vec::with_capacity(raw_len);
        for r in 0..nrows {
            let (a, b) = toks[r * ncols + c];
            raw_c.extend_from_slice(&input[a as usize..b as usize]);
            raw_c.push(0);
        }
        let mut plane_c = Vec::new();
        for &l in lens.iter() {
            put_varint(&mut plane_c, l as u64);
        }
        plane_c.try_reserve(mx.checked_mul(nrows)?).ok()?;
        for p in 0..mx {
            for r in 0..nrows {
                let (a, _) = toks[r * ncols + c];
                if p < lens[r] {
                    plane_c.push(input[a as usize + p]);
                }
            }
        }
        let zr = zstd_compress(&raw_c, 22).ok()?;
        let zp = zstd_compress(&plane_c, 22).ok()?;
        if zp.len() < zr.len() {
            modes[c / 8] |= 1u8 << (c % 8);
            segs.push(plane_c);
        } else {
            segs.push(raw_c);
        }
    }
    let mut t = meta.to_vec();
    t.extend_from_slice(&modes);
    for s in segs.iter() {
        t.extend_from_slice(s);
    }
    Some(t)
}

// Per-column raw-vs-dict mix. Dict body per dict column:
// varint ndist, then ndist*(varint len + bytes) in sorted bytewise order,
// then 1 byte kind (0 = plain ids, 1 = id/run pairs), then ids or runs.
fn build_dict(
    input: &[u8],
    toks: &[(u32, u32)],
    nrows: usize,
    ncols: usize,
    meta: &[u8],
) -> Option<Vec<u8>> {
    if ncols > 64 || nrows < 2 || nrows > 200_000 {
        return None;
    }
    let mode_len = ncols.checked_add(7)?.checked_div(8)?;
    let mut modes = vec![0u8; mode_len];
    let mut segs: Vec<Vec<u8>> = Vec::with_capacity(ncols);
    let mut any_dict = false;
    for c in 0..ncols {
        // Collect exact ids in row order; key is raw token bytes incl quotes.
        let mut map: HashMap<Vec<u8>, u32> = HashMap::with_capacity(nrows.min(8192));
        let mut ids: Vec<u32> = Vec::with_capacity(nrows);
        let mut distinct: Vec<Vec<u8>> = Vec::new();
        let mut raw_len = 0usize;
        let mut bad = false;
        for r in 0..nrows {
            let (a, b) = toks[r * ncols + c];
            let sl = &input[a as usize..b as usize];
            raw_len = match raw_len.checked_add(sl.len().checked_add(1)?) {
                Some(v) => v,
                None => {
                    bad = true;
                    break;
                }
            };
            if let Some(&id) = map.get(sl) {
                ids.push(id);
            } else {
                if distinct.len() >= nrows || distinct.len() >= 100_000 {
                    bad = true;
                    break;
                }
                let id = distinct.len() as u32;
                distinct.push(sl.to_vec());
                map.insert(sl.to_vec(), id);
                ids.push(id);
            }
        }
        if bad || distinct.len() == nrows || distinct.is_empty() {
            // No repeats: keep raw NUL-joined.
            let mut raw_c = Vec::with_capacity(raw_len);
            for r in 0..nrows {
                let (a, b) = toks[r * ncols + c];
                raw_c.extend_from_slice(&input[a as usize..b as usize]);
                raw_c.push(0);
            }
            segs.push(raw_c);
            continue;
        }
        let nd = distinct.len();
        // Sort literals bytewise to improve backend compression.
        let mut order: Vec<usize> = (0..nd).collect();
        order.sort_by(|&x, &y| distinct[x].cmp(&distinct[y]));
        let mut rank = vec![0u32; nd];
        for (new_id, &old) in order.iter().enumerate() {
            rank[old] = new_id as u32;
        }
        for v in ids.iter_mut() {
            *v = rank[*v as usize];
        }
        let mut lit_len = varint_len(nd as u64);
        for &o in order.iter() {
            lit_len = lit_len
                .checked_add(varint_len(distinct[o].len() as u64))?
                .checked_add(distinct[o].len())?;
        }
        // Plain-id cost vs run cost (raw varint sizes, cheap proxy).
        let mut plain_len = 0usize;
        for &id in ids.iter() {
            plain_len = plain_len.checked_add(varint_len(id as u64))?;
        }
        let mut runs: Vec<(u32, usize)> = Vec::new();
        let mut ri = 0usize;
        while ri < ids.len() {
            let id = ids[ri];
            let mut j = ri + 1;
            while j < ids.len() && ids[j] == id {
                j += 1;
            }
            runs.push((id, j - ri));
            ri = j;
        }
        let mut rle_len = varint_len(runs.len() as u64);
        for (id, rl) in runs.iter() {
            rle_len = rle_len
                .checked_add(varint_len(*id as u64))?
                .checked_add(varint_len((rl - 1) as u64))?;
        }
        let use_rle = rle_len < plain_len;
        let body_len = lit_len
            .checked_add(1)?
            .checked_add(if use_rle { rle_len } else { plain_len })?;
        if body_len >= raw_len {
            let mut raw_c = Vec::with_capacity(raw_len);
            for r in 0..nrows {
                let (a, b) = toks[r * ncols + c];
                raw_c.extend_from_slice(&input[a as usize..b as usize]);
                raw_c.push(0);
            }
            segs.push(raw_c);
            continue;
        }
        // Dict wins locally on raw bytes; global zstd decides finally.
        let mut d = Vec::with_capacity(body_len);
        put_varint(&mut d, nd as u64);
        for &o in order.iter() {
            put_varint(&mut d, distinct[o].len() as u64);
            d.extend_from_slice(&distinct[o]);
        }
        if use_rle {
            d.push(1u8);
            put_varint(&mut d, runs.len() as u64);
            for (id, rl) in runs.iter() {
                put_varint(&mut d, *id as u64);
                put_varint(&mut d, (rl - 1) as u64);
            }
        } else {
            d.push(0u8);
            for &id in ids.iter() {
                put_varint(&mut d, id as u64);
            }
        }
        modes[c / 8] |= 1u8 << (c % 8);
        segs.push(d);
        any_dict = true;
    }
    if !any_dict {
        return None;
    }
    let mut t = meta.to_vec();
    t.extend_from_slice(&modes);
    for s in segs.iter() {
        t.extend_from_slice(s);
    }
    Some(t)
}

// T5: per-column raw-vs-affix mix. For each column find the longest common
// byte prefix and longest common suffix (bounded by min token length so every
// middle is non-negative). Affix body: varint plen + prefix + varint slen +
// suffix + NUL-joined exact middles. Fixed-width quoted numerics share quotes
// and padding as affix; sparse exceptions stay in middles byte-for-byte.
fn build_affix(
    input: &[u8],
    toks: &[(u32, u32)],
    nrows: usize,
    ncols: usize,
    meta: &[u8],
) -> Option<Vec<u8>> {
    if ncols > 64 || nrows < 2 || nrows > 200_000 {
        return None;
    }
    let mode_len = ncols.checked_add(7)?.checked_div(8)?;
    let mut modes = vec![0u8; mode_len];
    let mut segs: Vec<Vec<u8>> = Vec::with_capacity(ncols);
    let mut any_affix = false;
    for c in 0..ncols {
        let mut min_len = usize::MAX;
        let mut raw_len = 0usize;
        for r in 0..nrows {
            let (a, b) = toks[r * ncols + c];
            let l = (b - a) as usize;
            if l < min_len {
                min_len = l;
            }
            raw_len = raw_len.checked_add(l.checked_add(1)?)?;
        }
        if min_len == usize::MAX {
            return None;
        }
        // Longest common prefix.
        let (fa, _) = toks[c];
        let mut plen = 0usize;
        'pre: for p in 0..min_len {
            let v = input[fa as usize + p];
            for r in 1..nrows {
                let (a, _) = toks[r * ncols + c];
                if input[a as usize + p] != v {
                    break 'pre;
                }
            }
            plen += 1;
        }
        // Longest common suffix on the remainder.
        let mut slen = 0usize;
        'suf: for s in 0..(min_len - plen) {
            let (_, b0) = toks[c];
            let v = input[b0 as usize - 1 - s];
            for r in 1..nrows {
                let (_, b) = toks[r * ncols + c];
                if input[b as usize - 1 - s] != v {
                    break 'suf;
                }
            }
            slen += 1;
        }
        if plen == 0 && slen == 0 {
            let mut raw_c = Vec::with_capacity(raw_len);
            for r in 0..nrows {
                let (a, b) = toks[r * ncols + c];
                raw_c.extend_from_slice(&input[a as usize..b as usize]);
                raw_c.push(0);
            }
            segs.push(raw_c);
            continue;
        }
        let save = (plen.checked_add(slen)?).checked_mul(nrows)?;
        let overhead = varint_len(plen as u64)
            .checked_add(plen)?
            .checked_add(varint_len(slen as u64))?
            .checked_add(slen)?;
        // Raw-size gate: affix must save at least its header.
        if save <= overhead {
            let mut raw_c = Vec::with_capacity(raw_len);
            for r in 0..nrows {
                let (a, b) = toks[r * ncols + c];
                raw_c.extend_from_slice(&input[a as usize..b as usize]);
                raw_c.push(0);
            }
            segs.push(raw_c);
            continue;
        }
        let body_len = raw_len.checked_sub(save)?.checked_add(overhead)?;
        let mut d = Vec::with_capacity(body_len);
        put_varint(&mut d, plen as u64);
        d.extend_from_slice(&input[fa as usize..fa as usize + plen]);
        put_varint(&mut d, slen as u64);
        if slen > 0 {
            let (_, fb) = toks[c];
            d.extend_from_slice(&input[fb as usize - slen..fb as usize]);
        }
        for r in 0..nrows {
            let (a, b) = toks[r * ncols + c];
            let s = a as usize + plen;
            let e = b as usize - slen;
            if s > e {
                // Should not happen (bounded by min_len); fall back to raw.
                let mut raw_c = Vec::with_capacity(raw_len);
                for rr in 0..nrows {
                    let (aa, bb) = toks[rr * ncols + c];
                    raw_c.extend_from_slice(&input[aa as usize..bb as usize]);
                    raw_c.push(0);
                }
                segs.push(raw_c);
                break;
            }
            d.extend_from_slice(&input[s..e]);
            d.push(0);
        }
        // Guard path above pushed raw and broke; skip pushing partial d.
        if segs.len() == c + 1 {
            continue;
        }
        // Normal path: d holds the affix body.
        if segs.len() == c {
            modes[c / 8] |= 1u8 << (c % 8);
            segs.push(d);
            any_affix = true;
        }
    }
    if !any_affix {
        return None;
    }
    let mut t = meta.to_vec();
    t.extend_from_slice(&modes);
    for s in segs.iter() {
        t.extend_from_slice(s);
    }
    Some(t)
}

impl Codec for Candidate {
    fn encode(input: &[u8], _model: &[u8]) -> Result<Vec<u8>, CodecError> {
        if input.len() > crate::MAX_INPUT {
            return Err("input bound".into());
        }
        if input.len() < 512 {
            return raw_fallback(input);
        }
        let raw = zstd_compress(input, 22).map_err(|e| e.to_string())?;
        let (lines, ends, ncols) = match parse_grid(input) {
            None => return raw_fallback(input),
            Some(g) => g,
        };
        let nrows = lines.len();
        let toks = match token_spans(input, &lines, ncols) {
            None => return raw_fallback(input),
            Some(t) => t,
        };
        let meta = build_meta(nrows, ncols, &ends);
        let mut t1 = meta.clone();
        t1.reserve(input.len().checked_add(nrows * ncols).ok_or("len")?);
        for c in 0..ncols {
            for r in 0..nrows {
                let (a, b) = toks[r * ncols + c];
                t1.extend_from_slice(&input[a as usize..b as usize]);
                t1.push(0);
            }
        }
        let mut t2 = meta.clone();
        for (a, b) in toks.iter() {
            put_varint(&mut t2, (b - a) as u64);
        }
        t2.reserve(input.len());
        for c in 0..ncols {
            let mut mx = 0usize;
            for r in 0..nrows {
                let (a, b) = toks[r * ncols + c];
                mx = mx.max((b - a) as usize);
            }
            for p in 0..mx {
                for r in 0..nrows {
                    let (a, b) = toks[r * ncols + c];
                    let len = (b - a) as usize;
                    if p < len {
                        t2.push(input[a as usize + p]);
                    }
                }
            }
        }
        let z1 = zstd_compress(&t1, 22).map_err(|e| e.to_string())?;
        let z2 = zstd_compress(&t2, 22).map_err(|e| e.to_string())?;
        let mut best_tag = TAG_RAW;
        let mut best = raw;
        if z1.len() < best.len() {
            best = z1;
            best_tag = TAG_COL;
        } else {
            let _ = z1;
        }
        if z2.len() < best.len() {
            best = z2;
            best_tag = TAG_PLANE;
        }
        if let Some(t3) = build_mix(input, &toks, nrows, ncols, &meta) {
            let z3 = zstd_compress(&t3, 22).map_err(|e| e.to_string())?;
            if z3.len() < best.len() {
                best = z3;
                best_tag = TAG_MIX;
            }
        }
        if let Some(t4) = build_dict(input, &toks, nrows, ncols, &meta) {
            let z4 = zstd_compress(&t4, 22).map_err(|e| e.to_string())?;
            if z4.len() < best.len() {
                best = z4;
                best_tag = TAG_DICT;
            }
        }
        if let Some(t5) = build_affix(input, &toks, nrows, ncols, &meta) {
            let z5 = zstd_compress(&t5, 22).map_err(|e| e.to_string())?;
            if z5.len() < best.len() {
                best = z5;
                best_tag = TAG_AFFIX;
            }
        }
        let mut o = Vec::with_capacity(best.len() + 1);
        o.push(best_tag);
        o.extend_from_slice(&best);
        if o.len() > crate::MAX_PAYLOAD {
            return Err("payload".into());
        }
        Ok(o)
    }

    fn decode(encoded: &[u8], _model: &[u8], original_len: usize) -> Result<Vec<u8>, CodecError> {
        if original_len > crate::MAX_INPUT {
            return Err("len".into());
        }
        let (&tag, blob) = encoded.split_first().ok_or("empty")?;
        if tag == TAG_RAW {
            let o = zstd_decompress(blob).map_err(|e| e.to_string())?;
            if o.len() != original_len {
                return Err("len mismatch".into());
            }
            return Ok(o);
        }
        if tag != TAG_COL
            && tag != TAG_PLANE
            && tag != TAG_MIX
            && tag != TAG_DICT
            && tag != TAG_AFFIX
        {
            return Err("tag".into());
        }
        let b = zstd_decompress(blob).map_err(|e| e.to_string())?;
        let (nr, mut p) = get_varint(&b, 0).ok_or("meta")?;
        let (nc, p2) = get_varint(&b, p).ok_or("meta")?;
        p = p2;
        if nr < 2 || nc < 2 || nr > 200_000 || nc > 1024 {
            return Err("dims".into());
        }
        let (nrows, ncols) = (nr as usize, nc as usize);
        let ntok = nrows.checked_mul(ncols).ok_or("dims")?;
        let pe = p.checked_add(nrows).ok_or("dims")?;
        if pe > b.len() {
            return Err("meta".into());
        }
        let ends = &b[p..pe];
        for e in ends.iter() {
            if *e > 3 {
                return Err("meta".into());
            }
        }
        p = pe;
        let mut col_tok: Vec<Vec<u8>> = Vec::new();
        col_tok.try_reserve(ntok).map_err(|_| "alloc")?;
        for _ in 0..ntok {
            col_tok.push(Vec::new());
        }
        if tag == TAG_COL {
            let mut k = p;
            for c in 0..ncols {
                for r in 0..nrows {
                    let s = k;
                    while k < b.len() && b[k] != 0 {
                        k += 1;
                    }
                    if k >= b.len() {
                        return Err("body".into());
                    }
                    col_tok[r * ncols + c].extend_from_slice(&b[s..k]);
                    k += 1;
                }
            }
            if k != b.len() {
                return Err("trailing".into());
            }
        } else if tag == TAG_PLANE {
            let mut lens = Vec::with_capacity(ntok);
            for _ in 0..ntok {
                let (v, np) = get_varint(&b, p).ok_or("lens")?;
                if v > 8192 {
                    return Err("lens".into());
                }
                lens.push(v as usize);
                p = np;
            }
            let mut k = p;
            for c in 0..ncols {
                let mut mx = 0usize;
                for r in 0..nrows {
                    mx = mx.max(lens[r * ncols + c]);
                }
                for r in 0..nrows {
                    col_tok[r * ncols + c]
                        .try_reserve(lens[r * ncols + c])
                        .map_err(|_| "alloc")?;
                }
                for pl in 0..mx {
                    for r in 0..nrows {
                        let idx = r * ncols + c;
                        if pl < lens[idx] {
                            if k >= b.len() {
                                return Err("body".into());
                            }
                            col_tok[idx].push(b[k]);
                            k += 1;
                        }
                    }
                }
            }
            if k != b.len() {
                return Err("trailing".into());
            }
        } else if tag == TAG_MIX {
            let mode_len = ncols.checked_add(7).ok_or("dims")?.checked_div(8).ok_or("dims")?;
            if p.checked_add(mode_len).ok_or("meta")? > b.len() {
                return Err("meta".into());
            }
            let modes = &b[p..p + mode_len];
            p += mode_len;
            let mut k = p;
            for c in 0..ncols {
                let plane = (modes[c / 8] >> (c % 8)) & 1 == 1;
                if !plane {
                    for r in 0..nrows {
                        let s = k;
                        while k < b.len() && b[k] != 0 {
                            k += 1;
                        }
                        if k >= b.len() {
                            return Err("body".into());
                        }
                        if k - s > 8192 {
                            return Err("lens".into());
                        }
                        col_tok[r * ncols + c].extend_from_slice(&b[s..k]);
                        k += 1;
                    }
                } else {
                    let mut lens = Vec::with_capacity(nrows);
                    let mut total = 0usize;
                    let mut mx = 0usize;
                    for _ in 0..nrows {
                        let (v, nk) = get_varint(&b, k).ok_or("lens")?;
                        if v > 8192 {
                            return Err("lens".into());
                        }
                        let l = v as usize;
                        total = total.checked_add(l).ok_or("lens")?;
                        mx = mx.max(l);
                        lens.push(l);
                        k = nk;
                    }
                    if k.checked_add(total).ok_or("lens")? > b.len() {
                        return Err("body".into());
                    }
                    for r in 0..nrows {
                        col_tok[r * ncols + c].try_reserve(lens[r]).map_err(|_| "alloc")?;
                    }
                    let body_start = k;
                    let mut bi = 0usize;
                    for pl in 0..mx {
                        for r in 0..nrows {
                            if pl < lens[r] {
                                col_tok[r * ncols + c].push(b[body_start + bi]);
                                bi += 1;
                            }
                        }
                    }
                    if bi != total {
                        return Err("body".into());
                    }
                    k += total;
                }
            }
            if k != b.len() {
                return Err("trailing".into());
            }
        } else if tag == TAG_DICT {
            let mode_len = ncols.checked_add(7).ok_or("dims")?.checked_div(8).ok_or("dims")?;
            if p.checked_add(mode_len).ok_or("meta")? > b.len() {
                return Err("meta".into());
            }
            let modes = b[p..p + mode_len].to_vec();
            p += mode_len;
            let mut k = p;
            for c in 0..ncols {
                let is_dict = (modes[c / 8] >> (c % 8)) & 1 == 1;
                if !is_dict {
                    for r in 0..nrows {
                        let s = k;
                        while k < b.len() && b[k] != 0 {
                            k += 1;
                        }
                        if k >= b.len() {
                            return Err("body".into());
                        }
                        if k - s > 8192 {
                            return Err("lens".into());
                        }
                        col_tok[r * ncols + c].extend_from_slice(&b[s..k]);
                        k += 1;
                    }
                } else {
                    let (ndv, np) = get_varint(&b, k).ok_or("lens")?;
                    if ndv == 0 || ndv > nrows as u64 || ndv > 100_000 {
                        return Err("lens".into());
                    }
                    let nd = ndv as usize;
                    k = np;
                    let mut lits: Vec<Vec<u8>> = Vec::with_capacity(nd);
                    let mut lit_total = 0usize;
                    for _ in 0..nd {
                        let (lv, nq) = get_varint(&b, k).ok_or("lens")?;
                        if lv > 8192 {
                            return Err("lens".into());
                        }
                        k = nq;
                        let l = lv as usize;
                        if k.checked_add(l).ok_or("lens")? > b.len() {
                            return Err("body".into());
                        }
                        lit_total = lit_total.checked_add(l).ok_or("lens")?;
                        if lit_total > crate::MAX_INPUT {
                            return Err("lens".into());
                        }
                        lits.push(b[k..k + l].to_vec());
                        k += l;
                    }
                    if k >= b.len() {
                        return Err("body".into());
                    }
                    let kind = b[k];
                    k += 1;
                    if kind == 0 {
                        for r in 0..nrows {
                            let (idv, nk) = get_varint(&b, k).ok_or("lens")?;
                            if idv >= nd as u64 {
                                return Err("lens".into());
                            }
                            k = nk;
                            col_tok[r * ncols + c].extend_from_slice(&lits[idv as usize]);
                        }
                    } else if kind == 1 {
                        let (nrv, nk) = get_varint(&b, k).ok_or("lens")?;
                        if nrv == 0 || nrv > nrows as u64 {
                            return Err("lens".into());
                        }
                        k = nk;
                        let mut done = 0usize;
                        for _ in 0..(nrv as usize) {
                            let (idv, n1) = get_varint(&b, k).ok_or("lens")?;
                            let (rlv, n2) = get_varint(&b, n1).ok_or("lens")?;
                            if idv >= nd as u64 {
                                return Err("lens".into());
                            }
                            let rl = (rlv as usize).checked_add(1).ok_or("lens")?;
                            if done.checked_add(rl).ok_or("lens")? > nrows {
                                return Err("lens".into());
                            }
                            k = n2;
                            for _ in 0..rl {
                                col_tok[(done) * ncols + c].extend_from_slice(&lits[idv as usize]);
                                done += 1;
                            }
                        }
                        if done != nrows {
                            return Err("body".into());
                        }
                    } else {
                        return Err("tag".into());
                    }
                }
            }
            if k != b.len() {
                return Err("trailing".into());
            }
        } else {
            // TAG_AFFIX: per-column raw or (plen + prefix + slen + suffix +
            // NUL-joined middles). Reconstruction is prefix+middle+suffix.
            let mode_len = ncols.checked_add(7).ok_or("dims")?.checked_div(8).ok_or("dims")?;
            if p.checked_add(mode_len).ok_or("meta")? > b.len() {
                return Err("meta".into());
            }
            let modes = b[p..p + mode_len].to_vec();
            p += mode_len;
            let mut k = p;
            for c in 0..ncols {
                let is_affix = (modes[c / 8] >> (c % 8)) & 1 == 1;
                if !is_affix {
                    for r in 0..nrows {
                        let s = k;
                        while k < b.len() && b[k] != 0 {
                            k += 1;
                        }
                        if k >= b.len() {
                            return Err("body".into());
                        }
                        if k - s > 8192 {
                            return Err("lens".into());
                        }
                        col_tok[r * ncols + c].extend_from_slice(&b[s..k]);
                        k += 1;
                    }
                } else {
                    let (plv, n1) = get_varint(&b, k).ok_or("lens")?;
                    if plv > 8192 {
                        return Err("lens".into());
                    }
                    k = n1;
                    let plen = plv as usize;
                    if k.checked_add(plen).ok_or("lens")? > b.len() {
                        return Err("body".into());
                    }
                    let prefix = b[k..k + plen].to_vec();
                    k += plen;
                    let (slv, n2) = get_varint(&b, k).ok_or("lens")?;
                    if slv > 8192 {
                        return Err("lens".into());
                    }
                    k = n2;
                    let slen = slv as usize;
                    if k.checked_add(slen).ok_or("lens")? > b.len() {
                        return Err("body".into());
                    }
                    let suffix = b[k..k + slen].to_vec();
                    k += slen;
                    if plen.checked_add(slen).ok_or("lens")? > 8192 {
                        return Err("lens".into());
                    }
                    for r in 0..nrows {
                        let s = k;
                        while k < b.len() && b[k] != 0 {
                            k += 1;
                        }
                        if k >= b.len() {
                            return Err("body".into());
                        }
                        let mlen = k - s;
                        if mlen.checked_add(plen).ok_or("lens")?.checked_add(slen).ok_or("lens")?
                            > 8192
                        {
                            return Err("lens".into());
                        }
                        col_tok[r * ncols + c].try_reserve(plen + mlen + slen).map_err(|_| "alloc")?;
                        col_tok[r * ncols + c].extend_from_slice(&prefix);
                        col_tok[r * ncols + c].extend_from_slice(&b[s..k]);
                        col_tok[r * ncols + c].extend_from_slice(&suffix);
                        k += 1;
                    }
                }
            }
            if k != b.len() {
                return Err("trailing".into());
            }
        }
        let mut out = Vec::new();
        out.try_reserve(original_len).map_err(|_| "alloc")?;
        for r in 0..nrows {
            for c in 0..ncols {
                if c > 0 {
                    out.push(b',');
                }
                out.extend_from_slice(&col_tok[r * ncols + c]);
            }
            match ends[r] {
                0 => out.push(b'\n'),
                1 => out.extend_from_slice(b"\r\n"),
                2 => out.push(b'\r'),
                _ => {}
            }
            if out.len() > crate::MAX_INPUT {
                return Err("len".into());
            }
        }
        if out.len() != original_len {
            return Err("len mismatch".into());
        }
        Ok(out)
    }
}
