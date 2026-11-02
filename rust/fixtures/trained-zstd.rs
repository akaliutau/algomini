// Handwritten fixture: its nonempty training model is required for decoding.
use crate::{Codec, CodecError};
pub struct Candidate;
impl Codec for Candidate {
    fn train(samples: &[&[u8]]) -> Result<Vec<u8>, CodecError> {
        Ok(samples.first().filter(|sample| !sample.is_empty()).map(|sample| sample[..sample.len().min(31)].to_vec())
            .unwrap_or_else(|| b"default".to_vec()))
    }
    fn encode(input: &[u8], model: &[u8]) -> Result<Vec<u8>, CodecError> {
        if model.is_empty() { return Err("model required".into()); }
        let masked: Vec<u8> = input.iter().enumerate().map(|(i, byte)| byte ^ model[i % model.len()]).collect();
        crate::toolkit::zstd_compress(&masked, 1)
    }
    fn decode(input: &[u8], model: &[u8], original_len: usize) -> Result<Vec<u8>, CodecError> {
        if model.is_empty() { return Err("model required".into()); }
        let masked = crate::toolkit::zstd_decompress(input)?;
        if masked.len() != original_len { return Err("length mismatch".into()); }
        Ok(masked.iter().enumerate().map(|(i, byte)| byte ^ model[i % model.len()]).collect())
    }
}
