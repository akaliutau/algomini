# Stage 5: one sealed verification

Status: **passed**.
Candidate freeze: `487251797c2b5facec5d9cd36a5621b9f119169728676b421cd93ceed2078f18`. Protocol: `73dafe0f2d9b06edf75df4a98c94135c27b8372497d76f3395a5f29853bb8d3a`.
The durable claim preceded opening the private manifest. This is the final outcome for the frozen candidate; cached replay does not reevaluate it.

All 14 committed held-out chunks total 2,429,231 bytes; every compression invocation is bounded by the original chunk cap.

| Configuration | Complete corpus bytes | Shared model bytes |
|---|---:|---:|
| gzip-9 | 354255 | 0 |
| xz-9e | 276645 | 0 |
| zstd-19 | 291769 | 0 |
| zstd-22 | 291769 | 0 |
| zstd-dict-19 | 304935 | 8192 |
| zstd-dict-22 | 304935 | 8192 |
| raw-token-transpose-zstd-19 | 244693 | 0 |
| s4-98e12bcd61e8117b0 | 226800 | 0 |

Best baseline: **xz-9e**. Candidate archive gain: **18.02%**.
Minimum per-chunk decode **29.76 MiB/s**; peak job RSS **32,040 KiB**.
Declared incremental executable: **391,032 bytes**; deployment saving **-341,187 bytes**, assuming the common runtime/libzstd already exists.
Per-chunk input hashes/groups, sizes, every decode repetition and baseline resources are in `result.json`. The model is the exact frozen artifact; candidate training calls: zero.

| Chunk | Day groups | Input bytes | Candidate block bytes | Best-corpus baseline block bytes | Decode MiB/s |
|---|---|---:|---:|---:|---:|
| test-000.csv | 2016-04-30, 2016-05-01 | 175034 | 14491 | 17912 | 43.87 |
| test-001.csv | 2016-05-02, 2016-05-03 | 175034 | 15383 | 19092 | 34.83 |
| test-002.csv | 2016-05-04, 2016-05-05 | 175034 | 15747 | 19076 | 34.09 |
| test-003.csv | 2016-05-06, 2016-05-07 | 175034 | 15533 | 19004 | 49.51 |
| test-004.csv | 2016-05-08, 2016-05-09 | 175034 | 15940 | 19336 | 34.66 |
| test-005.csv | 2016-05-10, 2016-05-11 | 175034 | 16087 | 19512 | 34.69 |
| test-006.csv | 2016-05-12, 2016-05-13 | 175034 | 16046 | 19560 | 34.28 |
| test-007.csv | 2016-05-14, 2016-05-15 | 175034 | 16419 | 19940 | 45.76 |
| test-008.csv | 2016-05-16, 2016-05-17 | 175034 | 16130 | 19648 | 35.22 |
| test-009.csv | 2016-05-18, 2016-05-19 | 175034 | 15947 | 19676 | 35.83 |
| test-010.csv | 2016-05-20, 2016-05-21 | 175034 | 18481 | 22296 | 32.00 |
| test-011.csv | 2016-05-22, 2016-05-23 | 175034 | 17102 | 21796 | 41.67 |
| test-012.csv | 2016-05-24, 2016-05-25 | 175034 | 17393 | 20664 | 33.45 |
| test-013.csv | 2016-05-26, 2016-05-27 | 153789 | 15813 | 18860 | 29.76 |

The per-chunk baseline is the same winning whole-corpus configuration throughout. Block sizes include native/ALG2 framing; the aggregate above also counts collection metadata and shared models.

Verified compiled package: `packages/s4-98e12bcd61e8117b0`; manifest id `fd4de6ec7cc2fbbc395658a9905fb5e5f23fa145c41d1474fd0992b9494485fb`.
The installable bundle is 705,541 bytes including Python runtime files, source and evidence; full-bundle deployment saving is -655,696 bytes. The Python interpreter and native system libraries remain external prerequisites.
A clean temporary installation decoded every saved sealed archive hash without supplying original/development paths or a compiler. A synthetic original was deleted before installed decode. Only the verified selected manifest was promoted; the trusted core remains unchanged.
Estimated break-even input volume is 18.17 MiB for the incremental executable, or 32.79 MiB for all bundled files, assuming identical per-byte savings and fixed package overhead. This extrapolation is not a measured larger-corpus result.

Scope: later time periods in one house. This establishes no generalization to unseen houses or scientific novelty. Negative deployment savings are reported separately from archive gains.
No Docker or model inference was used. Source/model/parameters/compiler/evaluator pins match. New generation is disabled after sealed consumption.
Reproduce the stored outcome: `python -m algomini stage5`. Do not delete the consumed marker or rerun the sealed measurements. Packaging/decode replay uses saved artifacts.
