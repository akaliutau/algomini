# Codec request issue-2

Plugin: `json-t1`. Mode: `generate`. Selected origin: reused reviewed seed; not a newly generated algorithm.

Size ranks first; throughput is secondary. Hard correctness, memory and time limits remain mandatory.

| Scope | Method | Archive bytes | Saving | Encode s | Decode MiB/s | RSS MiB | Exact |
|---|---|---:|---:|---:|---:|---:|---|
| Development | `wk-3169dda381d9c072` | 9,236 | -4.94% | 0.179 | 125.36 | 39.92 | yes |
| Development | `gzip-9` | 126,871 | -1341.55% | 0.039 | 159.35 | 37.33 | yes |
| Development | `xz-9e` | 8,801 | 0.00% | 0.154 | 251.61 | 38.04 | yes |
| Development | `zstd-19` | 9,187 | -4.39% | 0.081 | 244.52 | 39.04 | yes |
| Development | `zstd-22` | 9,187 | -4.39% | 0.081 | 254.73 | 39.92 | yes |
| Development | `zstd-dict-19` | 15,146 | -72.09% | 0.048 | 241.58 | 39.92 | yes |
| Development | `zstd-dict-22` | 15,146 | -72.09% | 0.048 | 254.71 | 39.92 | yes |

| Scope | Method | Archive bytes | Saving | Encode s | Decode MiB/s | RSS MiB | Exact |
|---|---|---:|---:|---:|---:|---:|---|
| Final | `wk-3169dda381d9c072` | 9,172 | -5.12% | 0.119 | 101.06 | 33.87 | yes |
| Final | `gzip-9` | 64,221 | -636.06% | 0.022 | 127.84 | 29.87 | yes |
| Final | `xz-9e` | 8,725 | 0.00% | 0.088 | 161.72 | 31.37 | yes |
| Final | `zstd-19` | 9,123 | -4.56% | 0.064 | 148.07 | 32.87 | yes |
| Final | `zstd-22` | 9,123 | -4.56% | 0.066 | 146.88 | 33.87 | yes |
| Final | `zstd-dict-19` | 15,052 | -72.52% | 0.036 | 147.98 | 33.87 | yes |
| Final | `zstd-dict-22` | 15,052 | -72.52% | 0.036 | 150.74 | 33.87 | yes |

Final size-first win: **False**. The 10% target was not met. Estimated/reserved model usage: $5.552354. Executable: 979,160 bytes, separate from archive sizes. One declared final file was used after source/model/client freezing; no tuning followed. Previously used test hash: **False**.

Pinned cloud image: `ghcr.io/akaliutau/algomini-lab@sha256:375e177e5a6a97bce6aaafe359060dc04bff17fb50ec0aeeca3823910151dc71`. Source: `3169dda381d9c07279def7e8e37cc6ccfc31200e8de22919b36c7dcb3b88a5fa`. Model: `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`.

Measured host: AMD EPYC 7763 64-Core Processor; kernel `7.0.0-1017-azure`. Generation failures: 1; details remain in research.json.
