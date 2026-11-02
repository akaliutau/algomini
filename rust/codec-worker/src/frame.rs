use crate::api::{MAX_INPUT, MAX_MODEL, MAX_PAYLOAD};
use crate::identity::CODEC_ID;
use crate::toolkit::crc32;
use crate::{Codec, CodecError};

pub const HEADER: usize = 40;

fn number(data: &[u8], offset: usize) -> Result<usize, CodecError> {
    let bytes: [u8; 4] = data.get(offset..offset + 4).ok_or("truncated integer")?.try_into().unwrap();
    Ok(u32::from_le_bytes(bytes) as usize)
}

pub fn encode<C: Codec>(input: &[u8], model: &[u8]) -> Result<Vec<u8>, CodecError> {
    encode_mode::<C>(input, model, false)
}

pub fn encode_shared<C: Codec>(input: &[u8], model: &[u8]) -> Result<Vec<u8>, CodecError> {
    encode_mode::<C>(input, model, true)
}

fn encode_mode<C: Codec>(input: &[u8], model: &[u8], shared: bool) -> Result<Vec<u8>, CodecError> {
    if input.len() > MAX_INPUT || model.len() > MAX_MODEL {
        return Err("input/model exceeds limits".into());
    }
    let encoded = C::encode(input, model)?;
    if encoded.len() > MAX_PAYLOAD {
        return Err("candidate output exceeds limits".into());
    }
    let raw = encoded.len().checked_add(if shared { 0 } else { model.len() }).ok_or("length overflow")? >= input.len();
    let external = !raw && shared && !model.is_empty();
    let (payload, used_model) = if raw { (input, &[][..]) } else { (encoded.as_slice(), if shared { &[][..] } else { model }) };
    let mut out = Vec::with_capacity(HEADER + used_model.len() + payload.len());
    out.extend_from_slice(b"ALG2");
    out.extend_from_slice(&[if external { 2 } else { u8::from(raw) }, 0, 0, 0]);
    for size in [input.len(), used_model.len(), payload.len()] {
        out.extend_from_slice(&(size as u32).to_le_bytes());
    }
    out.extend_from_slice(&crc32(input).to_le_bytes());
    out.extend_from_slice(if raw { &[0; 16] } else { &CODEC_ID });
    out.extend_from_slice(used_model);
    out.extend_from_slice(payload);
    Ok(out)
}

pub fn decode<C: Codec>(frame: &[u8], shared: &[u8]) -> Result<Vec<u8>, CodecError> {
    if frame.len() < HEADER || (frame[..4] != *b"ALG1" && frame[..4] != *b"ALG2")
        || frame[4] > 2 || (&frame[..4] == b"ALG1" && frame[4] == 2) || frame[5..8] != [0; 3] {
        return Err("invalid archive header".into());
    }
    let (original, model_len, payload_len) = (number(frame, 8)?, number(frame, 12)?, number(frame, 16)?);
    if original > MAX_INPUT || model_len > MAX_MODEL || payload_len > MAX_PAYLOAD
        || frame.len() != HEADER + model_len + payload_len {
        return Err("archive dimensions exceed limits or do not match input".into());
    }
    let model = if frame[4] == 2 {
        if model_len != 0 || shared.is_empty() || shared.len() > MAX_MODEL {
            return Err("missing or invalid shared model".into());
        }
        shared
    } else { &frame[HEADER..HEADER + model_len] };
    let payload = &frame[HEADER + model_len..];
    let out = if frame[4] == 1 {
        if model_len != 0 || frame[24..40] != [0; 16] {
            return Err("invalid raw frame".into());
        }
        payload.to_vec()
    } else {
        if frame[24..40] != CODEC_ID {
            return Err("archive belongs to a different codec".into());
        }
        C::decode(payload, model, original)?
    };
    if out.len() != original || crc32(&out) != number(frame, 20)? as u32 {
        return Err("decoded length/checksum mismatch".into());
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::candidate::Candidate;
    #[test]
    fn raw_frame_preserves_arbitrary_bytes_and_rejects_corruption() {
        let data = b"arbitrary\0\xff\r\n";
        let mut encoded = encode::<Candidate>(data, &[]).unwrap();
        assert_eq!(encoded.len(), data.len() + HEADER);
        assert_eq!(decode::<Candidate>(&encoded, &[]).unwrap(), data);
        let mut legacy = encoded.clone();
        legacy[..4].copy_from_slice(b"ALG1");
        assert_eq!(decode::<Candidate>(&legacy, &[]).unwrap(), data);
        *encoded.last_mut().unwrap() ^= 1;
        assert!(decode::<Candidate>(&encoded, &[]).is_err());
        assert!(decode::<Candidate>(&encoded[..20], &[]).is_err());
    }

    struct Modeled;
    impl Codec for Modeled {
        fn encode(input: &[u8], model: &[u8]) -> Result<Vec<u8>, CodecError> {
            if model != b"model" { return Err("model".into()); }
            crate::toolkit::zstd_compress(input, 1)
        }
        fn decode(input: &[u8], model: &[u8], _: usize) -> Result<Vec<u8>, CodecError> {
            if model != b"model" { return Err("model".into()); }
            crate::toolkit::zstd_decompress(input)
        }
    }

    #[test]
    fn external_model_and_dimensions_are_checked() {
        let input = vec![b'a'; 1000];
        let encoded = encode_shared::<Modeled>(&input, b"model").unwrap();
        assert_eq!(encoded[4], 2);
        assert_eq!(decode::<Modeled>(&encoded, b"model").unwrap(), input);
        assert!(decode::<Modeled>(&encoded, b"").is_err());
        assert!(decode::<Modeled>(&encoded, b"wrong").is_err());
        for offset in [4, 5, 8, 12, 16, 20, 24] {
            let mut bad = encoded.clone();
            bad[offset] ^= 0xff;
            assert!(decode::<Modeled>(&bad, b"model").is_err());
        }
        for end in 0..encoded.len() {
            assert!(decode::<Modeled>(&encoded[..end], b"model").is_err());
        }
    }
}
