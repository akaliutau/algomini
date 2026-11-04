use sha2::{Digest, Sha256};
use std::{
    collections::BTreeSet,
    env, fs,
    path::{Path, PathBuf},
};

fn checked(root: &Path, value: &str, limit: u64) -> PathBuf {
    let relative = Path::new(value);
    assert!(
        !relative.is_absolute()
            && relative
                .components()
                .all(|p| matches!(p, std::path::Component::Normal(_))),
        "invalid asset path"
    );
    let path = root.join(relative);
    let canonical = path.canonicalize().expect("missing registry asset");
    assert!(
        canonical.starts_with(root.join("plugins")) && path == canonical,
        "asset must be a regular nonsymlink plugin file"
    );
    assert!(
        path.is_file() && path.metadata().unwrap().len() <= limit,
        "asset exceeds bounds"
    );
    println!("cargo:rerun-if-changed={}", path.display());
    path
}

fn main() {
    let root = Path::new(&env::var("CARGO_MANIFEST_DIR").unwrap())
        .join("../..")
        .canonicalize()
        .unwrap();
    let registry = root.join("release/registry.json");
    println!("cargo:rerun-if-changed={}", registry.display());
    let raw = fs::read(&registry).expect("release registry is required");
    let config: serde_json::Value = serde_json::from_slice(&raw).unwrap();
    assert_eq!(config["schema_version"], 1);
    let codecs = config["codecs"].as_array().expect("codec array");
    assert!(codecs.len() <= 128);
    let mut ids = BTreeSet::new();
    let mut modules = String::new();
    let mut rows = String::from("static CODECS: &[CodecInfo] = &[\n");
    for (index, item) in codecs.iter().enumerate() {
        let id = item["id"].as_str().unwrap();
        assert!(
            !id.is_empty()
                && id.len() <= 64
                && id
                    .bytes()
                    .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-')
        );
        assert!(ids.insert(id.to_string()), "duplicate codec id");
        let source = checked(&root, item["source"].as_str().unwrap(), 65536);
        let code = fs::read(&source).unwrap();
        assert!(!code.is_empty());
        let hash = format!("{:x}", Sha256::digest(&code));
        assert_eq!(item["source_sha256"].as_str().unwrap(), hash);
        let model = checked(&root, item["model"].as_str().unwrap(), 65536);
        assert_eq!(
            item["model_sha256"].as_str().unwrap(),
            format!("{:x}", Sha256::digest(fs::read(&model).unwrap()))
        );
        assert_eq!(item["codec_id"].as_str().unwrap(), &hash[..32]);
        let identity: Vec<u8> = (0..32)
            .step_by(2)
            .map(|p| u8::from_str_radix(&hash[p..p + 2], 16).unwrap())
            .collect();
        modules.push_str(&format!(
            "#[allow(dead_code)] #[forbid(unsafe_code)] mod codec_{index} {{ include!({:?}); }}\n",
            source
        ));
        rows.push_str(&format!("CodecInfo {{ id: {id:?}, identity: {identity:?}, model: include_bytes!({model:?}), encode: codec_{index}::Candidate::encode, decode: codec_{index}::Candidate::decode, train: codec_{index}::Candidate::train }},\n"));
    }
    rows.push_str("];\nstatic ALIASES: &[(&str, &str)] = &[\n");
    for (alias, id) in config["aliases"].as_object().unwrap() {
        assert!(
            !alias.is_empty()
                && alias.len() <= 64
                && alias
                    .bytes()
                    .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-')
        );
        let id = id.as_str().unwrap();
        assert!(ids.contains(id), "alias points to unknown codec");
        rows.push_str(&format!("({alias:?}, {id:?}),\n"));
    }
    rows.push_str("];\n");
    fs::write(
        Path::new(&env::var("OUT_DIR").unwrap()).join("registry.rs"),
        modules + &rows,
    )
    .unwrap();
}
