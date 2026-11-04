# Codec request issue-3

Plugin: `json-v1`. Mode: `generate`. Selected origin: reused reviewed seed; not a newly generated algorithm.

Size ranks first; throughput is secondary. Hard correctness, memory and time limits remain mandatory.

| Scope | Method | Archive bytes | Saving | Encode s | Decode MiB/s | RSS MiB | Exact |
|---|---|---:|---:|---:|---:|---:|---|
| Validation | `cx-4c16b773d1840d2130089affeddad6c7-e3b0c44298fc1c14` | 5,045 | 3.74% | 0.047 | 102.20 | 38.74 | yes |
| Validation | `gzip-9` | 19,930 | -280.27% | 0.007 | 181.68 | 37.27 | yes |
| Validation | `xz-9e` | 5,241 | 0.00% | 0.067 | 249.32 | 37.27 | yes |
| Validation | `zstd-19` | 5,375 | -2.56% | 0.025 | 226.58 | 37.74 | yes |
| Validation | `zstd-22` | 5,375 | -2.56% | 0.025 | 213.61 | 38.74 | yes |
| Validation | `zstd-dict-19` | 12,289 | -134.48% | 0.015 | 205.35 | 38.74 | yes |
| Validation | `zstd-dict-22` | 12,289 | -134.48% | 0.015 | 232.16 | 38.74 | yes |

| Scope | Method | Archive bytes | Saving | Encode s | Decode MiB/s | RSS MiB | Exact |
|---|---|---:|---:|---:|---:|---:|---|
| Development | `cx-4c16b773d1840d2130089affeddad6c7-e3b0c44298fc1c14` | 8,269 | 6.04% | 0.120 | 121.18 | 41.19 | yes |
| Development | `gzip-9` | 126,871 | -1341.55% | 0.035 | 190.39 | 38.74 | yes |
| Development | `xz-9e` | 8,801 | 0.00% | 0.146 | 354.40 | 39.19 | yes |
| Development | `zstd-19` | 9,187 | -4.39% | 0.069 | 364.09 | 40.19 | yes |
| Development | `zstd-22` | 9,187 | -4.39% | 0.069 | 375.61 | 41.19 | yes |
| Development | `zstd-dict-19` | 15,225 | -72.99% | 0.045 | 356.44 | 41.19 | yes |
| Development | `zstd-dict-22` | 15,220 | -72.93% | 0.049 | 361.21 | 41.19 | yes |

| Scope | Method | Archive bytes | Saving | Encode s | Decode MiB/s | RSS MiB | Exact |
|---|---|---:|---:|---:|---:|---:|---|
| Final | `cx-4c16b773d1840d2130089affeddad6c7-e3b0c44298fc1c14` | 8,217 | 5.82% | 0.075 | 100.88 | 35.15 | yes |
| Final | `gzip-9` | 64,221 | -636.06% | 0.018 | 148.26 | 31.36 | yes |
| Final | `xz-9e` | 8,725 | 0.00% | 0.077 | 182.04 | 32.65 | yes |
| Final | `zstd-19` | 9,123 | -4.56% | 0.047 | 170.77 | 34.15 | yes |
| Final | `zstd-22` | 9,123 | -4.56% | 0.046 | 175.21 | 35.15 | yes |
| Final | `zstd-dict-19` | 15,147 | -73.60% | 0.027 | 172.71 | 35.15 | yes |
| Final | `zstd-dict-22` | 15,142 | -73.55% | 0.031 | 175.14 | 35.15 | yes |

Final size-first win: **True**. The 10% target was not met. Estimated/reserved model usage: $15.000000. Executable: 1,001,800 bytes, separate from archive sizes. One declared final file was used after source/model/client freezing; no tuning followed. Previously used test hash: **True**.

Sampling: 3168 records contain 197 distinct lexical groups; duplicate groups stay in one partition.

Final record overlap: 197 of 197 distinct groups also occur in development. No exact record overlap: **False**. This check alone does not establish statistical independence.

| Trial | Origin | Archive bytes | Payload bytes | Model bytes | Mode |
|---|---|---:|---:|---:|---|
| `cx-4c16b773d1840d2130089affeddad6c7-e3b0c44298fc1c14` | reused reviewed seed; not a newly generated algorithm | 5,045 | 4921 | 0 | json-lexical |

Pinned cloud image: `ghcr.io/akaliutau/algomini-lab@sha256:23229a604e5c907ae1e966cd3101a3d32e1297cf2f373698a64742dda4b19a31`. Source: `4c16b773d1840d2130089affeddad6c75484788b0af161c42bb93d4163bdd43a`. Model: `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`.

Measured host: AMD EPYC 9V74 80-Core Processor; kernel `7.0.0-1017-azure`. Generation failures: 3; details remain in research.json.
