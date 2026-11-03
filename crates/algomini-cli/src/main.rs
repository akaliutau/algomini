#![forbid(unsafe_code)]
mod api;
mod frame;
pub mod toolkit;
pub use api::{Codec, CodecError};
use api::{MAX_INPUT, MAX_MODEL, MAX_PAYLOAD};
use std::{
    env,
    fs::{File, OpenOptions},
    io::{Read, Write},
    path::Path,
};

struct CodecInfo {
    id: &'static str,
    identity: [u8; 16],
    model: &'static [u8],
    encode: fn(&[u8], &[u8]) -> Result<Vec<u8>, CodecError>,
    decode: fn(&[u8], &[u8], usize) -> Result<Vec<u8>, CodecError>,
    train: fn(&[&[u8]]) -> Result<Vec<u8>, CodecError>,
}
include!(concat!(env!("OUT_DIR"), "/registry.rs"));

fn resolve(name: &str) -> Result<&'static CodecInfo, CodecError> {
    let id = ALIASES
        .iter()
        .find(|(alias, _)| *alias == name)
        .map(|(_, id)| *id)
        .unwrap_or(name);
    CODECS
        .iter()
        .find(|codec| codec.id == id)
        .ok_or_else(|| format!("unknown codec: {name}"))
}

fn label_valid(label: &str) -> bool {
    !label.is_empty()
        && label != "."
        && label != ".."
        && !label.contains(['/', '\\', '\0'])
        && label.len() <= 256
}

fn pack(codec: &CodecInfo, label: &str, data: &[u8]) -> Result<Vec<u8>, CodecError> {
    if !label_valid(label) {
        return Err("invalid archive label".into());
    }
    let encoded = frame::encode(codec, data, codec.model, true)?;
    let model = if encoded[4] == 2 { codec.model } else { &[] };
    let mut out = b"ALC1".to_vec();
    out.extend_from_slice(&(codec.id.len() as u16).to_le_bytes());
    out.extend_from_slice(&0u16.to_le_bytes());
    out.extend_from_slice(&1u32.to_le_bytes());
    out.extend_from_slice(&(model.len() as u32).to_le_bytes());
    out.extend_from_slice(codec.id.as_bytes());
    out.extend_from_slice(model);
    out.extend_from_slice(&(label.len() as u16).to_le_bytes());
    out.extend_from_slice(label.as_bytes());
    out.extend_from_slice(&(encoded.len() as u32).to_le_bytes());
    out.extend_from_slice(&encoded);
    Ok(out)
}

fn unpack(input: &[u8]) -> Result<Vec<u8>, CodecError> {
    if input.len() < 16
        || &input[..4] != b"ALC1"
        || input[6..8] != [0; 2]
        || frame::number(input, 8)? != 1
    {
        return Err("expected one bounded ALC1 block".into());
    }
    let name_len = u16::from_le_bytes(input[4..6].try_into().unwrap()) as usize;
    let model_len = frame::number(input, 12)?;
    if name_len == 0 || name_len > 64 || model_len > MAX_MODEL {
        return Err("invalid collection dimensions".into());
    }
    let mut cursor = 16;
    fn take<'a>(
        input: &'a [u8],
        cursor: &mut usize,
        length: usize,
    ) -> Result<&'a [u8], CodecError> {
        let end = cursor
            .checked_add(length)
            .ok_or("collection length overflow")?;
        let bytes = input.get(*cursor..end).ok_or("truncated collection")?;
        *cursor = end;
        Ok(bytes)
    }
    let name = std::str::from_utf8(take(input, &mut cursor, name_len)?)
        .map_err(|_| "invalid codec name")?;
    // Archive names resolve immutable IDs only; aliases may change between releases.
    let codec = CODECS
        .iter()
        .find(|codec| codec.id == name)
        .ok_or("codec version is not installed")?;
    let model = take(input, &mut cursor, model_len)?;
    let label_len = u16::from_le_bytes(take(input, &mut cursor, 2)?.try_into().unwrap()) as usize;
    let label = std::str::from_utf8(take(input, &mut cursor, label_len)?)
        .map_err(|_| "invalid label encoding")?;
    if !label_valid(label) {
        return Err("invalid archive label".into());
    }
    let size = u32::from_le_bytes(take(input, &mut cursor, 4)?.try_into().unwrap()) as usize;
    if size > MAX_PAYLOAD + MAX_MODEL + frame::HEADER {
        return Err("encoded block exceeds limits".into());
    }
    let block = take(input, &mut cursor, size)?;
    if cursor != input.len() {
        return Err("trailing collection bytes".into());
    }
    frame::decode(codec, block, model)
}

fn read(path: &str, limit: usize) -> Result<Vec<u8>, CodecError> {
    let mut data = Vec::new();
    File::open(path)
        .map_err(|e| e.to_string())?
        .take(limit as u64 + 1)
        .read_to_end(&mut data)
        .map_err(|e| e.to_string())?;
    if data.len() > limit {
        return Err("input exceeds the bounded file limit".into());
    }
    Ok(data)
}

fn save(path: &str, data: &[u8]) -> Result<(), CodecError> {
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(path)
        .map_err(|e| e.to_string())?;
    file.write_all(data).map_err(|e| e.to_string())
}

fn worker(codec: &CodecInfo, input: &[u8]) -> Result<Vec<u8>, CodecError> {
    if input.len() < 17 || &input[..4] != b"ALQ1" {
        return Err("invalid worker request".into());
    }
    let (length, model_len, data_len) = (
        frame::number(input, 5)?,
        frame::number(input, 9)?,
        frame::number(input, 13)?,
    );
    if length > MAX_INPUT
        || model_len > MAX_MODEL
        || data_len > MAX_PAYLOAD + frame::HEADER
        || input.len() != 17 + model_len + data_len
    {
        return Err("invalid worker dimensions".into());
    }
    let model = &input[17..17 + model_len];
    let data = &input[17 + model_len..];
    let result = match input[4] {
        1 => frame::encode(codec, data, model, false),
        2 => frame::decode(codec, data, model),
        3 => {
            if data.len() > MAX_INPUT {
                return Err("training input exceeds limits".into());
            }
            let mut samples = Vec::new();
            let mut position = 0;
            while position < data.len() {
                let size = frame::number(data, position)?;
                position += 4;
                let end = position
                    .checked_add(size)
                    .ok_or("training length overflow")?;
                samples.push(data.get(position..end).ok_or("truncated training sample")?);
                position = end;
                if samples.len() > 128 {
                    return Err("too many training samples".into());
                }
            }
            (codec.train)(&samples)
        }
        4 => {
            if data.len() > MAX_INPUT {
                return Err("probe exceeds limits".into());
            }
            (codec.encode)(data, model)
        }
        5 => (codec.decode)(data, model, length),
        6 => frame::encode(codec, data, model, true),
        _ => Err("unknown worker operation".into()),
    }?;
    if result.len() > MAX_PAYLOAD + MAX_MODEL + frame::HEADER
        || (input[4] == 3 && result.len() > MAX_MODEL)
    {
        return Err("worker output exceeds limits".into());
    }
    Ok(result)
}

fn run(args: &[String]) -> Result<(), CodecError> {
    match args.iter().map(String::as_str).collect::<Vec<_>>().as_slice() {
        ["plugins"] => { for (alias,id) in ALIASES { println!("{alias}\t{id}"); } },
        ["--version"] => println!("algomini {}",env!("CARGO_PKG_VERSION")),
        ["compress","--plugin",name,input,output] => {
            let codec=resolve(name)?; let data=read(input,MAX_INPUT)?;
            let label=Path::new(input).file_name().and_then(|v|v.to_str()).ok_or("invalid input filename")?;
            let packed=pack(codec,label,&data)?; save(output,&packed)?;
            println!("{} -> {} bytes ({})",data.len(),packed.len(),codec.id);
        },
        ["decompress",input,output] => { let packed=read(input,MAX_PAYLOAD+MAX_MODEL+1024)?; let data=unpack(&packed)?; save(output,&data)?; println!("decoded {} bytes",data.len()); },
        _ => return Err("usage: algomini plugins | compress --plugin NAME INPUT OUTPUT | decompress ARCHIVE OUTPUT | --version".into())
    }
    Ok(())
}

fn main() {
    let args: Vec<String> = env::args().skip(1).collect();
    if args.len() == 2 && args[0] == "__worker" {
        let mut data = Vec::new();
        let result = std::io::stdin()
            .take((MAX_PAYLOAD + MAX_MODEL + frame::HEADER + 18) as u64)
            .read_to_end(&mut data)
            .map_err(|e| e.to_string())
            .and_then(|_| resolve(&args[1]))
            .and_then(|codec| worker(codec, &data));
        let (status, output) = match result {
            Ok(v) => (0u8, v),
            Err(e) => (1, e.into_bytes().into_iter().take(1024).collect()),
        };
        let mut out = std::io::stdout().lock();
        let _ = out
            .write_all(b"ALR1")
            .and_then(|_| out.write_all(&[status]))
            .and_then(|_| out.write_all(&(output.len() as u32).to_le_bytes()))
            .and_then(|_| out.write_all(&output));
        if status != 0 {
            std::process::exit(1);
        }
    } else if let Err(error) = run(&args) {
        eprintln!("algomini: {error}");
        std::process::exit(1);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn complete_collection_is_exact_and_strict() {
        let data = b"<record>the quick brown fox</record>\n".repeat(50);
        for codec in CODECS {
            let packed = pack(codec, "sample.xml", &data).unwrap();
            assert_eq!(unpack(&packed).unwrap(), data);
            for end in [0, 10, packed.len() - 1] {
                assert!(unpack(&packed[..end]).is_err());
            }
            let mut bad = packed.clone();
            bad.push(0);
            assert!(unpack(&bad).is_err());
            assert!(pack(codec, "../escape", &data).is_err());
        }
    }
    #[test]
    fn worker_preserves_direct_and_framed_protocols() {
        for codec in CODECS {
            let data = b"one two one two\r\n".repeat(50);
            for (encode, decode) in [(1u8, 2u8), (4, 5), (6, 2)] {
                let request = |op, payload: &[u8]| {
                    let mut out = b"ALQ1".to_vec();
                    out.push(op);
                    for n in [data.len(), 0, payload.len()] {
                        out.extend_from_slice(&(n as u32).to_le_bytes());
                    }
                    out.extend_from_slice(payload);
                    out
                };
                let encoded = worker(codec, &request(encode, &data)).unwrap();
                assert_eq!(worker(codec, &request(decode, &encoded)).unwrap(), data);
            }
        }
    }
}
