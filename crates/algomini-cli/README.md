# Algomini Rust client

This crate builds the `algomini` binary. Approved source/model versions and human-friendly aliases come from `../../release/registry.json`; the build checks every asset SHA-256 and prohibits unsafe code in generated codecs. The zstd dependency builds its bundled native library. A Rust 1.92+ toolchain and C compiler are needed for source installation. Compression itself needs no Python, API key, compiler or network access.

```bash
cargo install --path crates/algomini-cli --locked
algomini plugins
algomini compress --plugin wiki-xml sample.xml sample.alc
algomini decompress sample.alc restored.xml
```

One file may contain at most 2 MiB. Outputs must not already exist. ALC1 stores the immutable codec ID, complete ALG2 frame and any used shared model; aliases are never stored as the decoding identity. This first client handles single-block collections. Existing Python tools remain available for historical multi-file collections.

`__worker CODEC_ID` implements the bounded ALQ1/ALR1 protocol for independent contained evaluation. Cloud reports measure this exact executable in fresh restricted processes and separately prove the public console writes identical archive bytes and decodes after original deletion. The standalone console executes reviewed, compiled codecs; it does not install or execute downloaded plugins at runtime.

The shipped aliases initially reference the previously measured CSV and Wikipedia mechanisms. Their old local benchmark numbers are provenance, not cloud qualification of this new static runtime. A request produces its own committed cloud evidence before a release can be published.
