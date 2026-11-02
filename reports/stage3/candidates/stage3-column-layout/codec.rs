use crate::{Codec, CodecError};
use crate::toolkit::{zstd_compress, zstd_decompress};

pub struct Candidate;

// Raw-token column transpose with NUL separators + block-local dup refs.
// Transform (pre-zstd): [nrows][ncols][endings nrows bytes][colflags ncols bytes]
// [ref varint per dup col][bloblen varint + blob per stored col].
// Endings: 0=none(last only),1=LF,2=CRLF. Colflag: 0=stored,1=dup of earlier.
// Each stored blob = cells joined by single NUL (no trailing NUL).
// Outer: [tag 0=plain,1=column][zstd(inner)]. Encode picks smaller payload.

fn wv(mut v: usize, o: &mut Vec<u8>) {
    loop {
        let b = (v & 0x7f) as u8;
        v >>= 7;
        if v == 0 { o.push(b); break; } else { o.push(b | 0x80); }
    }
}
fn rv(d: &[u8], p: &mut usize) -> Option<usize> {
    let mut v = 0usize;
    let mut s = 0u32;
    loop {
        if *p >= d.len() || s > 28 { return None; }
        let b = d[*p]; *p += 1;
        let lo = (b & 0x7f) as usize;
        v |= lo.checked_shl(s)?; s += 7;
        if b & 0x80 == 0 { return Some(v); }
    }
}
fn split_lines(inp: &[u8]) -> Option<(Vec<&[u8]>, Vec<u8>)> {
    if inp.is_empty() || inp.contains(&0) { return None; }
    let mut rows: Vec<&[u8]> = Vec::new();
    let mut ends: Vec<u8> = Vec::new();
    let mut s = 0usize;
    for (i, &b) in inp.iter().enumerate() {
        if b == b'\n' {
            let mut e = &inp[s..i];
            let k = if !e.is_empty() && e[e.len() - 1] == b'\r' { e = &e[..e.len() - 1]; 2 } else { 1 };
            rows.push(e); ends.push(k); s = i + 1;
        }
    }
    if s < inp.len() { rows.push(&inp[s..]); ends.push(0); }
    else if s == inp.len() && inp.last() == Some(&b'\n') { /* trailing nl consumed */ }
    else { return None; }
    if rows.len() < 2 { return None; }
    Some((rows, ends))
}
fn build(inp: &[u8]) -> Option<Vec<u8>> {
    let (rows, ends) = split_lines(inp)?;
    let nr = rows.len();
    if nr > 100_000 { return None; }
    // split on commas; require consistent ncols
    let mut grid: Vec<Vec<&[u8]>> = Vec::with_capacity(nr);
    let mut nc: Option<usize> = None;
    for r in &rows {
        let mut cells: Vec<&[u8]> = Vec::new();
        let mut st = 0usize;
        for (i, &b) in r.iter().enumerate() {
            if b == b',' { cells.push(&r[st..i]); st = i + 1; }
        }
        cells.push(&r[st..]);
        if cells.len() < 2 { return None; }
        if cells.len() > 4096 { return None; }
        match nc { None => nc = Some(cells.len()), Some(n) if n != cells.len() => return None, _ => {} }
        grid.push(cells);
    }
    let nc = nc?;
    let ncells = nr.checked_mul(nc)?;
    if ncells > 8_000_000 { return None; }
    // dup refs: identical raw cells to earlier column
    let mut is_dup = vec![false; nc];
    let mut refc = vec![0usize; nc];
    let do_dup = nc <= 512 && nr <= 20_000;
    if do_dup {
        for j in 0..nc {
            for k in 0..j {
                let mut eq = true;
                for i in 0..nr {
                    if grid[i][j] != grid[i][k] { eq = false; break; }
                }
                if eq { is_dup[j] = true; refc[j] = k; break; }
            }
        }
    }
    let mut out = Vec::with_capacity(inp.len() + 64);
    wv(nr, &mut out); wv(nc, &mut out);
    out.extend_from_slice(&ends);
    for j in 0..nc { out.push(if is_dup[j] { 1 } else { 0 }); }
    for j in 0..nc { if is_dup[j] { wv(refc[j], &mut out); } }
    for j in 0..nc {
        if is_dup[j] { continue; }
        let mut blen = 0usize;
        for i in 0..nr {
            blen = blen.checked_add(grid[i][j].len())?;
            if i + 1 < nr { blen = blen.checked_add(1)?; }
        }
        if blen > crate::MAX_INPUT * 2 { return None; }
        wv(blen, &mut out);
        for i in 0..nr {
            out.extend_from_slice(grid[i][j]);
            if i + 1 < nr { out.push(0); }
        }
    }
    Some(out)
}
fn invert(buf: &[u8], olen: usize) -> Result<Vec<u8>, CodecError> {
    let mut p = 0usize;
    let nr = rv(buf, &mut p).ok_or("bad meta".to_string())?;
    let nc = rv(buf, &mut p).ok_or("bad meta".to_string())?;
    if nr == 0 || nr > 100_000 || nc == 0 || nc > 4096 { return Err("bad dims".to_string()); }
    let ncells = nr.checked_mul(nc).ok_or("bad dims".to_string())?;
    if ncells > 8_000_000 { return Err("bad dims".to_string()); }
    if p.checked_add(nr).ok_or("bad meta".to_string())? > buf.len() { return Err("bad ends".to_string()); }
    let ends = &buf[p..p + nr]; p += nr;
    for (i, &e) in ends.iter().enumerate() {
        if e > 2 { return Err("bad ends".to_string()); }
        if e == 0 && i + 1 != nr { return Err("bad ends".to_string()); }
    }
    if p.checked_add(nc).ok_or("bad meta".to_string())? > buf.len() { return Err("bad flags".to_string()); }
    let flags = &buf[p..p + nc]; p += nc;
    let mut refs: Vec<Option<usize>> = vec![None; nc];
    for (j, &f) in flags.iter().enumerate() {
        if f > 1 { return Err("bad flags".to_string()); }
        if f == 1 {
            let r = rv(buf, &mut p).ok_or("bad ref".to_string())?;
            if r >= j { return Err("bad ref".to_string()); }
            if flags[r] == 1 { return Err("bad ref".to_string()); }
            refs[j] = Some(r);
        }
    }
    // load stored blobs -> cells
    let mut cols: Vec<Vec<&[u8]>> = vec![Vec::new(); nc];
    for j in 0..nc {
        if refs[j].is_some() { continue; }
        let bl = rv(buf, &mut p).ok_or("bad blob".to_string())?;
        if bl > crate::MAX_INPUT * 2 { return Err("bad blob".to_string()); }
        if p.checked_add(bl).ok_or("bad blob".to_string())? > buf.len() { return Err("bad blob".to_string()); }
        let blob = &buf[p..p + bl]; p += bl;
        let mut cells: Vec<&[u8]> = Vec::with_capacity(nr);
        if nr == 1 {
            if blob.contains(&0) { return Err("bad blob".to_string()); }
            cells.push(blob);
        } else {
            let mut st = 0usize; let mut n = 0usize;
            for (i, &b) in blob.iter().enumerate() {
                if b == 0 { cells.push(&blob[st..i]); st = i + 1; n += 1; }
            }
            cells.push(&blob[st..]);
            if n + 1 != nr { return Err("bad blob".to_string()); }
        }
        cols[j] = cells;
    }
    if p != buf.len() { return Err("trailing meta".to_string()); }
    for j in 0..nc {
        if let Some(r) = refs[j] {
            // clone cell refs (raw bytes shared from buf)
            let tmp = cols[r].clone();
            cols[j] = tmp;
        }
    }
    if olen > crate::MAX_INPUT { return Err("bad len".to_string()); }
    let mut out = Vec::with_capacity(olen);
    for i in 0..nr {
        for j in 0..nc {
            out.extend_from_slice(cols[j][i]);
            if j + 1 < nc { out.push(b','); }
        }
        match ends[i] { 1 => out.push(b'\n'), 2 => { out.extend_from_slice(b"\r\n"); } _ => {} }
    }
    if out.len() != olen { return Err("len mismatch".to_string()); }
    Ok(out)
}
fn plain(inp: &[u8]) -> Result<Vec<u8>, CodecError> {
    let c = zstd_compress(inp, 9).map_err(|e| e.to_string())?;
    let mut o = Vec::with_capacity(c.len() + 1);
    o.push(0u8); o.extend_from_slice(&c);
    if o.len() > crate::MAX_PAYLOAD { return Err("payload too large".to_string()); }
    Ok(o)
}
impl Codec for Candidate {
    fn encode(input: &[u8], _m: &[u8]) -> Result<Vec<u8>, CodecError> {
        if input.len() > crate::MAX_INPUT { return Err("input too large".to_string()); }
        let base = zstd_compress(input, 9).map_err(|e| e.to_string())?;
        let mut best = Vec::with_capacity(base.len() + 1);
        best.push(0u8); best.extend_from_slice(&base);
        if let Some(t) = build(input) {
            let c = zstd_compress(&t, 9).map_err(|e| e.to_string())?;
            if c.len().checked_add(1).ok_or("too large".to_string())? < best.len() {
                best.clear(); best.push(1u8); best.extend_from_slice(&c);
            }
        }
        if best.len() > crate::MAX_PAYLOAD { return Err("payload too large".to_string()); }
        Ok(best)
    }
    fn decode(enc: &[u8], _m: &[u8], ol: usize) -> Result<Vec<u8>, CodecError> {
        if ol > crate::MAX_INPUT { return Err("bad len".to_string()); }
        if enc.is_empty() { return Err("empty".to_string()); }
        let (tag, c) = (enc[0], &enc[1..]);
        let raw = zstd_decompress(c).map_err(|e| e.to_string())?;
        match tag {
            0 => { if raw.len() != ol { return Err("len mismatch".to_string()); } Ok(raw) }
            1 => invert(&raw, ol),
            _ => Err("bad tag".to_string()),
        }
    }
}
