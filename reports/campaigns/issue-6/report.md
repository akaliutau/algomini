# Plugin: text-v2

| Detail | Value |
| --- | --- |
| Outcome | Qualified for a release PR |
| Algorithm | Independent 2 MiB chunks. Text codec comparing adaptive PPM/arithmetic coding with zstd fallback. |
| Selected candidate | wk-3169dda3 |
| Origin | Existing reviewed codec reused |
| Request mode | generate |
| Generation failures | 0 |
| Target | 10% saving; strictly positive saving qualifies |
| Target reached | True |
| Model estimate / held reserve | $0.1577 |
| Client executable | 1,005,864 bytes; separate from archive size |

[Review Rust source](https://github.com/akaliutau/algomini/blob/codec/issue-6/plugins/wk-3169dda381d9c072/codec.rs)

[Dataset manifest and file split](https://github.com/akaliutau/algomini/blob/codec/issue-6/reports/campaigns/issue-6/dataset-manifest.json)

This request qualifies an existing implementation; no new winning algorithm was generated.

Archive size ranks first. Exact reconstruction and memory/time limits are mandatory.

| Scope | Method | Archive bytes | Saving | Encode s | Decode MiB/s | RSS MiB | Exact |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Validation | wk-3169dda3 | 283,183 | 16.55% | 0.688 | 2.75 | 89.60 | yes |
| Validation | gzip-9 | 408,732 | -20.45% | 0.092 | 114.07 | 89.60 | yes |
| Validation | xz-9e | 339,329 | 0.00% | 0.367 | 54.90 | 89.60 | yes |
| Validation | zstd-19 | 343,653 | -1.27% | 0.340 | 171.80 | 89.60 | yes |
| Validation | zstd-22 | 343,653 | -1.27% | 0.337 | 184.76 | 89.60 | yes |
| Validation | zstd-dict-19 | 392,360 | -15.63% | 0.178 | 177.18 | 89.60 | yes |
| Validation | zstd-dict-22 | 392,360 | -15.63% | 0.180 | 172.59 | 89.60 | yes |

| Scope | Method | Archive bytes | Saving | Encode s | Decode MiB/s | RSS MiB | Exact |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Development | wk-3169dda3 | 1,434,371 | 13.39% | 3.705 | 2.62 | 89.60 | yes |
| Development | gzip-9 | 2,096,483 | -26.58% | 0.474 | 136.78 | 89.60 | yes |
| Development | xz-9e | 1,656,213 | 0.00% | 2.275 | 65.87 | 89.60 | yes |
| Development | zstd-19 | 1,675,443 | -1.16% | 2.417 | 321.04 | 89.60 | yes |
| Development | zstd-22 | 1,675,440 | -1.16% | 2.519 | 335.10 | 89.60 | yes |
| Development | zstd-dict-19 | 1,986,230 | -19.93% | 0.915 | 301.60 | 89.60 | yes |
| Development | zstd-dict-22 | 1,986,230 | -19.93% | 0.917 | 300.05 | 89.60 | yes |

| Scope | Method | Archive bytes | Saving | Encode s | Decode MiB/s | RSS MiB | Exact |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Final | wk-3169dda3 | 644,368 | 15.71% | 1.730 | 2.35 | 67.03 | yes |
| Final | gzip-9 | 991,935 | -29.76% | 0.188 | 165.98 | 36.70 | yes |
| Final | xz-9e | 764,445 | 0.00% | 1.215 | 83.24 | 40.06 | yes |
| Final | zstd-19 | 773,355 | -1.17% | 1.056 | 396.47 | 40.06 | yes |
| Final | zstd-22 | 773,355 | -1.17% | 1.153 | 397.71 | 40.06 | yes |
| Final | zstd-dict-19 | 938,652 | -22.79% | 0.300 | 383.92 | 40.06 | yes |
| Final | zstd-dict-22 | 938,652 | -22.79% | 0.309 | 361.90 | 40.09 | yes |

| Detail | Value |
| --- | --- |
| Final access | One frozen final benchmark; no subsequent tuning |
| Previously used test hash | False |
| Final record overlap | No complete record partition |
| Independence | This overlap check alone cannot establish statistical independence |
| Development files | 1 files; 5,422,721 bytes |
| Final files | 1 files; 2,704,417 bytes |

<details>
<summary>Candidate trials and build details</summary>

| Trial | Origin | Archive bytes | Payload bytes | Model bytes | Mode |
| --- | --- | --- | --- | --- | --- |
| wk-3169dda3 | reused reviewed seed; not a newly generated algorithm | 283,183 | 283092 | 0 | codec-specific; see source |
| cx-4c16b773-e3b0 | reused reviewed seed; not a newly generated algorithm | 343,735 | 343611 | 0 | zstd |
| cx-a0c0279f-e3b0 | Muse-generated initial | 283,216 | 283092 | 0 | codec-specific; see source |

| Detail | Value |
| --- | --- |
| Cloud image | lab@885871bf |
| Source SHA-256 | 3169dda3 |
| Model SHA-256 | e3b0c442 |
| Host | AMD EPYC 9V45 96-Core Processor |
| Kernel | 7.0.0-1017-azure |
| Generation failures | 0 |

Full identities and detailed evidence are retained in the campaign JSON files. Unknown provider usage remains reserved.

</details>
