//! Temporal-prefix telemetry codec: per-column longest-common-prefix + suffix.
//! Transform: split lines preserving LF/CRLF/none endings, split each line on
//! raw commas (byte-exact, no CSV semantics), transpose to columns, then per
//! column encode each token as (lcp-with-prev, suffix-len) plus suffix bytes.
//! Metadata varints live in one stream, suffix bytes in another, so tiny
//! prefix values do not fragment lexeme runs before zstd. First token per
//! column is a literal (prefix 0). Equal neighbours become (len, 0) with no
//! suffix bytes, giving equality runs for free. Layout detection is strict:
//! >1 row, >1 column, identical column counts; anything else (empty, NUL,
//! JSON, malformed CSV, single row) uses tagged plain-zstd fallback (tag 0).
//! Limitations: naive comma split misaligns columns when quoted fields hold
//! commas/newlines (still exactly reversible); prefix pass only helps when
//! successive tokens in a column share bytes; otherwise encode picks the
//! smaller of raw-transpose (tag 1) / prefix (tag 2) / fallback (tag 0).
use crate::toolkit::{zstd_compress, zstd_decompress};
use crate::{Codec, CodecError, MAX_INPUT, MAX_PAYLOAD};

pub struct Candidate;

fn put_vi(out: &mut Vec<u8>, mut v: u32) {
    loop {
        let b = (v & 0x7f) as u8;
        v >>= 7;
        if v == 0 {
            out.push(b);
            return;
        }
        out.push(b | 0x80);
    }
}

fn get_vi(d: &[u8], p: &mut usize) -> Option<u32> {
    let mut v: u32 = 0;
    let mut s = 0;
    for _ in 0..5 {
        if *p >= d.len() {
            return None;
        }
        let b = d[*p];
        *p += 1;
        v |= ((b & 0x7f) as u32).checked_shl(s)?;
        s += 7;
        if b & 0x80 == 0 {
            return Some(v);
        }
    }
    None
}

fn lcp(a: &[u8], b: &[u8]) -> usize {
    let mut n = 0;
    let m = a.len().min(b.len());
    while n < m && a[n] == b[n] {
        n += 1;
    }
    n
}

// Split input into (content without line ending, ending code 0/1/2).
fn split_lines(inp: &[u8]) -> Option<(Vec<&[u8]>, Vec<u8>)> {
    if inp.is_empty() {
        return Some((Vec::new(), Vec::new()));
    }
    let mut cs = Vec::new();
    let mut es = Vec::new();
    let mut s = 0;
    for (i, &b) in inp.iter().enumerate() {
        if b == b'\n' {
            let mut c = &inp[s..i];
            let e = if c.last() == Some(&b'\r') {
                c = &c[..c.len() - 1];
                2u8
            } else {
                1u8
            };
            cs.push(c);
            es.push(e);
            s = i + 1;
        }
    }
    if s < inp.len() {
        cs.push(&inp[s..]);
        es.push(0);
    } else if s == inp.len() {
        // trailing newline already recorded; nothing more
    }
    if cs.len() > 500_000 {
        return None;
    }
    Some((cs, es))
}

// Build column table with raw-comma split. Returns (cols, table[col][row] slices).
fn table<'a>(cs: &[&'a [u8]]) -> Option<(usize, Vec<Vec<&'a [u8]>>)> {
    if cs.len() < 2 || cs.len() > 200_000 {
        return None;
    }
    let mut cols: Option<usize> = None;
    let mut tab: Vec<Vec<&[u8]>> = Vec::new();
    for c in cs {
        let mut toks: Vec<&[u8]> = Vec::new();
        let mut s = 0;
        for (i, &b) in c.iter().enumerate() {
            if b == b',' {
                toks.push(&c[s..i]);
                s = i + 1;
            }
        }
        toks.push(&c[s..]);
        if toks.len() < 2 || toks.len() > 2048 {
            return None;
        }
        if let Some(k) = cols {
            if k != toks.len() {
                return None;
            }
        } else {
            cols = Some(toks.len());
        }
        // grow column-major table lazily
        if tab.is_empty() {
            tab = vec![Vec::with_capacity(cs.len()); toks.len()];
        }
        for (ci, t) in toks.into_iter().enumerate() {
            tab[ci].push(t);
        }
    }
    let k = cols?;
    // checked total cells before further allocation
    let _ = k.checked_mul(cs.len())?;
    Some((k, tab))
}

fn build_transpose(cols: usize, rows: usize, ends: &[u8], tab: &[Vec<&[u8]>]) -> Option<Vec<u8>> {
    let mut p = Vec::new();
    put_vi(&mut p, cols as u32);
    put_vi(&mut p, rows as u32);
    p.extend_from_slice(ends);
    for c in tab {
        for t in c {
            put_vi(&mut p, t.len() as u32);
            p.extend_from_slice(t);
        }
    }
    if p.len() > MAX_PAYLOAD {
        return None;
    }
    Some(p)
}

fn build_prefix(cols: usize, rows: usize, ends: &[u8], tab: &[Vec<&[u8]>]) -> Option<Vec<u8>> {
    let mut meta = Vec::new();
    let mut suf = Vec::new();
    for c in tab {
        let mut prev: &[u8] = &[];
        for (r, t) in c.iter().enumerate() {
            let (pl, sb) = if r == 0 {
                (0, *t)
            } else {
                let n = lcp(prev, t);
                (n, &t[n..])
            };
            put_vi(&mut meta, pl as u32);
            put_vi(&mut meta, sb.len() as u32);
            suf.extend_from_slice(sb);
            prev = t;
        }
    }
    let mut p = Vec::new();
    put_vi(&mut p, cols as u32);
    put_vi(&mut p, rows as u32);
    p.extend_from_slice(ends);
    put_vi(&mut p, meta.len() as u32);
    p.extend_from_slice(&meta);
    p.extend_from_slice(&suf);
    if p.len() > MAX_PAYLOAD {
        return None;
    }
    Some(p)
}

fn rebuild_transpose(d: &[u8]) -> Result<Vec<u8>, CodecError> {
    let mut p = 0;
    let cols = get_vi(d, &mut p).ok_or("bad header")? as usize;
    let rows = get_vi(d, &mut p).ok_or("bad header")? as usize;
    if cols < 2 || cols > 2048 || rows < 2 || rows > 200_000 {
        return Err("bad dims".into());
    }
    let cells = cols.checked_mul(rows).ok_or("bad dims")?;
    if cells > 20_000_000 {
        return Err("too many cells".into());
    }
    if p.checked_add(rows).ok_or("bad ends")? > d.len() {
        return Err("bad ends".into());
    }
    let ends = d[p..p + rows].to_vec();
    p += rows;
    let mut tab: Vec<Vec<Vec<u8>>> = vec![Vec::new(); cols];
    for c in 0..cols {
        for _ in 0..rows {
            let n = get_vi(d, &mut p).ok_or("bad tok")? as usize;
            if n > MAX_INPUT {
                return Err("tok too big".into());
            }
            if p.checked_add(n).ok_or("bad tok")? > d.len() {
                return Err("truncated".into());
            }
            tab[c].push(d[p..p + n].to_vec());
            p += n;
        }
    }
    assemble(&tab, &ends, cols, rows)
}

fn rebuild_prefix(d: &[u8]) -> Result<Vec<u8>, CodecError> {
    let mut p = 0;
    let cols = get_vi(d, &mut p).ok_or("bad header")? as usize;
    let rows = get_vi(d, &mut p).ok_or("bad header")? as usize;
    if cols < 2 || cols > 2048 || rows < 2 || rows > 200_000 {
        return Err("bad dims".into());
    }
    let cells = cols.checked_mul(rows).ok_or("bad dims")?;
    if cells > 20_000_000 {
        return Err("too many cells".into());
    }
    if p.checked_add(rows).ok_or("bad ends")? > d.len() {
        return Err("bad ends".into());
    }
    let ends = d[p..p + rows].to_vec();
    p += rows;
    let mlen = get_vi(d, &mut p).ok_or("bad meta")? as usize;
    if p.checked_add(mlen).ok_or("bad meta")? > d.len() {
        return Err("bad meta".into());
    }
    let mend = p + mlen;
    let mut mp = p;
    let mut sp = mend;
    let mut tab: Vec<Vec<Vec<u8>>> = vec![Vec::new(); cols];
    for c in 0..cols {
        let mut prev: Vec<u8> = Vec::new();
        for _ in 0..rows {
            let pl = get_vi(d, &mut mp).ok_or("bad pl")? as usize;
            let sl = get_vi(d, &mut mp).ok_or("bad sl")? as usize;
            if pl > prev.len() || sl > MAX_INPUT {
                return Err("bad prefix".into());
            }
            if sp.checked_add(sl).ok_or("bad suf")? > d.len() {
                return Err("truncated".into());
            }
            let mut cur = Vec::with_capacity(pl + sl);
            cur.extend_from_slice(&prev[..pl]);
            cur.extend_from_slice(&d[sp..sp + sl]);
            sp += sl;
            prev = cur.clone();
            tab[c].push(cur);
        }
    }
    if mp != mend {
        return Err("meta mismatch".into());
    }
    assemble(&tab, &ends, cols, rows)
}

fn assemble(tab: &[Vec<Vec<u8>>], ends: &[u8], cols: usize, rows: usize) -> Result<Vec<u8>, CodecError> {
    let mut out = Vec::new();
    for r in 0..rows {
        for c in 0..cols {
            if c > 0 {
                out.push(b',');
            }
            out.extend_from_slice(&tab[c][r]);
            if out.len() > MAX_PAYLOAD {
                return Err("too big".into());
            }
        }
        match ends[r] {
            1 => out.push(b'\n'),
            2 => {
                out.extend_from_slice(b"\r\n");
            }
            _ => {}
        }
        if out.len() > MAX_PAYLOAD {
            return Err("too big".into());
        }
    }
    Ok(out)
}

impl Codec for Candidate {
    fn encode(input: &[u8], _model: &[u8]) -> Result<Vec<u8>, CodecError> {
        if input.len() > MAX_INPUT {
            return Err("input too big".into());
        }
        if _model.len() > MAX_PAYLOAD {
            return Err("model too big".into());
        }
        let raw = zstd_compress(input, 3).map_err(|e| e)?;
        if raw.len() > MAX_PAYLOAD {
            return Err("output too big".into());
        }
        let mut best_tag: u8 = 0;
        let mut best: Vec<u8> = raw;
        if let Some((cs, es)) = split_lines(input) {
            if let Some((cols, tab)) = table(&cs) {
                let rows = cs.len();
                if let Some(tp) = build_transpose(cols, rows, &es, &tab) {
                    if let Ok(z) = zstd_compress(&tp, 3) {
                        if z.len() < best.len() {
                            best = z;
                            best_tag = 1;
                        }
                    }
                }
                if let Some(pp) = build_prefix(cols, rows, &es, &tab) {
                    if let Ok(z) = zstd_compress(&pp, 3) {
                        if z.len() < best.len() {
                            best = z;
                            best_tag = 2;
                        }
                    }
                }
            }
        }
        if best.len().checked_add(1).ok_or("too big")? > MAX_PAYLOAD {
            return Err("output too big".into());
        }
        let mut out = Vec::with_capacity(best.len() + 1);
        out.push(best_tag);
        out.extend_from_slice(&best);
        Ok(out)
    }

    fn decode(encoded: &[u8], _model: &[u8], original_len: usize) -> Result<Vec<u8>, CodecError> {
        if original_len > MAX_INPUT {
            return Err("bad original_len".into());
        }
        if encoded.is_empty() {
            return Err("empty frame".into());
        }
        let tag = encoded[0];
        let blob = zstd_decompress(&encoded[1..]).map_err(|e| e)?;
        if blob.len() > MAX_PAYLOAD {
            return Err("payload too big".into());
        }
        let out = match tag {
            0 => blob,
            1 => rebuild_transpose(&blob)?,
            2 => rebuild_prefix(&blob)?,
            _ => return Err("bad tag".into()),
        };
        if out.len() != original_len {
            return Err("length mismatch".into());
        }
        Ok(out)
    }
}
