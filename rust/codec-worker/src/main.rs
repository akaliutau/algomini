mod api;
pub use api::{Codec, CodecError};
#[forbid(unsafe_code)]
#[path = "codec.rs"]
mod candidate;
mod frame;
mod identity;
pub mod toolkit;

use api::{MAX_INPUT, MAX_MODEL, MAX_PAYLOAD};
use candidate::Candidate;
use std::io::{Read, Write};

fn execute(request: &[u8]) -> Result<Vec<u8>, CodecError> {
    if request.len() < 17 || &request[..4] != b"ALQ1" {
        return Err("invalid worker request".into());
    }
    let number = |offset| u32::from_le_bytes(request[offset..offset + 4].try_into().unwrap()) as usize;
    let (original_len, model_len, data_len) = (number(5), number(9), number(13));
    if original_len > MAX_INPUT || model_len > MAX_MODEL || data_len > MAX_PAYLOAD + frame::HEADER
        || request.len() != 17 + model_len + data_len {
        return Err("worker request dimensions exceed limits".into());
    }
    let model = &request[17..17 + model_len];
    let data = &request[17 + model_len..];
    let out = match request[4] {
        1 => frame::encode::<Candidate>(data, model)?,
        2 => frame::decode::<Candidate>(data, model)?,
        3 => {
            let mut samples = Vec::new();
            let mut cursor = 0;
            while cursor < data.len() {
                let end = cursor.checked_add(4).ok_or("training cursor overflow")?;
                let count = u32::from_le_bytes(data.get(cursor..end).ok_or("truncated training length")?.try_into().unwrap()) as usize;
                cursor = end;
                let end = cursor.checked_add(count).ok_or("training sample overflow")?;
                samples.push(data.get(cursor..end).ok_or("truncated training sample")?);
                cursor = end;
                if samples.len() > 128 { return Err("too many training samples".into()); }
            }
            if data.len() > MAX_INPUT { return Err("training input exceeds limits".into()); }
            let model = Candidate::train(&samples)?;
            if model.len() > MAX_MODEL { return Err("trained model exceeds limits".into()); }
            model
        }
        // Direct algorithm probes prevent a broken implementation hiding behind raw escape.
        4 => {
            if data.len() > MAX_INPUT { return Err("probe input exceeds limits".into()); }
            Candidate::encode(data, model)?
        }
        5 => Candidate::decode(data, model, original_len)?,
        6 => frame::encode_shared::<Candidate>(data, model)?,
        _ => return Err("unknown worker operation".into()),
    };
    if out.len() > MAX_PAYLOAD + frame::HEADER + MAX_MODEL {
        return Err("worker output exceeds limits".into());
    }
    Ok(out)
}

fn main() {
    let mut request = Vec::new();
    let result = std::io::stdin().take((MAX_PAYLOAD + MAX_MODEL + frame::HEADER + 18) as u64)
        .read_to_end(&mut request).map_err(|error| error.to_string()).and_then(|_| execute(&request));
    let (status, output) = match result {
        Ok(output) => (0u8, output),
        Err(error) => (1u8, error.bytes().take(1024).collect()),
    };
    let mut stdout = std::io::stdout().lock();
    let _ = stdout.write_all(b"ALR1").and_then(|_| stdout.write_all(&[status]))
        .and_then(|_| stdout.write_all(&(output.len() as u32).to_le_bytes()))
        .and_then(|_| stdout.write_all(&output));
    if status != 0 { std::process::exit(1); }
}
