use crate::{Codec, CodecError};

pub struct Candidate;

impl Codec for Candidate {
    fn encode(input: &[u8], _model: &[u8]) -> Result<Vec<u8>, CodecError> {
        Ok(input.to_vec())
    }
    fn decode(encoded: &[u8], _model: &[u8], _original_len: usize) -> Result<Vec<u8>, CodecError> {
        Ok(encoded.to_vec())
    }
}
