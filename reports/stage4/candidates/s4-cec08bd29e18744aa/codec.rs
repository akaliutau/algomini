// Stage 4 controlled variant: {"author": "deterministic host variant of exact Muse source", "mode": "column", "parent_sha256": "3d0f93fed0f457b33097f605c80dd314fbdb6c87bc2312fc4fca5acb9e3a499c", "zstd_level": 19}
use crate::{Codec, CodecError};
use crate::toolkit::{zstd_compress, zstd_decompress};

// Byteplane / position-transpose experiment for quoted numeric CSV lexemes.
// Transform: split raw bytes into lines (LF/CRLF/CR/final-none preserved per
// line) and naive comma columns (quotes/padding/spelling kept byte-exact in
// tokens). Candidate T1 concatenates each column row-major with NUL
// separators; candidate T2 ("byteplanes") groups each column by character
// position p (all p-th bytes across rows) plus exact token lengths, so fixed-
// width decimals / exponents align. Everything is compared against plain zstd
// at level 19 in encode and the smallest wins (tagged fallback). No numeric
// conversion, rounding, or quote normalization ever happens.
// Limitations: only rectangular comma layouts without NUL bytes are
// transformed; JSON, single-column, ragged/malformed CSV, embedded NUL, and
// tiny inputs use the tagged raw-zstd fallback. Quoted commas/newlines are
// treated as delimiters (still exactly invertible via stored dimensions) so
// compression may be poor there but bytes round-trip.

pub struct Candidate;

const TAG_RAW: u8 = 0;
const TAG_COL: u8 = 1;
const TAG_PLANE: u8 = 2;

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
    let mut c = zstd_compress(input, 19).map_err(|e| e.to_string())?;
    let mut o = Vec::with_capacity(c.len().checked_add(1).ok_or("len")?);
    o.push(TAG_RAW);
    o.append(&mut c);
    if o.len() > crate::MAX_PAYLOAD {
        return Err("payload".into());
    }
    Ok(o)
}

// Parse lines + rectangular comma grid. Returns (line_contents, endings, ncols).
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

impl Codec for Candidate {
    fn encode(input: &[u8], _model: &[u8]) -> Result<Vec<u8>, CodecError> {
        if input.len() > crate::MAX_INPUT {
            return Err("input bound".into());
        }
        if input.len() < 512 {
            return raw_fallback(input);
        }
        let raw = zstd_compress(input, 19).map_err(|e| e.to_string())?;
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
        // T1: column-major NUL-joined tokens.
        let mut t1 = meta.clone();
        t1.reserve(input.len().checked_add(nrows * ncols).ok_or("len")?);
        for c in 0..ncols {
            for r in 0..nrows {
                let (a, b) = toks[r * ncols + c];
                t1.extend_from_slice(&input[a as usize..b as usize]);
                t1.push(0);
            }
        }
        // T2: lengths + position-major byteplanes per column.
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
        let z1 = zstd_compress(&t1, 19).map_err(|e| e.to_string())?;
        let z2 = zstd_compress(&t2, 19).map_err(|e| e.to_string())?;
        let (tag, zb) = if z1.len() < raw.len() {
            (TAG_COL, z1)
        } else if false {
            (TAG_PLANE, z2)
        } else {
            let mut o = Vec::with_capacity(raw.len() + 1);
            o.push(TAG_RAW);
            o.extend_from_slice(&raw);
            if o.len() > crate::MAX_PAYLOAD {
                return Err("payload".into());
            }
            return Ok(o);
        };
        let mut o = Vec::with_capacity(zb.len() + 1);
        o.push(tag);
        o.extend_from_slice(&zb);
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
        if tag != TAG_COL && tag != TAG_PLANE {
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
        // Recover token bytes.
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
        } else {
            let mut lens = Vec::with_capacity(ntok);
            for _ in 0..ntok {
                let (v, np) = get_varint(&b, p).ok_or("lens")?;
                if v > 8192 {
                    return Err("lens".into());
                }
                lens.push(v as usize);
                p = np;
            }
            // Distribute position-major planes back to tokens in exact
            // encode order: per column, per position p, per row (if present).
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
        }
        // Reassemble rows with commas + recorded endings.
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
