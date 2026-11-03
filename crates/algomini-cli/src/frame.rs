use crate::{
    api::{MAX_INPUT, MAX_MODEL, MAX_PAYLOAD},
    toolkit::crc32,
    CodecError, CodecInfo,
};
pub const HEADER: usize = 40;

pub fn number(data: &[u8], offset: usize) -> Result<usize, CodecError> {
    Ok(u32::from_le_bytes(
        data.get(offset..offset + 4)
            .ok_or("truncated integer")?
            .try_into()
            .unwrap(),
    ) as usize)
}

pub fn encode(
    codec: &CodecInfo,
    data: &[u8],
    model: &[u8],
    shared: bool,
) -> Result<Vec<u8>, CodecError> {
    if data.len() > MAX_INPUT || model.len() > MAX_MODEL {
        return Err("input/model exceeds limits".into());
    }
    let coded = (codec.encode)(data, model)?;
    if coded.len() > MAX_PAYLOAD {
        return Err("encoded payload exceeds limits".into());
    }
    let raw = coded.len() + if shared { 0 } else { model.len() } >= data.len();
    let flag = if raw {
        1
    } else if shared && !model.is_empty() {
        2
    } else {
        0
    };
    let stored = if raw || shared { &[][..] } else { model };
    let payload = if raw { data } else { &coded };
    let mut out = Vec::with_capacity(HEADER + stored.len() + payload.len());
    out.extend_from_slice(b"ALG2");
    out.extend_from_slice(&[flag, 0, 0, 0]);
    for value in [
        data.len(),
        stored.len(),
        payload.len(),
        crc32(data) as usize,
    ] {
        out.extend_from_slice(&(value as u32).to_le_bytes());
    }
    out.extend_from_slice(if raw { &[0; 16] } else { &codec.identity });
    out.extend_from_slice(stored);
    out.extend_from_slice(payload);
    Ok(out)
}

pub fn decode(codec: &CodecInfo, input: &[u8], shared: &[u8]) -> Result<Vec<u8>, CodecError> {
    if input.len() < HEADER
        || !matches!(&input[..4], b"ALG1" | b"ALG2")
        || input[4] > 2
        || input[5..8] != [0; 3]
        || (&input[..4] == b"ALG1" && input[4] == 2)
    {
        return Err("invalid frame".into());
    }
    let (length, model_len, payload_len) =
        (number(input, 8)?, number(input, 12)?, number(input, 16)?);
    if length > MAX_INPUT
        || model_len > MAX_MODEL
        || payload_len > MAX_PAYLOAD
        || input.len() != HEADER + model_len + payload_len
    {
        return Err("invalid frame dimensions".into());
    }
    let model = if input[4] == 2 {
        if model_len != 0 || shared.is_empty() || shared.len() > MAX_MODEL {
            return Err("missing shared model".into());
        }
        shared
    } else {
        &input[HEADER..HEADER + model_len]
    };
    let payload = &input[HEADER + model_len..];
    let decoded = if input[4] == 1 {
        if model_len != 0 || input[24..40] != [0; 16] || payload_len != length {
            return Err("invalid raw frame".into());
        }
        payload.to_vec()
    } else {
        if input[24..40] != codec.identity {
            return Err("wrong codec identity".into());
        }
        (codec.decode)(payload, model, length)?
    };
    if decoded.len() != length || crc32(&decoded) != number(input, 20)? as u32 {
        return Err("decoded length/checksum mismatch".into());
    }
    Ok(decoded)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::CODECS;
    #[test]
    fn all_codecs_roundtrip_and_reject_corruption() {
        for codec in CODECS {
            for data in [
                vec![],
                vec![0],
                (0..=255).collect(),
                b"a,b\r\n1.00,-0\r\n".repeat(30),
            ] {
                let encoded = encode(codec, &data, codec.model, true).unwrap();
                assert_eq!(decode(codec, &encoded, codec.model).unwrap(), data);
                for end in [0, 3, 20, encoded.len() - 1] {
                    assert!(decode(codec, &encoded[..end], codec.model).is_err());
                }
                for offset in [4, 5, 8, 12, 16, 20, 24] {
                    let mut bad = encoded.clone();
                    bad[offset] ^= 255;
                    assert!(decode(codec, &bad, codec.model).is_err());
                }
            }
        }
    }
}
