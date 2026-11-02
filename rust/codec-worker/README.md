# Fixed codec worker, protocol 1 / archive 2

The trusted Cargo wrapper has no third-party Rust dependencies. A build copies it into an isolated cache, replaces only `src/codec.rs` and the host-generated `CODEC_ID` constant, and compiles offline with the locked Cargo manifest. The candidate source, wrapper, compiler version, and execution-policy hashes identify each build. Generated modules use `forbid(unsafe_code)`; the toolkit owns the small stable libzstd FFI boundary.

Each codec exports `Candidate` implementing `Codec` from `api.rs`. `train(&[&[u8]])` defaults to an empty model. `encode(input, model)` and `decode(encoded, model, original_len)` are required. Inputs are capped at 2 MiB, candidate payloads at 4 MiB, and serialized models at 64 KiB. The caller supplies only training samples to training.

Safe helpers are `crate::toolkit::zstd_compress(input, level)` and `zstd_decompress(encoded)`. The latter requires a known frame content size and rejects output above 4 MiB. The worker links the system `libzstd.so.1` by its exact soname, so no development header or extra Cargo dependency is needed.

## Worker request and response

All integers are unsigned little-endian. One invocation reads one bounded request from stdin and writes one response to stdout; decode runs in a fresh restricted process.

| Request field | Bytes |
|---|---:|
| `ALQ1` magic | 4 |
| Operation | 1 |
| Original length | 4 |
| Model length | 4 |
| Data length | 4 |
| Model, then data | Declared lengths |

Operations are `1` standalone framed encode, `2` framed decode, `3` train, `4` direct algorithm encode, `5` direct algorithm decode, and `6` framed encode with an external shared model. Training data is a sequence of 4-byte sample lengths followed by their bytes, with at most 128 samples and a combined 2 MiB limit. Direct operations exercise candidate logic without raw escape. Operation 2 receives the shared model in the request's model field when a frame requires one.

Responses contain `ALR1` (4 bytes), status (1 byte), payload length (4 bytes), and payload. Status zero means success; status one contains a bounded UTF-8 error and a nonzero process exit. The supervisor validates the response independently.

## Counted archive frame

| Field | Bytes |
|---|---:|
| `ALG2` magic | 4 |
| Flag, then three reserved zero bytes | 4 |
| Original, model, and payload lengths | 12 |
| CRC32 of original bytes | 4 |
| Codec ID: first 16 bytes of the source SHA-256 | 16 |
| Model, then payload | Declared lengths |

The fixed header is **40 bytes**. Flag 0 embeds the model, flag 1 is raw escape, and flag 2 requires a nonempty external model and zero embedded model length. Models and transform metadata count toward the archive. Raw escape stores the original bytes, no model, and an all-zero codec ID. Compressed mode binds to the source ID compiled by the trusted build helper. Standalone encoding selects raw escape if payload plus embedded model is no smaller than the input. Shared encoding compares payload with input because the collection stores its shared model once.

Decode also accepts legacy `ALG1` frames with flags 0/1. New writers always emit `ALG2`. Decode rejects invalid dimensions, reserved flags, wrong codec IDs, incorrect output lengths, and checksum mismatches. The host repeats structural, length, source-ID and CRC checks independently of candidate stdout. CRC32 detects corruption; it provides no authenticity guarantee.

The common counted `ALC1` collection holds the selected method id, block labels/lengths, shared model, and complete block frames. See [`docs/evaluator.md`](../../docs/evaluator.md) for exact layout, corpus scoring and native baseline accounting.

Local execution uses Landlock and libseccomp. Worker processes receive only their request and a read-only executable/library set; they cannot read original-file contents, create network sockets, fork, create threads, or write arbitrary files. Builders can write their own cache and use local socketpairs for Cargo IPC. Trusted compression CLIs may create threads. Path metadata remains visible; only the coder receives root directory enumeration required by its native file mediator. Trusted source/config/runtime hashes are pinned in the Stage 2 evaluator freeze. No local Docker execution is used.
