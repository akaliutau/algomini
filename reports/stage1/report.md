# Stage 1: first local codec loop

Muse generated `plugins/muse-telemetry-v1/codec.rs` using **muse-spark-1.3**.
The harness compiled it offline, checked the algorithm directly on eight tiny inputs, and verified framed reconstruction in a fresh restricted process.

| Measurement | Bytes |
|---|---:|
| Training input | 65167 |
| Strongest declared baseline (xz-9e) | 7428 |
| Identity codec including frame | 65207 |
| Generated codec including frame/model | 7072 |
| Simple raw-token transpose reference | 6591 |
| Decoder executable | 376328 |

Archive reduction on this training probe: **4.79%**. Net saving after the incremental executable: **-375972 bytes**.
These are exploratory training results. The module is experimental; unseen-data verification and final throughput gates remain later stages.

The generated module currently trails the simpler reference. Its source comment about never losing to raw zstd describes its internal payload choice; the complete archive still includes the mode tag and trusted framing.
Reported token usage estimates total $0.4294; $10.00 remains reserved for calls without complete usage. These are not reconciled provider invoices.
New generation attempts during this invocation: 0.

The deliberately incorrect codec was rejected: `Algorithm roundtrip failed on tiny case 1, input SHA-256 6e340b9cffb37a989ca544e6bb780a2c78901d3fb33738768511a30617afa01d`.
The trusted core hashes matched before and after the run. Candidate builds and workers had no credentials or network access; decode received only the counted archive.
Landlock restricts file contents and writes but does not hide host path metadata. Directory traversal metadata is visible to support native APIs. This is a host boundary, with final evaluator hardening still required in Stage 2.
Timings include process and isolation startup. Do not interpret them as warmed decoder throughput.

Reproduce with `python -m algomini stage1`. Matching generation inputs reuse the exact cached artifact; independent compilation and round-trip checks remain mandatory.
See `summary.json`, `negative-control.json`, the attempt records, and `../budget/ledger.json` for evidence.
