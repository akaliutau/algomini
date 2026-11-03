use crate::{api::MAX_PAYLOAD, CodecError};

pub fn zstd_compress(input: &[u8], level: i32) -> Result<Vec<u8>, CodecError> {
    if input.len() > MAX_PAYLOAD || !(1..=22).contains(&level) {
        return Err("zstd input/level exceeds limits".into());
    }
    zstd::bulk::compress(input, level).map_err(|e| e.to_string())
}

pub fn zstd_decompress(input: &[u8]) -> Result<Vec<u8>, CodecError> {
    let size = zstd::zstd_safe::get_frame_content_size(input)
        .map_err(|_| "invalid zstd header")?
        .ok_or("unknown zstd content size")?;
    if size > MAX_PAYLOAD as u64 {
        return Err("zstd output exceeds limits".into());
    }
    let result = zstd::bulk::decompress(input, size as usize).map_err(|e| e.to_string())?;
    if result.len() != size as usize {
        return Err("zstd size mismatch".into());
    }
    Ok(result)
}

pub fn crc32(data: &[u8]) -> u32 {
    let mut crc = !0u32;
    for byte in data {
        crc ^= *byte as u32;
        for _ in 0..8 {
            crc = (crc >> 1) ^ (0xedb88320 & 0u32.wrapping_sub(crc & 1));
        }
    }
    !crc
}
