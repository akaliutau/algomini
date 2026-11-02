# Frozen development evaluator

Stage 2 fixes the measurement contract before broad search. Run from the dedicated `algomini` environment:

```bash
python -m algomini stage2
python -m algomini.evaluator freeze-status
python -m algomini.evaluator plugins
python -m algomini.evaluator evaluate muse-telemetry-v1 --split validation
```

`stage2` creates the freeze only when absent and then runs acceptance. Replays verify the existing freeze and repeat measurements without inference. The evaluator accepts only `train` or `validation`. It never reads the private sealed manifest, sealed chunks, or full cached source. The public split manifest's existing sealed commitment is carried into the report. Dataset preparation is a separate trusted operation.

`run.toml` fixes first/middle/last development chunks, each capped at 256 KiB, and three fresh decode processes per chunk. The dictionary and candidate trainers get the same sixteen bounded training samples, starting at training chunk 1, with combined input below 2 MiB. Validation bytes never train models. Stage 3 can analyze other development data, but the fixed probe workload and scoring policy remain pinned. Full held-out session evaluation belongs to Stage 5 and needs a separately reviewed trusted entry point; this CLI exposes no test-data access.

## Bytes and scoring

Every method produces an actual `ALC1` collection. All integers are unsigned little-endian:

| Field | Bytes |
|---|---:|
| Magic `ALC1` | 4 |
| Method name length | 2 |
| Reserved zero | 2 |
| Block count | 4 |
| Shared model length | 4 |
| ASCII method id | Declared name length |
| Shared model | Declared model length |
| Repeated: UTF-8 label length, label, block length, block bytes | 2 + label length + 4 + block length |

Names are at most 64 bytes; model at most 64 KiB; count at most 128. Labels are unique basenames of at most 256 UTF-8 bytes. A block is at most 4 MiB + 64 KiB + 40 bytes. Invalid dimensions, truncated data, path traversal, duplicate labels and trailing bytes are rejected. A collection may contain many bounded blocks; no compression invocation receives the entire collection.

Native baseline blocks contain their full gzip/xz/zstd stream, including that format's own framing. Dictionary baselines store the dictionary once in the collection. The generated codec stores complete 40-byte `ALG2` frames; operation 6 uses flag 2 for an external model. If every block uses raw escape, no model is stored. The saved collection is decoded again using only its stored model and blocks before acceptance. Model-training output, unused metadata and frame flags cannot silently grant the decoder uncounted state.

For each of the six declared baseline configurations, sum its complete collection. `B` is the smallest **whole-corpus configuration**, not the sum of the best method on each file. `C` is the candidate's complete collection. Archive gain is `1 - C/B`, with a target of at least 10%. The handwritten raw-token transpose + zstd-19 reference is reported separately to test whether generation improves on an obvious reversible representation. Both preserve original spellings, line endings and bytes.

The incremental decoder package `D` is the complete candidate executable. Deployment saving is `B - C - D`, assuming the common host runtime/libzstd is available. This is a different claim from archive gain; a negative value is reported. No extrapolated corpus size is substituted for measured data.

## Resource measurement and containment

The parent starts the wall deadline before launching the isolation helper. A private descriptor carries a readiness marker after Landlock/seccomp/resource setup. The helper waits for the parent's start acknowledgement before native exec; both control descriptors close before exec. The parent records the timer before sending the acknowledgement and input. A parent `wait4` measures completion, CPU and maximum RSS. Candidate stdout cannot supply timing or RSS.

`execution_seconds` covers input transfer, native startup, algorithm execution, output and exit. `setup_seconds` reports launcher/isolation startup separately. Three decodes use separate fresh processes; their median determines per-probe throughput. Each probe must reach 10 MiB/s. This is a conservative short-block process measurement, not warmed in-process codec throughput; noise and cloud hardware can affect it. Resource records retain every repetition. RSS includes the launcher and is capped conservatively at 256 MiB for candidate decode. `RLIMIT_AS` also bounds virtual address space, so its enforcement can precede an RSS ceiling. Native baselines use the declared 2 GiB limit; trusted zstd I/O threads are permitted. The Python structured reference reports native child RSS and separately measured Python restore time; it is not eligible as an installable candidate.

The local backend requires Linux Landlock ABI >=6 and libseccomp. Candidate workers cannot fork, start threads, create network sockets, read original files/credentials, or write filesystem paths. They see only stdin, the executable and installed system-library/tool data. Each decode starts in an empty temporary directory; neither development/test paths nor the original ZIP are granted. Builds are offline, get only the fixed Cargo workspace/toolchain, and may write their own cache. The coder is a distinct mode with networking and model credentials; it cannot write the evaluator or evidence inputs. Only coder mode permits root directory enumeration needed by Muse's file mediator. Filesystem path metadata (`stat`) remains visible: this host boundary does not hide all filenames or provide a virtual machine. Every job fails closed if containment cannot be applied.

Logs use preopened files and `RLIMIT_FSIZE`. The supervisor kills the process session on timeouts and cleans surviving descendants after exit. Candidate outputs, request/frame dimensions, original lengths and checksums are checked independently by the parent. Direct algorithm probes prevent broken decode logic hiding behind raw escape. Tests cover malformed framing, lexical edge cases, random bytes, file/network/fork/credential denial, address-space/output/time limits, model sharing, manifest tampering and freeze changes.

## Plugins and local archives

Discovery reads `plugins/*/manifest.json`. A manifest binds its directory/id to a regular `codec.rs`, SHA-256 and 16-byte codec id. Version/protocol, bounds, path and duplicate checks run before use. The host rebuilds with the fixed offline wrapper; optional historical binary hints do not select an executable. Plugin Python modules and Cargo metadata are never loaded.

Registering a new codec changes only its plugin directory:

```bash
python -m algomini register-codec my-codec-v1 path/to/codec.rs
python -m algomini.evaluator evaluate my-codec-v1 --split train
python -m algomini.evaluator encode my-codec-v1 input.csv output.alc
python -m algomini.evaluator decode output.alc decoded/
```

Local encode accepts one input of at most 2 MiB and an optional `--model model.bin` of at most 64 KiB; the model must come from a separately recorded train-only operation for research claims. Local decode selects the plugin from the collection. It verifies all blocks before writing and refuses to overwrite outputs. This general file utility is distinct from the development-only measurement loader. Do not give search access to sealed files through it. All shipped plugins remain `experimental` or `reference`; manifest discoverability is not an unseen-performance endorsement.

The Stage 2 acceptance command registers `zstd-reference-v1` after the freeze and proves exact reconstruction with unchanged trusted hashes. Its handwritten source is explicitly a plumbing reference, not evidence of AI novelty.

## Integrity and future changes

`repro/evaluator-freeze.json` pins twelve Python modules, the entire Rust template file set, the public dataset manifest, scoring configuration, actual compiler/native executables, dynamic dependencies and Rust libraries. Its SHA-256 is in `repro/evaluator-freeze.sha256` and the acceptance report. Added build scripts and changes to baseline arguments, resource limits or probe positions fail verification. Every official evaluation checks the freeze before and after execution. The freeze creator refuses replacement.

Search orchestration, the profiler, coder adapter, budgets/models/prices, research records, skills and plugin sources remain editable. New modules can be registered without changing the pinned files. Use the dedicated `python -m algomini.evaluator` process for trusted checks; do not run official scoring in a process where search code can monkey-patch imported modules.

The adjacent hash detects accidental edits; it is not authentication against a host user who can replace both files. A future publisher must pin the reviewed freeze id outside candidate control and run a fresh trusted checkout. Local search/candidate processes cannot edit the freeze. Cloud Docker packaging remains future work: use a host kernel meeting the backend requirement or provide a reviewed equivalent backend. Different native binaries require a new reviewed experimental reference, never a silent rewrite of this freeze. Record timing on the target CPU before making cloud speed claims.

The first Stage 2 preflight freeze and failure are retained under `reports/stage2/preflight-failures/01/`: Conda xz needed its matching liblzma inside the baseline read boundary. Trusted binaries installed in the dedicated environment now receive that environment's read access. The final freeze follows this correction and the explicit parent start acknowledgement; no population search or sealed evaluation occurred under the preflight freeze.
