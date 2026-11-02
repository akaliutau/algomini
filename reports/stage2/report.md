# Stage 2: frozen local evaluator

Freeze: `e42cb5487a653169342d9eddbcd492613038c36d36b01370a3c2ffddc3ff7340`. Exact code/config/runtime pins are in `repro/evaluator-freeze.json`.

Three fixed validation probes total 437,694 bytes. Each block is at most 262,144 bytes.
Training models use training chunks only. The sealed test was not opened. No inference calls or Docker were used.

| Configuration | Complete corpus bytes | Shared model bytes |
|---|---:|---:|
| gzip-9 | 59038 | 0 |
| xz-9e | 46253 | 0 |
| zstd-19 | 48475 | 0 |
| zstd-22 | 48478 | 0 |
| zstd-dict-19 | 57136 | 8192 |
| zstd-dict-22 | 57136 | 8192 |
| raw-token-transpose-zstd-19 | 40434 | 0 |
| muse-telemetry-v1 | 43084 | 0 |

The strongest declared configuration is **xz-9e**. Muse's Stage 1 module has **6.85%** archive gain on these validation probes.
Minimum per-probe decode speed is **27.03 MiB/s**; peak decoder job RSS is **32,052 KiB**.
The incremental executable is 376,568 bytes; deployment saving is **-373,399 bytes**, assuming the common runtime/libzstd is available.
These are development measurements. They do not establish an unseen-data win, scientific novelty, or approval to install a winning module.

Two independent runs agree on every complete archive size and SHA-256, including all six baselines, the structured reference, and the candidate.
Identity loses on compressible input; bad flags, dimensions, codec ids, checksums and truncations are rejected. A second handwritten module was registered after the freeze and round-tripped without core edits.
The test suite additionally exercises original-file/credential/network/process denial, timeout, output and address-space limits, trained-model sharing, manifest tampering and freeze mismatches.

Timing starts at the trusted supervisor's readiness marker before input transfer and ends at child exit. It includes native startup and teardown; Python isolation setup is separate. Three fresh processes are measured per block. RSS includes the launcher and is conservative. Timings vary; only byte counts/hashes are required to repeat.
The common ALC1 container counts the method id, filenames, lengths and shared model; generated ALG2 frames additionally count their full header, checksum, raw escapes and payloads. B is the smallest complete baseline corpus, never a per-file oracle.

See `evaluation-first.json`, `evaluation-repeat.json`, `summary.json`, `docs/evaluator.md`, and `repro/README.md`.
