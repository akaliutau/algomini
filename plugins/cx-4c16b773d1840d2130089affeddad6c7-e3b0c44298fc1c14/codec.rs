use crate::{Codec, CodecError};
use std::collections::HashMap;

pub struct Candidate;

fn token_end(data: &[u8], start: usize) -> Option<usize> {
    let mut end = start;
    match data[start] {
        b'"' => {
            end += 1;
            while end < data.len() {
                match data[end] {
                    b'"' => return Some(end + 1),
                    b'\\' => end += 1,
                    _ => {}
                }
                end += 1;
            }
            None
        }
        b'-' | b'0'..=b'9' => {
            if data[end] == b'-' {
                end += 1;
            }
            if end == data.len() || !data[end].is_ascii_digit() {
                return None;
            }
            if data[end] == b'0' {
                end += 1;
            } else {
                while end < data.len() && data[end].is_ascii_digit() {
                    end += 1;
                }
            }
            if end + 1 < data.len() && data[end] == b'.' && data[end + 1].is_ascii_digit() {
                end += 2;
                while end < data.len() && data[end].is_ascii_digit() {
                    end += 1;
                }
            }
            if end < data.len() && matches!(data[end], b'e' | b'E') {
                let mut exponent = end + 1;
                if exponent < data.len() && matches!(data[exponent], b'+' | b'-') {
                    exponent += 1;
                }
                let digits = exponent;
                while exponent < data.len() && data[exponent].is_ascii_digit() {
                    exponent += 1;
                }
                if exponent > digits {
                    end = exponent;
                }
            }
            Some(end)
        }
        _ => [b"true".as_slice(), b"false", b"null"]
            .iter()
            .find(|literal| data[start..].starts_with(literal))
            .map(|literal| start + literal.len()),
    }
}

fn transpose(data: &[u8]) -> Option<Vec<u8>> {
    if data.contains(&0) || !data.contains(&b':') || !data.contains(&b'"') {
        return None;
    }
    let mut groups: HashMap<Vec<u8>, u8> = HashMap::new();
    let mut streams: Vec<Vec<u8>> = Vec::new();
    let mut layout = Vec::new();
    let (mut position, mut copied, mut previous_start, mut previous_end) = (0, 0, 0, 0);
    while position < data.len() {
        let Some(end) = token_end(data, position) else {
            if data[position] == b'"' {
                // No unescaped closing quote remains: preserve the tail once.
                // Restarting at each escaped quote would make this quadratic.
                break;
            }
            position += 1;
            continue;
        };
        let gap = &data[copied..position];
        let previous = &data[previous_start..previous_end];
        let key = if previous.starts_with(b"\"")
            && gap
                .iter()
                .copied()
                .filter(|b| !b.is_ascii_whitespace())
                .eq([b':'])
        {
            previous
        } else {
            b""
        };
        let channel = if let Some(index) = groups.get(key) {
            *index
        } else {
            if groups.len() == 255 {
                return None;
            }
            let index = groups.len() as u8;
            groups.insert(key.to_vec(), index);
            streams.push(Vec::new());
            index
        };
        layout.extend_from_slice(gap);
        layout.extend_from_slice(&[0, channel]);
        let stream = &mut streams[channel as usize];
        if !stream.is_empty() {
            stream.push(0);
        }
        stream.extend_from_slice(&data[position..end]);
        previous_start = position;
        previous_end = end;
        copied = end;
        position = end;
    }
    if streams.is_empty() {
        return None;
    }
    layout.extend_from_slice(&data[copied..]);
    let length =
        5 + 4 * (streams.len() + 1) + layout.len() + streams.iter().map(Vec::len).sum::<usize>();
    if length > 4 * 1024 * 1024 {
        return None;
    }
    let mut out = Vec::with_capacity(length);
    out.extend_from_slice(b"JCL1");
    out.push(streams.len() as u8);
    for part in std::iter::once(&layout).chain(streams.iter()) {
        out.extend_from_slice(&(part.len() as u32).to_le_bytes());
    }
    out.extend_from_slice(&layout);
    for stream in streams {
        out.extend_from_slice(&stream);
    }
    Some(out)
}

fn restore(data: &[u8], original_len: usize) -> Result<Vec<u8>, CodecError> {
    if data.len() < 9 || &data[..4] != b"JCL1" || original_len > 2 * 1024 * 1024 {
        return Err("invalid lexical header".into());
    }
    let count = data[4] as usize;
    let header = 5 + 4 * (count + 1);
    if count == 0 || header > data.len() {
        return Err("invalid lexical dimensions".into());
    }
    let mut parts = Vec::with_capacity(count + 1);
    let mut offset = header;
    for index in 0..=count {
        let size =
            u32::from_le_bytes(data[5 + index * 4..9 + index * 4].try_into().unwrap()) as usize;
        let end = offset.checked_add(size).ok_or("lexical size overflow")?;
        if end > data.len() {
            return Err("truncated lexical stream".into());
        }
        parts.push(&data[offset..end]);
        offset = end;
    }
    if offset != data.len() {
        return Err("trailing lexical bytes".into());
    }
    let mut cursors = vec![0; count];
    let layout = parts[0];
    let mut out = Vec::with_capacity(original_len);
    let mut position = 0;
    while position < layout.len() {
        if layout[position] != 0 {
            if out.len() == original_len {
                return Err("lexical output exceeds length".into());
            }
            out.push(layout[position]);
            position += 1;
            continue;
        }
        if position + 1 >= layout.len() {
            return Err("truncated lexical channel".into());
        }
        let index = layout[position + 1] as usize;
        if index >= count {
            return Err("invalid lexical channel".into());
        }
        let stream = parts[index + 1];
        let start = cursors[index];
        if start >= stream.len() {
            return Err("missing lexical value".into());
        }
        let end = stream[start..]
            .iter()
            .position(|b| *b == 0)
            .map_or(stream.len(), |n| start + n);
        if end == start || end - start > original_len - out.len() {
            return Err("invalid lexical value length".into());
        }
        out.extend_from_slice(&stream[start..end]);
        cursors[index] = end + 1;
        position += 2;
    }
    if out.len() != original_len
        || cursors
            .iter()
            .zip(&parts[1..])
            .any(|(cursor, stream)| *cursor != stream.len() + 1)
    {
        return Err("lexical length or unused values".into());
    }
    Ok(out)
}

impl Codec for Candidate {
    fn encode(input: &[u8], _model: &[u8]) -> Result<Vec<u8>, CodecError> {
        let mut best = vec![0];
        best.extend_from_slice(&crate::toolkit::zstd_compress(input, 22)?);
        if let Some(layout) = transpose(input) {
            let encoded = crate::toolkit::zstd_compress(&layout, 22)?;
            if encoded.len() + 1 < best.len() {
                best.clear();
                best.push(1);
                best.extend_from_slice(&encoded);
            }
        }
        Ok(best)
    }

    fn decode(encoded: &[u8], _model: &[u8], original_len: usize) -> Result<Vec<u8>, CodecError> {
        if encoded.is_empty() || original_len > 2 * 1024 * 1024 {
            return Err("invalid lexical frame".into());
        }
        let raw = crate::toolkit::zstd_decompress(&encoded[1..])?;
        match encoded[0] {
            0 if raw.len() == original_len => Ok(raw),
            1 => restore(&raw, original_len),
            _ => Err("invalid lexical mode or output length".into()),
        }
    }
}
