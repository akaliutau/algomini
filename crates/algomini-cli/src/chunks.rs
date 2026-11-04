//! Simple in-memory large-file wrapper around the existing independent frames.

use crate::{api::*, frame, label_valid, toolkit::crc32, CodecInfo, CODECS};

pub fn pack(codec: &CodecInfo, label: &str, data: &[u8]) -> Result<Vec<u8>, CodecError> {
    if !label_valid(label) || data.len() <= MAX_INPUT || data.len() > MAX_FILE {
        return Err("invalid chunked file size or label".into());
    }
    let mut out = b"ALC2".to_vec();
    out.extend_from_slice(&(codec.id.len() as u16).to_le_bytes());
    out.extend_from_slice(&(label.len() as u16).to_le_bytes());
    out.extend_from_slice(&(codec.model.len() as u32).to_le_bytes());
    out.extend_from_slice(&(data.len() as u64).to_le_bytes());
    out.extend_from_slice(&(data.len().div_ceil(MAX_INPUT) as u32).to_le_bytes());
    out.extend_from_slice(&crc32(data).to_le_bytes());
    out.extend_from_slice(codec.id.as_bytes());
    out.extend_from_slice(label.as_bytes());
    out.extend_from_slice(codec.model);
    for chunk in data.chunks(MAX_INPUT) {
        let encoded = frame::encode(codec, chunk, codec.model, true)?;
        out.extend_from_slice(&(encoded.len() as u32).to_le_bytes());
        out.extend_from_slice(&encoded);
    }
    Ok(out)
}

fn take<'a>(input: &'a [u8], cursor: &mut usize, size: usize) -> Result<&'a [u8], CodecError> {
    let end = cursor.checked_add(size).ok_or("chunk length overflow")?;
    let result = input.get(*cursor..end).ok_or("truncated chunked file")?;
    *cursor = end;
    Ok(result)
}

pub fn unpack(input: &[u8]) -> Result<Vec<u8>, CodecError> {
    if input.len() < 28 || input.len() > MAX_ARCHIVE || &input[..4] != b"ALC2" {
        return Err("invalid chunked file header".into());
    }
    let name_len = u16::from_le_bytes(input[4..6].try_into().unwrap()) as usize;
    let label_len = u16::from_le_bytes(input[6..8].try_into().unwrap()) as usize;
    let model_len = frame::number(input, 8)?;
    let size = u64::from_le_bytes(input[12..20].try_into().unwrap());
    let count = frame::number(input, 20)?;
    let checksum = frame::number(input, 24)? as u32;
    if !(1..=64).contains(&name_len)
        || !(1..=256).contains(&label_len)
        || model_len > MAX_MODEL
        || size <= MAX_INPUT as u64
        || size > MAX_FILE as u64
        || count != (size as usize).div_ceil(MAX_INPUT)
    {
        return Err("invalid chunked file dimensions".into());
    }
    let mut cursor = 28;
    let name = std::str::from_utf8(take(input, &mut cursor, name_len)?)
        .map_err(|_| "invalid codec name")?;
    let codec = CODECS
        .iter()
        .find(|row| row.id == name)
        .ok_or("codec version is not installed")?;
    let label = std::str::from_utf8(take(input, &mut cursor, label_len)?)
        .map_err(|_| "invalid archive label")?;
    if !label_valid(label) {
        return Err("invalid archive label".into());
    }
    let model = take(input, &mut cursor, model_len)?;
    let mut out = Vec::new();
    for _ in 0..count {
        let length = u32::from_le_bytes(take(input, &mut cursor, 4)?.try_into().unwrap()) as usize;
        if length > MAX_PAYLOAD + frame::HEADER {
            return Err("chunk exceeds the frame limit".into());
        }
        let decoded = frame::decode(codec, take(input, &mut cursor, length)?, model)?;
        if decoded.len() != MAX_INPUT.min(size as usize - out.len()) {
            return Err("decoded chunk length mismatch".into());
        }
        out.extend_from_slice(&decoded);
    }
    if cursor != input.len() || out.len() != size as usize || crc32(&out) != checksum {
        return Err("chunked file length/checksum mismatch".into());
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn independent_chunks_roundtrip_and_reject_corruption_reordering_and_truncation() {
        let codec = crate::resolve("general").unwrap();
        let mut data = vec![b'a'; MAX_INPUT];
        data.extend(vec![b'b'; MAX_INPUT]);
        let packed = pack(codec, "large.bin", &data).unwrap();
        assert_eq!(unpack(&packed).unwrap(), data);
        assert_eq!(crate::unpack(&packed).unwrap(), data);
        for end in [0, 27, packed.len() - 1] {
            assert!(crate::unpack(&packed[..end]).is_err());
        }
        for offset in [4, 6, 8, 12, 19, 20, 24, packed.len() - 1] {
            let mut bad = packed.clone();
            bad[offset] ^= 0xff;
            assert!(unpack(&bad).is_err());
        }
        let start = 28 + codec.id.len() + "large.bin".len() + codec.model.len();
        let length = frame::number(&packed, start).unwrap();
        let split = start + 4 + length;
        let mut reordered = packed[..start].to_vec();
        reordered.extend_from_slice(&packed[split..]);
        reordered.extend_from_slice(&packed[start..split]);
        assert!(unpack(&reordered).is_err());
        let mut trailing = packed;
        trailing.push(0);
        assert!(unpack(&trailing).is_err());
    }

    #[test]
    fn chunked_header_rejects_oversize_before_allocation() {
        let mut header = vec![0; 28];
        header[..4].copy_from_slice(b"ALC2");
        header[4..6].copy_from_slice(&1u16.to_le_bytes());
        header[6..8].copy_from_slice(&1u16.to_le_bytes());
        header[12..20].copy_from_slice(&u64::MAX.to_le_bytes());
        assert!(unpack(&header).is_err());
    }
}
