//! Deliberately incorrect codec used only to verify the rejection path.
use crate::{Codec, CodecError};
pub struct Candidate;
impl Codec for Candidate {
    fn encode(input: &[u8], _model: &[u8]) -> Result<Vec<u8>, CodecError> {
        // Force raw escape so a framed-only test would hide this broken decoder.
        Ok(input.to_vec())
    }
    fn decode(_encoded: &[u8], _model: &[u8], _original_len: usize) -> Result<Vec<u8>, CodecError> {
        Ok(Vec::new())
    }
}
