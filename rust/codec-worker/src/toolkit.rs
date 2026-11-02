//! Stable libzstd simple API; the generated module uses only these safe helpers.
use crate::api::MAX_PAYLOAD;
use crate::CodecError;
use std::ffi::{c_int, c_uint, c_void, CStr};

#[link(name = "libzstd.so.1", kind = "dylib", modifiers = "+verbatim")]
extern "C" {
    fn ZSTD_compressBound(size: usize) -> usize;
    fn ZSTD_compress(dst: *mut c_void, capacity: usize, src: *const c_void, size: usize, level: c_int) -> usize;
    fn ZSTD_decompress(dst: *mut c_void, capacity: usize, src: *const c_void, size: usize) -> usize;
    fn ZSTD_getFrameContentSize(src: *const c_void, size: usize) -> u64;
    fn ZSTD_isError(code: usize) -> c_uint;
    fn ZSTD_getErrorName(code: usize) -> *const std::ffi::c_char;
}

fn checked(code: usize) -> Result<usize, CodecError> {
    if unsafe { ZSTD_isError(code) } != 0 {
        return Err(unsafe { CStr::from_ptr(ZSTD_getErrorName(code)) }.to_string_lossy().into_owned());
    }
    Ok(code)
}

pub fn zstd_compress(input: &[u8], level: i32) -> Result<Vec<u8>, CodecError> {
    if input.len() > MAX_PAYLOAD || !(1..=22).contains(&level) {
        return Err("zstd input/level exceeds toolkit limits".into());
    }
    let mut out = vec![0u8; unsafe { ZSTD_compressBound(input.len()) }];
    let size = checked(unsafe { ZSTD_compress(out.as_mut_ptr().cast(), out.len(), input.as_ptr().cast(), input.len(), level) })?;
    out.truncate(size);
    Ok(out)
}

pub fn zstd_decompress(encoded: &[u8]) -> Result<Vec<u8>, CodecError> {
    let size = unsafe { ZSTD_getFrameContentSize(encoded.as_ptr().cast(), encoded.len()) };
    if size > MAX_PAYLOAD as u64 {
        return Err("zstd frame has unknown or excessive decoded size".into());
    }
    let mut out = vec![0u8; size as usize];
    let actual = checked(unsafe { ZSTD_decompress(out.as_mut_ptr().cast(), out.len(), encoded.as_ptr().cast(), encoded.len()) })?;
    if actual != out.len() {
        return Err("zstd content size mismatch".into());
    }
    Ok(out)
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

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn known_checksum_and_zstd_roundtrip() {
        assert_eq!(crc32(b"123456789"), 0xcbf43926);
        for data in [b"".as_slice(), b"abcabcabc\0\xff".as_slice()] {
            assert_eq!(zstd_decompress(&zstd_compress(data, 3).unwrap()).unwrap(), data);
        }
        assert!(zstd_decompress(b"bad frame").is_err());
    }
}
