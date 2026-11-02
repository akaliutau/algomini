// Stage 4 causal ablation: disable only mixed-column selection.
// Per-column raw-vs-plane choice over whole-block fallback.
// T1 = whole-block NUL column streams; T2 = whole-block byte planes with
// lengths. New T3 (TAG_MIX) picks per column c either raw NUL-joined tokens
// or position-major planes + varint lengths, using per-column actual zstd
// bytes (lengths counted) for the local choice, then compares actual
// compressed bytes of raw/T1/T2/T3 globally. No numeric conversion; all
// quotes, padding, exponents, whitespace and line endings round-trip exactly.
use crate::{Codec, CodecError};
use crate::toolkit::{zstd_compress, zstd_decompress};

pub struct Candidate;

const TAG_RAW: u8 = 0;
const TAG_COL: u8 = 1;
const TAG_PLANE: u8 = 2;
const TAG_MIX: u8 = 3;

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

// Build per-column mixed buffer: meta + mode bits + per-column segments.
// Each segment is raw NUL-joined (mode 0) or varint lens + plane body
// (mode 1). Local mode picked by actual per-column zstd bytes (lens counted),
// never by raw length alone. Returns None to skip MIX when gated/failing.
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
        // Counted local choice: actual compressed bytes of each option.
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
        // Global counted choice over actual compressed bytes; MIX only wins
        // if strictly smallest, so whole-block fallbacks stay strong.
        let mut best_tag = TAG_RAW;
        let mut best = raw;
        if z1.len() < best.len() {
            best = z1;
            best_tag = TAG_COL;
        } else {
            let _ = z1;
        }
        // Keep z2 if it is best so far; careful with move of z1 above.
        // Re-fetch logic: best holds owned vec; compare z2 against it.
        if z2.len() < best.len() {
            best = z2;
            best_tag = TAG_PLANE;
        }
        if let Some(t3) = build_mix(input, &toks, nrows, ncols, &meta) {
            let z3 = zstd_compress(&t3, 22).map_err(|e| e.to_string())?;
            if false && z3.len() < best.len() {
                best = z3;
                best_tag = TAG_MIX;
            }
        }
        if best_tag == TAG_RAW {
            let mut o = Vec::with_capacity(best.len() + 1);
            o.push(TAG_RAW);
            o.extend_from_slice(&best);
            if o.len() > crate::MAX_PAYLOAD {
                return Err("payload".into());
            }
            return Ok(o);
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
        if tag != TAG_COL && tag != TAG_PLANE && tag != TAG_MIX {
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
        } else {
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
                    if total > k.checked_sub(0).ok_or("lens")? && k.checked_add(total).ok_or("lens")? > b.len() {
                        return Err("body".into());
                    }
                    if k.checked_add(total).ok_or("lens")? > b.len() {
                        return Err("body".into());
                    }
                    for r in 0..nrows {
                        col_tok[r * ncols + c].try_reserve(lens[r]).map_err(|_| "alloc")?;
                    }
                    let body_start = k;
                    // Distribute in exact encode order: pos-major, row-minor.
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
