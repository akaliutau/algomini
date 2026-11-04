// Candidate text codec: bounded line-level move-to-front (MTF) + run-friendly
// token stream, zstd coded. Family: BWT/MTF/RLE-style recency transform in the
// spirit of Mahoney's Data Compression Explained (dictionary/context/BWT/MTF
// chapter), paired with a standard LZ backend (zstd). This is Trial 4 and is
// deliberately from a different family than the earlier trials: trial 0 used a
// static global line-dedup + word-dictionary with a stored table/model, trial 1
// used a case-plane split + line-prefix delta. This trial uses no static
// dictionary and no prefix delta; it uses a small bounded recency cache.
// Applicability: development probes show LF-terminated text (74246 lines in
// 2 MiB, max line 81 B, 101 distinct bytes, no CRLF/NUL), 28248 repeated lines
// (38% whole-line repeats, 45998 distinct), 185k repeated word pairs, and
// order-1/2 byte entropy well below order-0. Repeated verse-like lines are
// scattered, so a streaming recency cache captures them without a global table.
// Algorithm (encode): split input on LF byte 0x0A only, preserving every other
// byte (CR, NUL, non-UTF8 pass through; final fragment without LF is a record).
// Maintain a bounded MTF list of up to 128 recent records, each at most 512 B
// (covers the observed 81 B max with margin; longer records bypass the cache).
// For each record in order: linear search the cache; on hit at index i emit one
// tag byte (i+1 in 1..=128); on miss emit tag 0x00 + unsigned LEB128 length +
// raw record bytes, then insert the record at the front on the same rule.
// Prefix the token stream with a 4-byte little-endian record count, then
// compress that stream with zstd level 19. Also compress the raw input with
// zstd level 19 and emit whichever is smaller, prefixed with a mode byte
// (0x00 = raw zstd fallback, 0x01 = MTF structured). Hence binary or
// incompressible inputs automatically use the fallback and never inflate.
// Inverse (decode): branch on the mode byte; zstd-decompress the body; for
// mode 0x01 parse count + tags with the identical cache rule (hit: copy cached
// record to output and move to front; miss: copy literal bytes and insert when
// cacheable), concatenating records reproduces the input byte-exactly.
// Counted costs: train returns 0 model bytes. All tables are implicit in the
// payload (1 B per hit vs ~28 B raw line; ~2 B overhead per miss literal).
// Total archive = model (0) + 1 mode byte + zstd frame of the chosen stream;
// comparison is on model + payload + native frame, so the adaptive pick is the
// measured optimum of the two paths, never worse than raw zstd by more than
// the selection itself.
use crate::{Codec, CodecError};

pub struct Candidate;

const CACHE_ENTRIES: usize = 128;
const CACHE_MAX_LINE: usize = 512;
const ZSTD_LEVEL: i32 = 19;
const MODE_RAW: u8 = 0x00;
const MODE_MTF: u8 = 0x01;

fn write_varint(mut v: usize, out: &mut Vec<u8>) {
    loop {
        let b = (v & 0x7f) as u8;
        v >>= 7;
        if v == 0 {
            out.push(b);
            break;
        } else {
            out.push(b | 0x80);
        }
    }
}

fn read_varint(buf: &[u8], pos: &mut usize) -> Result<usize, CodecError> {
    let mut shift: u32 = 0;
    let mut value: usize = 0;
    loop {
        if *pos >= buf.len() {
            return Err("mtf stream truncated in varint".into());
        }
        let b = buf[*pos];
        *pos += 1;
        if shift >= 28 && (b & 0x7f) as usize > (usize::MAX >> shift) {
            return Err("mtf varint overflow".into());
        }
        value |= ((b & 0x7f) as usize) << shift;
        if b & 0x80 == 0 {
            break;
        }
        shift += 7;
        if shift > 35 {
            return Err("mtf varint too long".into());
        }
    }
    Ok(value)
}

fn build_mtf_stream(input: &[u8]) -> Vec<u8> {
    // Split into LF-terminated records, preserving all other bytes.
    let mut trans: Vec<u8> = Vec::with_capacity(input.len() + 1024);
    // Count records first for the 4-byte header.
    let mut n: u32 = 0;
    let mut start: usize = 0;
    if !input.is_empty() {
        let mut i: usize = 0;
        while i < input.len() {
            if input[i] == b'\n' {
                n += 1;
                i += 1;
                start = i;
            } else {
                i += 1;
            }
        }
        if start < input.len() {
            n += 1;
        }
        if input.is_empty() {
            n = 0;
        }
    }
    trans.extend_from_slice(&n.to_le_bytes());
    if n == 0 {
        return trans;
    }
    let mut cache: Vec<Vec<u8>> = Vec::new();
    let mut rec_start: usize = 0;
    let mut idx: usize = 0;
    while idx < input.len() {
        if input[idx] == b'\n' {
            let rec: &[u8] = &input[rec_start..idx + 1];
            let mut hit: Option<usize> = None;
            for (k, c) in cache.iter().enumerate() {
                if c.as_slice() == rec {
                    hit = Some(k);
                    break;
                }
            }
            if let Some(h) = hit {
                trans.push((h + 1) as u8);
                let entry = cache.remove(h);
                cache.insert(0, entry);
            } else {
                trans.push(0x00);
                write_varint(rec.len(), &mut trans);
                trans.extend_from_slice(rec);
                if rec.len() <= CACHE_MAX_LINE {
                    cache.insert(0, rec.to_vec());
                    if cache.len() > CACHE_ENTRIES {
                        cache.pop();
                    }
                }
            }
            rec_start = idx + 1;
        }
        idx += 1;
    }
    if rec_start < input.len() {
        let rec: &[u8] = &input[rec_start..];
        let mut hit: Option<usize> = None;
        for (k, c) in cache.iter().enumerate() {
            if c.as_slice() == rec {
                hit = Some(k);
                break;
            }
        }
        if let Some(h) = hit {
            trans.push((h + 1) as u8);
            let entry = cache.remove(h);
            cache.insert(0, entry);
        } else {
            trans.push(0x00);
            write_varint(rec.len(), &mut trans);
            trans.extend_from_slice(rec);
            if rec.len() <= CACHE_MAX_LINE {
                cache.insert(0, rec.to_vec());
                if cache.len() > CACHE_ENTRIES {
                    cache.pop();
                }
            }
        }
    }
    trans
}

fn invert_mtf_stream(trans: &[u8], original_len: usize) -> Result<Vec<u8>, CodecError> {
    if trans.len() < 4 {
        return Err("mtf stream too short".into());
    }
    let n = u32::from_le_bytes([trans[0], trans[1], trans[2], trans[3]]) as usize;
    if n > 3_000_000 {
        return Err("mtf record count implausible".into());
    }
    if n == 0 {
        if original_len != 0 {
            return Err("mtf length mismatch".into());
        }
        return Ok(Vec::new());
    }
    let mut cache: Vec<Vec<u8>> = Vec::new();
    let mut out: Vec<u8> = Vec::new();
    let mut pos: usize = 4;
    for _ in 0..n {
        if pos >= trans.len() {
            return Err("mtf stream truncated at tag".into());
        }
        let tag = trans[pos];
        pos += 1;
        if tag == 0x00 {
            let len = read_varint(trans, &mut pos)?;
            if len > 2 * 1024 * 1024 + 16 {
                return Err("mtf literal too long".into());
            }
            if pos + len > trans.len() {
                return Err("mtf stream truncated at literal".into());
            }
            let rec = trans[pos..pos + len].to_vec();
            pos += len;
            if out.len() + rec.len() > original_len {
                return Err("mtf output exceeds original length".into());
            }
            out.extend_from_slice(&rec);
            if rec.len() <= CACHE_MAX_LINE {
                cache.insert(0, rec);
                if cache.len() > CACHE_ENTRIES {
                    cache.pop();
                }
            }
        } else if tag >= 0x01 && tag <= CACHE_ENTRIES as u8 {
            let h = (tag - 1) as usize;
            if h >= cache.len() {
                return Err("mtf index out of range".into());
            }
            let entry = cache.remove(h);
            if out.len() + entry.len() > original_len {
                return Err("mtf output exceeds original length".into());
            }
            out.extend_from_slice(&entry);
            cache.insert(0, entry);
        } else {
            return Err("mtf unknown tag".into());
        }
    }
    if out.len() != original_len {
        return Err("mtf length mismatch".into());
    }
    Ok(out)
}

impl Codec for Candidate {
    fn train(_samples: &[&[u8]]) -> Result<Vec<u8>, CodecError> {
        Ok(Vec::new())
    }

    fn encode(input: &[u8], _model: &[u8]) -> Result<Vec<u8>, CodecError> {
        // Tiny inputs bypass the transform; direct zstd keeps framing exact.
        if input.len() < 256 {
            let body = crate::zstd_compress(input, ZSTD_LEVEL)?;
            let mut out = Vec::with_capacity(body.len() + 1);
            out.push(MODE_RAW);
            out.extend_from_slice(&body);
            return Ok(out);
        }
        let raw_res = crate::zstd_compress(input, ZSTD_LEVEL);
        let mut structured_res: Result<Vec<u8>, CodecError> =
            Err("structured path skipped".into());
        // Bound work: skip the MTF build only when it cannot fit the payload
        // limit even in principle (trans worst case is bounded here by check).
        let trans = build_mtf_stream(input);
        if trans.len() <= 4 * 1024 * 1024 {
            match crate::zstd_compress(&trans, ZSTD_LEVEL) {
                Ok(v) => structured_res = Ok(v),
                Err(e) => structured_res = Err(e),
            }
        }
        match (raw_res, structured_res) {
            (Ok(raw), Ok(st)) => {
                if st.len() < raw.len() {
                    // Use structured only when its frame is strictly smaller.
                    let mut out = Vec::with_capacity(st.len() + 1);
                    out.push(MODE_MTF);
                    out.extend_from_slice(&st);
                    Ok(out)
                } else {
                    let mut out = Vec::with_capacity(raw.len() + 1);
                    out.push(MODE_RAW);
                    out.extend_from_slice(&raw);
                    Ok(out)
                }
            }
            (Ok(raw), Err(_)) => {
                let mut out = Vec::with_capacity(raw.len() + 1);
                out.push(MODE_RAW);
                out.extend_from_slice(&raw);
                Ok(out)
            }
            (Err(_), Ok(st)) => {
                let mut out = Vec::with_capacity(st.len() + 1);
                out.push(MODE_MTF);
                out.extend_from_slice(&st);
                Ok(out)
            }
            (Err(e), Err(_)) => Err(e),
        }
    }

    fn decode(encoded: &[u8], _model: &[u8], original_len: usize) -> Result<Vec<u8>, CodecError> {
        if encoded.is_empty() {
            return Err("empty frame".into());
        }
        let mode = encoded[0];
        let body = &encoded[1..];
        if mode == MODE_RAW {
            let out = crate::zstd_decompress(body)?;
            if out.len() != original_len {
                return Err("length mismatch in raw path".into());
            }
            Ok(out)
        } else if mode == MODE_MTF {
            let trans = crate::zstd_decompress(body)?;
            invert_mtf_stream(&trans, original_len)
        } else {
            Err("unknown mode byte".into())
        }
    }
}
