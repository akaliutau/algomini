pub const MAX_INPUT: usize = 2 * 1024 * 1024;
// The PoC holds a whole file in memory, but codecs still receive bounded blocks.
pub const MAX_FILE: usize = 64 * 1024 * 1024;
pub const MAX_ARCHIVE: usize = MAX_FILE + MAX_MODEL + 8192;
pub const MAX_PAYLOAD: usize = 4 * 1024 * 1024;
pub const MAX_MODEL: usize = 65536;
pub type CodecError = String;

pub trait Codec {
    fn train(_samples: &[&[u8]]) -> Result<Vec<u8>, CodecError> {
        Ok(Vec::new())
    }
    fn encode(input: &[u8], model: &[u8]) -> Result<Vec<u8>, CodecError>;
    fn decode(encoded: &[u8], model: &[u8], original_len: usize) -> Result<Vec<u8>, CodecError>;
}
