// Mechanism: byte-exact columnar transpose for comma/newline CSV telemetry.
// Rows are split on b'\n' only (so \r of CRLF stays inside the last field and
// round-trips), then each row is split on b',' with no quote handling: split and
// rejoin on the same bytes is exactly reversible regardless of quoting. Fields
// are stored column-major as (varint len + raw bytes) per field, preceded by
// (rows, cols, trailing-newline flag). Column-major order puts same-field values
// adjacently (timestamps share prefixes, numeric spellings share prefixes and
// short alphabets) and places exactly-duplicated columns (e.g. rv1==rv2, if it
// holds) adjacently, so zstd finds long matches/runs it cannot see row-major.
// Header + body stay together; no float parsing/reserialization is used, so raw
// spellings, whitespace, quoting, line endings and final-newline presence are
// preserved. Anything irregular (empty, NUL bytes, ragged columns, too big) or
// anything that compresses worse takes a reversible fallback: tag 0x00 + zstd of
// the raw input. Tag 0x01 + zstd of the columnar blob is the structured path.
// Encoder picks the smaller of the two zstd payloads, so it never loses to raw
// zstd. All lengths are varint/count checked before allocation and indexing.
use crate::toolkit::{zstd_compress, zstd_decompress};
use crate::{Codec, CodecError};

pub struct Candidate;

fn put_var(mut v: u32, out: &mut Vec<u8>) {
    while v >= 0x80 {
        out.push((v as u8) | 0x80);
        v >>= 7;
    }
    out.push(v as u8);
}

fn read_var(buf: &[u8], pos: &mut usize) -> Option<u32> {
    let mut v: u32 = 0;
    let mut s = 0;
    for _ in 0..5 {
        if *pos >= buf.len() {
            return None;
        }
        let b = buf[*pos];
        *pos += 1;
        v |= ((b & 0x7f) as u32).checked_shl(s)?;
        s += 7;
        if b & 0x80 == 0 {
            return Some(v);
        }
    }
    None
}

// Build uncompressed columnar blob; None => use fallback.
fn build_columnar(input: &[u8]) -> Option<Vec<u8>> {
    if input.is_empty() || input.len() > crate::MAX_INPUT {
        return None;
    }
    if input.contains(&0) {
        return None;
    }
    let trailing = u8::from(input.ends_with(b"\n"));
    let mut rows: Vec<&[u8]> = input.split(|b| *b == b'\n').collect();
    if trailing == 1 {
        rows.pop();
    }
    let nrows = rows.len();
    if nrows < 2 || nrows > 200_000 {
        return None;
    }
    let ncols = rows[0].split(|b| *b == b',').count();
    if ncols < 2 || ncols > 2000 {
        return None;
    }
    let mut table: Vec<Vec<&[u8]>> = Vec::with_capacity(nrows);
    for r in &rows {
        let f: Vec<&[u8]> = r.split(|b| *b == b',').collect();
        if f.len() != ncols {
            return None;
        }
        for x in &f {
            if x.len() > 64 * 1024 {
                return None;
            }
        }
        table.push(f);
    }
    let prod = (nrows as u64).checked_mul(ncols as u64)?;
    if prod > 8_000_000 {
        return None;
    }
    let mut out = Vec::with_capacity(input.len() + 16);
    put_var(nrows as u32, &mut out);
    put_var(ncols as u32, &mut out);
    out.push(trailing);
    for c in 0..ncols {
        for r in 0..nrows {
            let f = table[r][c];
            put_var(f.len() as u32, &mut out);
            out.extend_from_slice(f);
        }
    }
    Some(out)
}

fn untranspose(plain: &[u8], original_len: usize) -> Result<Vec<u8>, CodecError> {
    let mut pos = 0;
    let nrows = read_var(plain, &mut pos).ok_or("bad header")? as usize;
    let ncols = read_var(plain, &mut pos).ok_or("bad header")? as usize;
    if nrows == 0 || ncols == 0 || nrows > 1_000_000 || ncols > 10_000 {
        return Err("bad dims".into());
    }
    let prod = (nrows as u64).checked_mul(ncols as u64).ok_or("bad dims")?;
    if prod > 20_000_000 {
        return Err("bad dims".into());
    }
    if pos >= plain.len() {
        return Err("bad header".into());
    }
    let trailing = plain[pos] == 1;
    if plain[pos] > 1 {
        return Err("bad flag".into());
    }
    pos += 1;
    // Read column-major fields into flat table[col * nrows + row].
    let mut tab: Vec<&[u8]> = Vec::new();
    tab.try_reserve(prod as usize).map_err(|_| "too big")?;
    for _ in 0..prod {
        let L = read_var(plain, &mut pos).ok_or("bad field")? as usize;
        if L > crate::MAX_INPUT || pos.checked_add(L).is_none() || pos + L > plain.len() {
            return Err("bad field".into());
        }
        tab.push(&plain[pos..pos + L]);
        pos += L;
    }
    if original_len > crate::MAX_INPUT {
        return Err("bad len".into());
    }
    let mut out = Vec::new();
    out.try_reserve(original_len).map_err(|_| "too big")?;
    for r in 0..nrows {
        for c in 0..ncols {
            if c > 0 {
                out.push(b',');
            }
            out.extend_from_slice(tab[c * nrows + r]);
        }
        if r + 1 < nrows || trailing {
            out.push(b'\n');
        }
    }
    if out.len() != original_len {
        return Err("len mismatch".into());
    }
    Ok(out)
}

impl Codec for Candidate {
    fn encode(input: &[u8], _model: &[u8]) -> Result<Vec<u8>, CodecError> {
        if input.len() > crate::MAX_INPUT {
            return Err("input too big".into());
        }
        let raw = zstd_compress(input, 19)?;
        if let Some(plain) = build_columnar(input) {
            let cz = zstd_compress(&plain, 19)?;
            if cz.len() < raw.len() {
                let mut o = Vec::with_capacity(cz.len() + 1);
                o.push(0x01);
                o.extend_from_slice(&cz);
                return Ok(o);
            }
        }
        let mut o = Vec::with_capacity(raw.len() + 1);
        o.push(0x00);
        o.extend_from_slice(&raw);
        Ok(o)
    }

    fn decode(encoded: &[u8], _model: &[u8], original_len: usize) -> Result<Vec<u8>, CodecError> {
        if original_len > crate::MAX_INPUT {
            return Err("bad len".into());
        }
        if encoded.is_empty() {
            return Err("empty".into());
        }
        let (tag, body) = (encoded[0], &encoded[1..]);
        if tag == 0x00 {
            let o = zstd_decompress(body)?;
            if o.len() != original_len {
                return Err("len mismatch".into());
            }
            Ok(o)
        } else if tag == 0x01 {
            untranspose(&zstd_decompress(body)?, original_len)
        } else {
            Err("bad tag".into())
        }
    }
}
