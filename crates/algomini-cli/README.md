# Algomini Rust client

This crate builds the `algomini` binary. Approved source/model versions and human-friendly aliases come from `../../release/registry.json`; the build checks every asset SHA-256 and prohibits unsafe code in generated codecs. The zstd dependency builds its bundled native library. A Rust 1.92+ toolchain and C compiler are needed for source installation. Compression itself needs no Python, API key, compiler or network access.

```bash
cargo install --path crates/algomini-cli --locked
algomini plugins
algomini compress --plugin wiki-xml sample.xml sample.alc
algomini decompress sample.alc restored.xml
```

One file may contain at most **64 MiB**. Outputs must not already exist. Files up to 2 MiB retain the original ALC1 representation. Larger files use ALC2 with independent 2 MiB ALG2 frames, one immutable codec ID, the model stored once, original size, chunk count and a whole-file CRC32. Existing codec implementations and their per-block bounds remain unchanged. Archives store immutable IDs rather than aliases.

This is intentionally a simple in-memory implementation: it reads the complete input and assembles the complete output, with no streaming, cross-chunk state, record alignment or parallelism. Chunking can reduce compression efficiency; exact original bytes are preserved. The decoder rejects bad dimensions, missing/extra frames, incorrect lengths and checksum failures before creating the output.

Large-file support starts with client 0.3.1. Older clients cannot decode ALC2; the new client still decodes existing ALC1 archives. Existing release tags retain their original binaries and limits.

ALC2 uses a 28-byte little-endian header: magic, codec-ID length (u16), filename length (u16), model length (u32), original length (u64), chunk count (u32), whole-file CRC32 (u32). The ID, filename and model follow, then repeated u32 frame lengths and complete ALG2 frames. The final chunk may be shorter; no trailing bytes are permitted.

`__worker CODEC_ID` implements the bounded ALQ1/ALR1 protocol for independent contained evaluation. Cloud reports measure this exact executable in fresh restricted processes and separately prove the public console writes identical archive bytes and decodes after original deletion. The standalone console executes reviewed, compiled codecs; it does not install or execute downloaded plugins at runtime.

The shipped aliases initially reference the previously measured CSV and Wikipedia mechanisms. Their old local benchmark numbers are provenance, not cloud qualification of this new static runtime. A request produces its own committed cloud evidence before a release can be published.
