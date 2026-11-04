// Candidate text codec: line-dedup + word-dictionary pre-transform, zstd coded.
// Family: dictionary / template transform in the spirit of Mahoney's
// Data Compression Explained (dictionary and context transforms) paired with
// a standard LZ backend (Zstandard). No PPM/PAQ mixing is implemented here:
// the measured gap on development text is repeated whole lines (28248 repeats
// in 74246 lines) and repeated words ("the" x10270, "and" x7952, ...), which a
// bounded per-block line table plus a small trained word table captures with
// fully counted headers. See Mahoney (mattmahoney.net/dc/dce.html) and the
// Zstandard reference; ANS/Gorilla ideas were considered but lexical spelling
// must be preserved exactly, so no numeric re-coding is used.
// Applicability: line-oriented text with whole-line repeats and a stable
// frequent-word head (probes: 101 distinct bytes, LF-delimited, max line 81 B,
// order-1 entropy 3.56 bits/byte). Binary or delimiter-free inputs fall back.
// Algorithm (encode): parse trained word list from model; reject structured
// path when input holds 0x00/0x01/0x02; split input into complete records
// (bytes up to and including each LF, plus a final fragment); count repeated
// lines in this block and keep the top positive-saving entries (ref = 3 bytes
// 0x02 + u16 id); word-encode remaining lines with 2-byte codes (0x01 + u8 id)
// for dictionary words with alnum boundaries via first-byte buckets and
// longest match; frame body = [u16 num lines][u32 len + bytes]* + transformed
// stream; zstd-compress body at level 19; also zstd-compress raw input; emit
// the smaller of [0x00]+raw and [0x01]+compressed-body.
// Inverse (decode): mode 0x00 -> zstd-decompress; mode 0x01 ->
// zstd-decompress body, split header table, single left-to-right scan expanding
// 0x01 word codes and 0x02 line refs, other bytes literal; verify length.
// Counted costs: model = word header + entries (usually < 8 KiB, empty when no
// gain); payload header = per-block line table inside the zstd frame plus one
// mode byte; archive compared as model + payload + native frame. No sample
// records are embedded: the model stores only aggregated frequent words, the
// line table stores only this block's own repeated lines in its own payload.
// Reversibility: transforms use reserved markers absent from structured inputs
// (checked), line splits keep delimiters, word codes restore exact spelling,
// fragments and endings preserved; malformed/truncated data returns Err.
use crate::toolkit::{zstd_compress, zstd_decompress};
use crate::{Codec, CodecError};
use std::collections::HashMap;

pub struct Candidate;

const MAGIC: [u8; 4] = [0x43, 0x31, 0x54, 0x58];
const VERSION: u8 = 0x01;
const ZLEVEL: i32 = 19;
const MAX_WORDS: usize = 220;
const MAX_WORD_LEN: usize = 32;
const MAX_LINE_DICT: usize = 2048;
const MAX_LINE_DICT_BYTES: usize = 180_000;

fn is_letter(b: u8) -> bool {
    matches!(b, b'A'..=b'Z' | b'a'..=b'z')
}

fn is_word_char(b: u8) -> bool {
    matches!(b, b'A'..=b'Z' | b'a'..=b'z' | b'\'')
}

fn is_cont(b: u8) -> bool {
    matches!(
        b,
        b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'_' | b'\''
    )
}

// Parse model bytes into word list. Empty model means no words.
fn parse_words(model: &[u8]) -> Result<Vec<Vec<u8>>, CodecError> {
    if model.is_empty() {
        return Ok(Vec::new());
    }
    if model.len() < 7 {
        return Err("bad model header".into());
    }
    if model[0..4] != MAGIC {
        return Err("bad model magic".into());
    }
    if model[4] != VERSION {
        return Err("bad model version".into());
    }
    let n = ((model[5] as usize) << 8) | (model[6] as usize);
    let mut words = Vec::new();
    let mut pos = 7usize;
    for _ in 0..n {
        if pos >= model.len() {
            return Err("bad model entry".into());
        }
        let l = model[pos] as usize;
        pos += 1;
        if l < 3 || l > MAX_WORD_LEN {
            return Err("bad model word len".into());
        }
        if pos + l > model.len() {
            return Err("bad model truncation".into());
        }
        words.push(model[pos..pos + l].to_vec());
        pos += l;
    }
    if pos != model.len() {
        return Err("bad model trailing bytes".into());
    }
    Ok(words)
}

fn count_words_in(samples: &[&[u8]], freq: &mut HashMap<Vec<u8>, u32>) {
    for s in samples {
        let mut i = 0usize;
        while i < s.len() {
            if is_word_char(s[i]) {
                let mut j = i + 1;
                while j < s.len() && is_word_char(s[j]) {
                    j += 1;
                }
                // Trim edge apostrophes.
                mut_trim_count(freq, &s[i..j]);
                i = j;
            } else {
                i += 1;
            }
        }
    }
}

fn mut_trim_count(freq: &mut HashMap<Vec<u8>, u32>, raw: &[u8]) {
    let mut a = 0usize;
    let mut b = raw.len();
    while a < b && raw[a] == b'\'' {
        a += 1;
    }
    while b > a && raw[b - 1] == b'\'' {
        b -= 1;
    }
    if b - a < 3 || b - a > MAX_WORD_LEN {
        return;
    }
    let w = &raw[a..b];
    // Require at least one ASCII letter so digit/quote runs are ignored.
    let mut has_letter = false;
    for &c in w {
        if is_letter(c) {
            has_letter = true;
            break;
        }
        if c != b'\'' {
            return;
        }
    }
    if !has_letter {
        return;
    }
    *freq.entry(w.to_vec()).or_insert(0) += 1;
}

// Split input into complete records: each LF-terminated slice plus trailing fragment.
fn split_lines(input: &[u8]) -> Vec<(usize, usize)> {
    let mut spans = Vec::new();
    let mut start = 0usize;
    for (idx, &b) in input.iter().enumerate() {
        if b == b'\n' {
            spans.push((start, idx + 1));
            start = idx + 1;
        }
    }
    if start < input.len() {
        spans.push((start, input.len()));
    }
    spans
}

impl Codec for Candidate {
    fn train(samples: &[&[u8]]) -> Result<Vec<u8>, CodecError> {
        let mut freq: HashMap<Vec<u8>, u32> = HashMap::new();
        count_words_in(samples, &mut freq);
        let mut scored: Vec<(u64, Vec<u8>)> = Vec::new();
        for (w, c) in freq {
            if w.len() < 3 || w.len() > MAX_WORD_LEN {
                continue;
            }
            if c < 4 {
                continue;
            }
            let saving = (w.len() as u64 - 2) * (c as u64);
            let cost = w.len() as u64 + 1;
            if saving > cost + 16 {
                scored.push((saving - cost, w));
            }
        }
        scored.sort_by(|a, b| b.0.cmp(&a.0).then_with(|| b.1.len().cmp(&a.1.len())));
        if scored.len() > MAX_WORDS {
            scored.truncate(MAX_WORDS);
        }
        if scored.is_empty() {
            return Ok(Vec::new());
        }
        let mut out = Vec::with_capacity(7 + scored.len() * 6);
        out.extend_from_slice(&MAGIC);
        out.push(VERSION);
        out.push(((scored.len() >> 8) & 0xff) as u8);
        out.push((scored.len() & 0xff) as u8);
        for (_, w) in &scored {
            out.push(w.len() as u8);
            out.extend_from_slice(w);
        }
        if out.len() > 65536 {
            return Err("model exceeds limits".into());
        }
        Ok(out)
    }

    fn encode(input: &[u8], model: &[u8]) -> Result<Vec<u8>, CodecError> {
        let words = parse_words(model)?;
        let raw_comp = zstd_compress(input, ZLEVEL)?;
        let mut raw_out = Vec::with_capacity(1 + raw_comp.len());
        raw_out.push(0x00u8);
        raw_out.extend_from_slice(&raw_comp);
        // Tiny inputs cannot amortize a table; keep raw path.
        if input.len() < 64 {
            return Ok(raw_out);
        }
        // Structured markers must be absent; else reversible scan breaks.
        let mut has_marker = false;
        for &b in input {
            if b == 0x00 || b == 0x01 || b == 0x02 {
                has_marker = true;
                break;
            }
        }
        if has_marker {
            return Ok(raw_out);
        }
        // Per-block line frequency.
        let spans = split_lines(input);
        if spans.is_empty() {
            return Ok(raw_out);
        }
        let mut counts: HashMap<Vec<u8>, u32> = HashMap::new();
        for (s, e) in &spans {
            let line = &input[*s..*e];
            // Skip huge lines as dict keys to bound hashing cost.
            if line.len() > 4096 {
                continue;
            }
            *counts.entry(line.to_vec()).or_insert(0) += 1;
        }
        let mut cands: Vec<(u64, Vec<u8>, u32)> = Vec::new();
        for (line, c) in counts {
            if c < 3 {
                continue;
            }
            if line.len() < 6 {
                continue;
            }
            let len = line.len() as u64;
            let cnt = c as u64;
            // Ref costs 3 bytes per use; entry costs len + 4 in header.
            let saving = (len - 3) * cnt;
            let cost = len + 4;
            if saving > cost + 32 {
                cands.push((saving - cost, line, c));
            }
        }
        cands.sort_by(|a, b| b.0.cmp(&a.0));
        let mut dict: Vec<Vec<u8>> = Vec::new();
        let mut dict_bytes = 0usize;
        for (_, line, _) in cands {
            if dict.len() >= MAX_LINE_DICT {
                break;
            }
            if dict_bytes + line.len() + 4 > MAX_LINE_DICT_BYTES {
                break;
            }
            dict_bytes += line.len() + 4;
            dict.push(line);
        }
        if dict.is_empty() && words.is_empty() {
            return Ok(raw_out);
        }
        let mut id_of: HashMap<Vec<u8>, u16> = HashMap::with_capacity(dict.len() * 2);
        for (i, line) in dict.iter().enumerate() {
            id_of.insert(line.clone(), i as u16);
        }
        // First-byte buckets for word lookup.
        let mut buckets: Vec<Vec<usize>> = vec![Vec::new(); 256];
        let mut max_wlen = 0usize;
        for (i, w) in words.iter().enumerate() {
            buckets[w[0] as usize].push(i);
            if w.len() > max_wlen {
                max_wlen = w.len();
            }
        }
        // Transform lines.
        let mut trans: Vec<u8> = Vec::with_capacity(input.len());
        for (s, e) in &spans {
            let line = &input[*s..*e];
            if let Some(&id) = id_of.get(line) {
                trans.push(0x02u8);
                trans.push((id >> 8) as u8);
                trans.push((id & 0xff) as u8);
                continue;
            }
            if words.is_empty() {
                trans.extend_from_slice(line);
                continue;
            }
            let mut k = 0usize;
            while k < line.len() {
                let b = line[k];
                let mut best: Option<usize> = None;
                let mut best_len = 0usize;
                if !buckets[b as usize].is_empty() {
                    // Left boundary: previous byte must not continue a word.
                    let left_ok = if k == 0 {
                        true
                    } else {
                        !is_cont(line[k - 1])
                    };
                    if left_ok {
                        for &wi in &buckets[b as usize] {
                            let w = &words[wi];
                            if w.len() < 3 {
                                continue;
                            }
                            if k + w.len() > line.len() {
                                continue;
                            }
                            if &line[k..k + w.len()] != w.as_slice() {
                                continue;
                            }
                            let right_ok = if k + w.len() >= line.len() {
                                true
                            } else {
                                !is_cont(line[k + w.len()])
                            };
                            if !right_ok {
                                continue;
                            }
                            if w.len() > best_len {
                                best_len = w.len();
                                best = Some(wi);
                            }
                        }
                    }
                }
                if let Some(wi) = best {
                    trans.push(0x01u8);
                    trans.push(wi as u8);
                    k += best_len;
                } else {
                    trans.push(b);
                    k += 1;
                }
                // Safety: max word walk bound (unused, longest-match covers it).
                let _ = max_wlen;
            }
        }
        // Frame body: header + transformed stream, then zstd.
        let mut body: Vec<u8> = Vec::with_capacity(2 + dict_bytes + trans.len());
        body.push(((dict.len() >> 8) & 0xff) as u8);
        body.push((dict.len() & 0xff) as u8);
        for line in &dict {
            let l = line.len() as u32;
            body.push((l >> 24) as u8);
            body.push((l >> 16) as u8);
            body.push((l >> 8) as u8);
            body.push(l as u8);
            body.extend_from_slice(line);
        }
        body.extend_from_slice(&trans);
        let comp = zstd_compress(&body, ZLEVEL)?;
        let mut struct_out = Vec::with_capacity(1 + comp.len());
        struct_out.push(0x01u8);
        struct_out.extend_from_slice(&comp);
        if struct_out.len() < raw_out.len() {
            Ok(struct_out)
        } else {
            Ok(raw_out)
        }
    }

    fn decode(
        encoded: &[u8],
        model: &[u8],
        original_len: usize,
    ) -> Result<Vec<u8>, CodecError> {
        if encoded.is_empty() {
            return Err("empty encoding".into());
        }
        let words = parse_words(model)?;
        match encoded[0] {
            0x00 => {
                let data = zstd_decompress(&encoded[1..])?;
                if data.len() != original_len {
                    return Err("length mismatch".into());
                }
                Ok(data)
            }
            0x01 => {
                let body = zstd_decompress(&encoded[1..])?;
                if body.len() < 2 {
                    return Err("malformed body".into());
                }
                let n = ((body[0] as usize) << 8) | (body[1] as usize);
                let mut pos = 2usize;
                let mut dict: Vec<Vec<u8>> = Vec::with_capacity(n);
                for _ in 0..n {
                    if pos + 4 > body.len() {
                        return Err("malformed line table".into());
                    }
                    let l = ((body[pos] as usize) << 24)
                        | ((body[pos + 1] as usize) << 16)
                        | ((body[pos + 2] as usize) << 8)
                        | (body[pos + 3] as usize);
                    pos += 4;
                    if pos + l > body.len() {
                        return Err("malformed line entry".into());
                    }
                    dict.push(body[pos..pos + l].to_vec());
                    pos += l;
                }
                let trans = &body[pos..];
                let mut out: Vec<u8> = Vec::with_capacity(original_len);
                let mut i = 0usize;
                while i < trans.len() {
                    let b = trans[i];
                    if b == 0x01 {
                        if i + 1 >= trans.len() {
                            return Err("malformed word code".into());
                        }
                        let id = trans[i + 1] as usize;
                        if id >= words.len() {
                            return Err("bad word id".into());
                        }
                        out.extend_from_slice(&words[id]);
                        i += 2;
                    } else if b == 0x02 {
                        if i + 2 >= trans.len() {
                            return Err("malformed line ref".into());
                        }
                        let id =
                            ((trans[i + 1] as usize) << 8) | (trans[i + 2] as usize);
                        if id >= dict.len() {
                            return Err("bad line id".into());
                        }
                        out.extend_from_slice(&dict[id]);
                        i += 3;
                    } else {
                        out.push(b);
                        i += 1;
                    }
                }
                if out.len() != original_len {
                    return Err("length mismatch".into());
                }
                Ok(out)
            }
            _ => Err("bad mode byte".into()),
        }
    }
}
