use crate::{Codec, CodecError};
use crate::toolkit::{zstd_compress, zstd_decompress};

pub struct Candidate;

// Timestamp-dedup + raw-token column transpose before zstd-19.
// Layout tag 0: plain zstd fallback (generic bytes, NUL, malformed CSV, mixed EOL).
// Layout tag 1: header-separated column-major store.
//   col0 uses previous-token prefix/suffix delta (regular timestamp tokens,
//   exact spelling preserved, calendar transitions become small suffixes).
//   other unique columns stored as NUL-joined raw tokens (quotes/padding kept).
//   byte-identical columns stored as block-local dup references (e.g. rv1/rv2).
// All schema (ncols/nrows/header/eol/dup refs/prefix lens) lives in payload.
// Encoder compares both tags and records the smaller one.
// Limitations: single uniform EOL (LF or CRLF), consistent column counts,
// RFC-style double-quote splitting, no NUL bytes in transformed layout.

const TAG_RAW: u8 = 0;
const TAG_TD: u8 = 1;
const MAGIC: [u8; 4] = *b"TD01";

fn put_uv(out: &mut Vec<u8>, mut v: usize) {
    loop {
        let b = (v & 0x7f) as u8;
        v >>= 7;
        if v == 0 { out.push(b | 0x80); break; }
        out.push(b);
    }
}

fn get_uv(d: &[u8], pos: &mut usize) -> Option<usize> {
    let mut v: usize = 0;
    let mut sh: u32 = 0;
    loop {
        if *pos >= d.len() || sh > 28 { return None; }
        let b = d[*pos]; *pos += 1;
        if b & 0x80 != 0 { v |= ((b & 0x7f) as usize).checked_shl(sh)?; break; }
        v |= ((b & 0x7f) as usize).checked_shl(sh)?;
        sh += 7;
    }
    Some(v)
}

fn split_fields(line: &[u8]) -> Option<Vec<&[u8]>> {
    let mut f = Vec::new();
    let mut start = 0usize;
    let mut i = 0usize;
    let mut q = false;
    while i < line.len() {
        let c = line[i];
        if q {
            if c == b'"' {
                if i + 1 < line.len() && line[i + 1] == b'"' { i += 2; continue; }
                q = false; i += 1; continue;
            }
            i += 1; continue;
        } else if c == b'"' { q = true; i += 1; }
        else if c == b',' { f.push(&line[start..i]); start = i + 1; i += 1; }
        else { i += 1; }
    }
    if q { return None; }
    f.push(&line[start..]);
    Some(f)
}

fn prefix(a: &[u8], b: &[u8]) -> usize {
    let mut n = 0;
    while n < a.len() && n < b.len() && a[n] == b[n] { n += 1; }
    n
}

fn build_td(input: &[u8]) -> Option<Vec<u8>> {
    if input.is_empty() || input.contains(&0) { return None; }
    // Uniform EOL scan.
    let mut lines: Vec<&[u8]> = Vec::new();
    let mut eol_crlf: Option<bool> = None;
    let mut start = 0usize;
    let mut i = 0usize;
    while i < input.len() {
        if input[i] == b'\n' {
            let mut e = &input[start..i];
            let crlf = e.ends_with(b"\r");
            if crlf { e = &e[..e.len() - 1]; }
            if e.contains(&b'\r') { return None; }
            match eol_crlf { None => eol_crlf = Some(crlf), Some(x) if x != crlf => return None, _ => {} }
            lines.push(e);
            start = i + 1;
        } else if input[i] == b'\r' { return None; }
        i += 1;
    }
    let final_nl = start == input.len() && !input.is_empty();
    if start < input.len() {
        let e = &input[start..];
        if e.contains(&b'\r') { return None; }
        lines.push(e);
    } else if start > input.len() { return None; }
    if lines.len() < 2 { return None; }
    let crlf = eol_crlf.unwrap_or(false);
    // Split rows.
    let hdr = split_fields(lines[0])?;
    let ncols = hdr.len();
    if ncols < 2 || ncols > 256 { return None; }
    let nrows = lines.len() - 1;
    if nrows == 0 || nrows > 200_000 { return None; }
    let _tok = ncols.checked_mul(nrows)?;
    let mut cols: Vec<Vec<&[u8]>> = vec![Vec::new(); ncols];
    for r in 1..lines.len() {
        let f = split_fields(lines[r])?;
        if f.len() != ncols { return None; }
        for (j, t) in f.into_iter().enumerate() { cols[j].push(t); }
    }
    // Dup detection (byte-identical raw tokens).
    let mut is_dup = vec![false; ncols];
    let mut dup_ref = vec![0usize; ncols];
    for j in 1..ncols {
        for k in 0..j {
            if is_dup[k] { continue; }
            if cols[j] == cols[k] { is_dup[j] = true; dup_ref[j] = k; break; }
        }
    }
    let mut inner: Vec<u8> = Vec::new();
    inner.extend_from_slice(&MAGIC);
    put_uv(&mut inner, ncols);
    put_uv(&mut inner, nrows);
    put_uv(&mut inner, lines[0].len());
    inner.extend_from_slice(lines[0]);
    inner.push(crlf as u8);
    inner.push(final_nl as u8);
    for j in 0..ncols {
        if is_dup[j] { inner.push(1); put_uv(&mut inner, dup_ref[j]); continue; }
        inner.push(0);
        if j == 0 {
            put_uv(&mut inner, cols[0][0].len());
            inner.extend_from_slice(cols[0][0]);
            for r in 1..nrows {
                let p = prefix(cols[0][r - 1], cols[0][r]);
                put_uv(&mut inner, p);
                put_uv(&mut inner, cols[0][r].len() - p);
                inner.extend_from_slice(&cols[0][r][p..]);
            }
        } else {
            let mut blen: usize = 0;
            for t in &cols[j] { blen = blen.checked_add(t.len())?.checked_add(1)?; }
            blen = blen.checked_sub(1)?;
            put_uv(&mut inner, blen);
            for (k, t) in cols[j].iter().enumerate() {
                if k > 0 { inner.push(0); }
                inner.extend_from_slice(t);
            }
        }
        if inner.len() > crate::MAX_INPUT * 2 { return None; }
    }
    Some(inner)
}

fn decode_td(d: &[u8]) -> Result<Vec<u8>, CodecError> {
    let mut p = 0usize;
    if d.len() < 4 || d[0..4] != MAGIC { return Err("bad magic".into()); }
    p = 4;
    let ncols = get_uv(d, &mut p).ok_or("bad ncols")?;
    let nrows = get_uv(d, &mut p).ok_or("bad nrows")?;
    if ncols < 2 || ncols > 256 || nrows == 0 || nrows > 200_000 { return Err("bad dims".into()); }
    ncols.checked_mul(nrows).ok_or("bad dims")?;
    let hlen = get_uv(d, &mut p).ok_or("bad header")?;
    if hlen > crate::MAX_INPUT || p.checked_add(hlen).is_none() || p + hlen > d.len() { return Err("bad header".into()); }
    let header = &d[p..p + hlen]; p += hlen;
    if p + 2 > d.len() { return Err("bad eol".into()); }
    let crlf = d[p] != 0; p += 1;
    let final_nl = d[p] != 0; p += 1;
    if (crlf as u8) > 1 || (final_nl as u8) > 1 { return Err("bad eol".into()); }
    let mut cols: Vec<Vec<Vec<u8>>> = vec![Vec::new(); ncols];
    for j in 0..ncols {
        if p >= d.len() { return Err("bad col".into()); }
        let flag = d[p]; p += 1;
        if flag == 1 {
            let r = get_uv(d, &mut p).ok_or("bad ref")?;
            if r >= j || cols[r].len() != nrows { return Err("bad ref".into()); }
            cols[j] = cols[r].clone();
        } else if flag == 0 {
            if j == 0 {
                let fl = get_uv(d, &mut p).ok_or("bad ts")?;
                if fl > crate::MAX_INPUT || p + fl > d.len() { return Err("bad ts".into()); }
                let mut prev = d[p..p + fl].to_vec(); p += fl;
                cols[0].push(prev.clone());
                for _ in 1..nrows {
                    let pre = get_uv(d, &mut p).ok_or("bad pre")?;
                    let sl = get_uv(d, &mut p).ok_or("bad suf")?;
                    if pre > prev.len() || sl > crate::MAX_INPUT || p + sl > d.len() { return Err("bad suf".into()); }
                    let mut cur = Vec::with_capacity(pre + sl);
                    cur.extend_from_slice(&prev[..pre]);
                    cur.extend_from_slice(&d[p..p + sl]); p += sl;
                    prev = cur.clone();
                    cols[0].push(cur);
                }
            } else {
                let bl = get_uv(d, &mut p).ok_or("bad blob")?;
                if bl > crate::MAX_INPUT * 2 || p + bl > d.len() { return Err("bad blob".into()); }
                let blob = &d[p..p + bl]; p += bl;
                let mut toks: Vec<Vec<u8>> = Vec::new();
                let mut s = 0usize;
                for _ in 0..nrows {
                    match blob[s..].iter().position(|&b| b == 0) {
                        Some(k) if toks.len() + 1 < nrows => { toks.push(blob[s..s + k].to_vec()); s += k + 1; }
                        _ => { toks.push(blob[s..].to_vec()); s = bl; break; }
                    }
                }
                if toks.len() != nrows || s != bl { return Err("bad blob split".into()); }
                cols[j] = toks;
            }
        } else { return Err("bad flag".into()); }
    }
    let eol: &[u8] = if crlf { b"\r\n" } else { b"\n" };
    let mut out: Vec<u8> = Vec::new();
    out.extend_from_slice(header);
    out.extend_from_slice(eol);
    for r in 0..nrows {
        for j in 0..ncols {
            if j > 0 { out.push(b','); }
            out.extend_from_slice(&cols[j][r]);
            if out.len() > crate::MAX_INPUT { return Err("too big".into()); }
        }
        if r + 1 < nrows || final_nl { out.extend_from_slice(eol); }
    }
    Ok(out)
}

impl Codec for Candidate {
    fn train(_s: &[&[u8]]) -> Result<Vec<u8>, CodecError> { Ok(Vec::new()) }

    fn encode(input: &[u8], _m: &[u8]) -> Result<Vec<u8>, CodecError> {
        if input.len() > crate::MAX_INPUT { return Err("input too big".into()); }
        let z0 = zstd_compress(input, 19).map_err(|e| e.to_string())?;
        let mut best = Vec::with_capacity(z0.len() + 1);
        best.push(TAG_RAW);
        best.extend_from_slice(&z0);
        if let Some(inner) = build_td(input) {
            let z1 = zstd_compress(&inner, 19).map_err(|e| e.to_string())?;
            if z1.len().checked_add(1).is_some() && z1.len() + 1 < best.len() {
                best.clear();
                best.push(TAG_TD);
                best.extend_from_slice(&z1);
            }
        }
        if best.len() > crate::MAX_PAYLOAD { return Err("payload too big".into()); }
        Ok(best)
    }

    fn decode(enc: &[u8], _m: &[u8], original_len: usize) -> Result<Vec<u8>, CodecError> {
        if original_len > crate::MAX_INPUT { return Err("bad len".into()); }
        if enc.is_empty() { return Err("empty".into()); }
        let out = match enc[0] {
            TAG_RAW => zstd_decompress(&enc[1..]).map_err(|e| e.to_string())?,
            TAG_TD => {
                let d = zstd_decompress(&enc[1..]).map_err(|e| e.to_string())?;
                decode_td(&d)?
            }
            _ => return Err("bad tag".into()),
        };
        if out.len() != original_len { return Err("length mismatch".into()); }
        Ok(out)
    }
}
