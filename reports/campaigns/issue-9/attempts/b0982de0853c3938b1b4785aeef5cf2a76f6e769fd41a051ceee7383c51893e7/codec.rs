// Candidate codec: case-plane + line-prefix-delta transform with a zstd backend.
//
// Algorithm (encode): split the block into LF-terminated lines without ever
// normalizing bytes (CR, NUL and non-UTF8 bytes are preserved verbatim).
// For each line after the first, emit the length of the common byte prefix
// with the previous *original* line plus the remaining suffix (a delta of the
// line-prefix family). Then fold ASCII A-Z in every suffix to lowercase and
// record one case bit per suffix byte, packed LSB-first (a channel-split /
// bitplane family: the dense lowercase stream compresses better while the
// sparse bitmap captures the exception pattern). Serialize a small header,
// the per-line records, the folded suffixes and the bitmap, compress the
// whole buffer with zstd level 19, and select the smallest of the raw-zstd,
// structured-zstd and stored (uncompressed) candidates. Output starts with
// a documented mode byte followed by a CRC32 of the original input.
//
// Inverse (decode): read mode + CRC, recover the payload (zstd-decompress or
// memcpy), then for the structured path parse the records, rebuild each line
// as prev_line[..prefix_len] + unfolded suffix (uppercasing a-z back to A-Z
// wherever the case bit is set), rejoin with LF plus the recorded trailing
// newline flag, and verify both the reconstructed length and the CRC32.
//
// Counted costs: the learned model is empty (0 bytes); the transform needs
// no training because ASCII case and line prefixes are static structural
// facts, so per-block cost is exactly 1 mode byte + 4 CRC bytes + the zstd
// frame or stored bytes, all measured in the final archive. Structured-path
// overhead inside the zstd input is two varints per line plus ceil(suffix
// bytes / 8) bitmap bytes. Per-block mode selection compares full candidate
// sizes, so output is never larger than raw zstd + 1 byte except for the
// fixed 5-byte header, and tiny inputs use the stored mode.
//
// Family and applicability: channel-split/bitplane + prefix-delta transforms
// in the spirit of Mahoney's Data Compression Explained (dictionary/context
// transforms) and Gorilla-style bitplane/delta separation (Pelkonen et al.),
// paired with a standard LZ backend (Zstandard). This is deliberately a
// different family from the earlier line-dedup + word-dictionary trial:
// no line table, no word IDs, no dictionary. It fits the development probes
// (74246 short LF lines, 38% repeated lines, strong order-1/2 byte contexts,
// prose vocabulary with mixed case, 101 distinct bytes, valid UTF-8) because
// adjacent verse/dialogue lines share speaker prefixes and case-insensitive
// wording; folding lets the LZ backend match across case variants while the
// bitmap stays near-zero and compresses to almost nothing. Only ASCII A-Z
// is folded, so UTF-8 multibyte sequences pass through untouched. Range
// coding (Duda, Asymmetric Numeral Systems) was considered, but a blind
// custom entropy coder risks exactness bugs; the trusted zstd backend keeps
// the new mechanism confined to the reversible transform. Binary or
// NUL-containing blocks skip the structured path via a cheap textualness
// test and use raw zstd or stored bytes, which are exactly reversible.
//
// Modes: 0x00 = zstd(raw input), 0x01 = zstd(structured buffer below),
// 0x02 = stored raw input. Structured buffer layout:
// ULEB128(nlines) || u8(trailing_newline 0/1) || per-line records || bitmap.
// Each record: ULEB128(prefix_len) || ULEB128(suffix_len) || folded suffix.
// Bitmap: ceil(total_suffix_bytes / 8) bytes, bit k (LSB-first) = 1 iff the
// k-th suffix byte over all records in order was uppercase ASCII.
use crate::{Codec, CodecError};
use crate::toolkit::{crc32, zstd_compress, zstd_decompress};

pub struct Candidate;

// Documented mode bytes so source review can tell the paths apart.
const MODE_RAW: u8 = 0x00;
const MODE_STRUCTURED: u8 = 0x01;
const MODE_STORED: u8 = 0x02;
// Fixed header after the mode byte: CRC32 (little endian) of the input.
const HEADER_LEN: usize = 5;
// zstd level balancing ratio against the 30 s encode budget.
const ZSTD_LEVEL: i32 = 19;

impl Codec for Candidate {
    // Static structural transform: nothing to learn, so the model is empty
    // and contributes zero counted bytes to the archive.
    fn train(_samples: &[&[u8]]) -> Result<Vec<u8>, CodecError> {
        Ok(Vec::new())
    }

    fn encode(input: &[u8], _model: &[u8]) -> Result<Vec<u8>, CodecError> {
        let crc = crc32(input);
        // Baseline candidate: raw input through zstd. Input blocks are
        // bounded by MAX_INPUT (2 MiB), well under the zstd MAX_PAYLOAD limit.
        let raw_body = zstd_compress(input, ZSTD_LEVEL)?;
        let mut best_mode = MODE_RAW;
        let mut best_body = raw_body;
        // Structured candidate only for plausibly textual blocks; binary
        // blocks would only gain bitmap noise and encoder time.
        if !input.is_empty() && looks_textual(input) {
            let structured = build_structured(input);
            // Tolerate backend rejection (e.g. pathological all-newline
            // input whose varint records exceed backend limits) by falling
            // back to the raw/stored candidates instead of failing.
            if let Ok(struct_body) = zstd_compress(&structured, ZSTD_LEVEL) {
                if struct_body.len() < best_body.len() {
                    best_mode = MODE_STRUCTURED;
                    best_body = struct_body;
                }
            }
        }
        // Reversible byte fallback for fragments/tiny inputs: storing the
        // bytes beats any frame header when both zstd outputs are larger.
        if input.len() < best_body.len() {
            best_mode = MODE_STORED;
            best_body = input.to_vec();
        }
        let mut out = Vec::with_capacity(HEADER_LEN + best_body.len());
        out.push(best_mode);
        out.extend_from_slice(&crc.to_le_bytes());
        out.extend_from_slice(&best_body);
        Ok(out)
    }

    fn decode(encoded: &[u8], _model: &[u8], original_len: usize) -> Result<Vec<u8>, CodecError> {
        if encoded.len() < HEADER_LEN {
            return Err("truncated header".into());
        }
        let mode = encoded[0];
        let mut crc_bytes = [0u8; 4];
        crc_bytes.copy_from_slice(&encoded[1..HEADER_LEN]);
        let want_crc = u32::from_le_bytes(crc_bytes);
        let body = &encoded[HEADER_LEN..];
        let data = match mode {
            MODE_RAW => zstd_decompress(body)?,
            MODE_STRUCTURED => {
                let buf = zstd_decompress(body)?;
                inverse_structured(&buf, original_len)?
            }
            MODE_STORED => body.to_vec(),
            _ => return Err("unknown mode byte".into()),
        };
        if data.len() != original_len {
            return Err("length mismatch".into());
        }
        if crc32(&data) != want_crc {
            return Err("crc mismatch".into());
        }
        Ok(data)
    }
}

// True for NUL-free blocks with >= 90% printable-ASCII / LF / CR / TAB /
// high bytes; the development text (LF lines, valid UTF-8) passes easily.
fn looks_textual(block: &[u8]) -> bool {
    if block.is_empty() {
        return false;
    }
    let mut ok: usize = 0;
    for &b in block {
        if b == 0 {
            return false;
        }
        if b == 9 || b == 10 || b == 13 || (32..=126).contains(&b) || b >= 128 {
            ok += 1;
        }
    }
    ok.saturating_mul(10) >= block.len().saturating_mul(9)
}

fn write_uleb128(out: &mut Vec<u8>, mut value: u64) {
    loop {
        let mut byte = (value & 0x7f) as u8;
        value >>= 7;
        if value != 0 {
            byte |= 0x80;
            out.push(byte);
        } else {
            out.push(byte);
            break;
        }
    }
}

fn common_prefix(a: &[u8], b: &[u8]) -> usize {
    let mut n = 0;
    while n < a.len() && n < b.len() && a[n] == b[n] {
        n += 1;
    }
    n
}

// Build the structured buffer described in the header comment. Splits on LF
// only; a trailing LF sets the flag and any CR bytes stay inside line data.
fn build_structured(input: &[u8]) -> Vec<u8> {
    let trailing_nl = input.last() == Some(&b'\n');
    let mut lines: Vec<&[u8]> = input.split(|&b| b == b'\n').collect();
    if trailing_nl {
        lines.pop();
    }
    let mut out = Vec::with_capacity(input.len() + lines.len() * 2 + 16);
    write_uleb128(&mut out, lines.len() as u64);
    out.push(trailing_nl as u8);
    let mut bitmap: Vec<u8> = Vec::new();
    let mut bit_len: usize = 0;
    let mut prev: &[u8] = b"";
    for line in lines {
        let plen = common_prefix(prev, line);
        let suffix = &line[plen..];
        write_uleb128(&mut out, plen as u64);
        write_uleb128(&mut out, suffix.len() as u64);
        for &b in suffix {
            let upper = (b'A'..=b'Z').contains(&b);
            out.push(if upper { b + 32 } else { b });
            if bit_len % 8 == 0 {
                bitmap.push(0);
            }
            if upper {
                let idx = bit_len / 8;
                bitmap[idx] |= 1u8 << (bit_len % 8);
            }
            bit_len += 1;
        }
        prev = line;
    }
    out.extend_from_slice(&bitmap);
    out
}

struct ByteReader<'a> {
    buf: &'a [u8],
    pos: usize,
}

impl<'a> ByteReader<'a> {
    fn read_byte(&mut self) -> Result<u8, CodecError> {
        let b = *self.buf.get(self.pos).ok_or("truncated buffer")?;
        self.pos += 1;
        Ok(b)
    }

    fn read_uleb128(&mut self) -> Result<u64, CodecError> {
        let mut value: u64 = 0;
        for i in 0..10u32 {
            let b = self.read_byte()?;
            if i == 9 && b > 1 {
                return Err("varint overflow".into());
            }
            value |= ((b & 0x7f) as u64) << (7 * i);
            if b & 0x80 == 0 {
                return Ok(value);
            }
        }
        Err("varint too long".into())
    }

    fn read_slice(&mut self, len: usize) -> Result<&'a [u8], CodecError> {
        let end = self.pos.checked_add(len).ok_or("length overflow")?;
        if end > self.buf.len() {
            return Err("truncated buffer".into());
        }
        let s = &self.buf[self.pos..end];
        self.pos = end;
        Ok(s)
    }
}

// Inverse of build_structured: reconstruct exact original bytes, including
// whitespace, numeric spellings, quoting, CR bytes and line endings.
fn inverse_structured(buf: &[u8], original_len: usize) -> Result<Vec<u8>, CodecError> {
    let mut reader = ByteReader { buf, pos: 0 };
    let nlines = reader.read_uleb128()?;
    if nlines > buf.len() as u64 + 1 {
        return Err("bad line count".into());
    }
    let nl_flag = reader.read_byte()?;
    if nl_flag > 1 {
        return Err("bad newline flag".into());
    }
    // Buffer record headers with offsets into buf (zero-copy suffixes).
    let mut records: Vec<(u64, usize, usize)> = Vec::new();
    let mut total_suffix: usize = 0;
    let mut count: u64 = 0;
    while count < nlines {
        let plen = reader.read_uleb128()?;
        let slen = reader.read_uleb128()?;
        if slen > usize::MAX as u64 {
            return Err("length overflow".into());
        }
        let slen_usize = slen as usize;
        let start = reader.pos;
        reader.read_slice(slen_usize)?;
        total_suffix = total_suffix.checked_add(slen_usize).ok_or("length overflow")?;
        records.push((plen, start, slen_usize));
        count += 1;
    }
    let bitmap_len = (total_suffix + 7) / 8;
    let bitmap_start = reader.pos;
    let bitmap_end = bitmap_start.checked_add(bitmap_len).ok_or("length overflow")?;
    if bitmap_end != buf.len() {
        return Err("trailing bytes".into());
    }
    let bitmap = &buf[bitmap_start..bitmap_end];
    // original_len comes from trusted framing; capacity is exact, and every
    // push below is guarded so corrupt records error instead of growing.
    let mut out = Vec::with_capacity(original_len);
    let mut prev: Vec<u8> = Vec::new();
    let mut bit_idx: usize = 0;
    for (i, (plen_u64, start, slen)) in records.iter().enumerate() {
        if i > 0 {
            if out.len() + 1 > original_len {
                return Err("length overflow".into());
            }
            out.push(b'\n');
        }
        if *plen_u64 > prev.len() as u64 {
            return Err("bad prefix length".into());
        }
        let plen = *plen_u64 as usize;
        if out.len() + plen > original_len {
            return Err("length overflow".into());
        }
        out.extend_from_slice(&prev[..plen]);
        if out.len() + *slen > original_len {
            return Err("length overflow".into());
        }
        let suffix = &buf[*start..*start + *slen];
        for &folded in suffix {
            let bit = (bitmap[(bit_idx / 8)] >> (bit_idx % 8)) & 1;
            bit_idx += 1;
            if bit == 1 {
                if !(b'a'..=b'z').contains(&folded) {
                    return Err("bad case bit".into());
                }
                out.push(folded - 32);
            } else {
                out.push(folded);
            }
        }
        let line_len = plen.checked_add(*slen).ok_or("length overflow")?;
        let line_start = out.len().checked_sub(line_len).ok_or("length overflow")?;
        prev.clear();
        prev.extend_from_slice(&out[line_start..]);
    }
    if nl_flag == 1 {
        if out.len() + 1 > original_len {
            return Err("length overflow".into());
        }
        out.push(b'\n');
    }
    if out.len() != original_len {
        return Err("length mismatch".into());
    }
    Ok(out)
}
