# Plugin: text-v4

| Detail | Value |
| --- | --- |
| Outcome | Qualified for a release PR |
| Algorithm | Independent 2 MiB chunks. Custom lossless Rust codec. Source note: Candidate text codec: adaptive PPM context model with exact arithmetic coding, reversible line-MTF and raw fallbacks coded with zstd, plus a distinct whole-block exact-line dictionary whose literals are PPM coded. Family: PPM / context mode |
| Selected candidate | cx-0c679d66-3bd2 |
| Origin | Muse-generated evolved |
| Request mode | generate |
| Generation failures | 2 |
| Target | 10% saving; strictly positive saving qualifies |
| Target reached | False |
| Model estimate / held reserve | $15.1361 |
| Client executable | 989,536 bytes; separate from archive size |

[Review Rust source](https://github.com/akaliutau/algomini/blob/codec/issue-9/plugins/cx-0c679d666dc043d788834be54a916c80-3bd2df8c3bf47c1d/codec.rs)

| Detail | Value |
| --- | --- |
| Coding sessions | 13 of at most 14 |
| Search stopped | candidate-limit |
| Models used | muse-spark-1.3, muse-wonder-1.0 |

[Dataset manifest and file split](https://github.com/akaliutau/algomini/blob/codec/issue-9/reports/campaigns/issue-9/dataset-manifest.json)

The selected implementation is included in this request's review evidence.

Archive size ranks first. Exact reconstruction and memory/time limits are mandatory.

| Scope | Method | Archive bytes | Saving | Encode s | Decode MiB/s | RSS MiB | Exact |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Validation | cx-0c679d66-3bd2 | 296,484 | 12.63% | 1.003 | 3.11 | 89.83 | yes |
| Validation | gzip-9 | 408,732 | -20.45% | 0.093 | 109.38 | 89.83 | yes |
| Validation | xz-9e | 339,329 | 0.00% | 0.459 | 52.68 | 89.83 | yes |
| Validation | zstd-19 | 343,653 | -1.27% | 0.411 | 175.31 | 89.83 | yes |
| Validation | zstd-22 | 343,653 | -1.27% | 0.409 | 161.78 | 89.83 | yes |
| Validation | zstd-dict-19 | 392,360 | -15.63% | 0.181 | 163.57 | 89.83 | yes |
| Validation | zstd-dict-22 | 392,360 | -15.63% | 0.183 | 160.62 | 89.83 | yes |

| Scope | Method | Archive bytes | Saving | Encode s | Decode MiB/s | RSS MiB | Exact |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Development | cx-0c679d66-3bd2 | 1,486,105 | 10.27% | 5.481 | 3.15 | 89.83 | yes |
| Development | gzip-9 | 2,096,483 | -26.58% | 0.476 | 135.80 | 89.83 | yes |
| Development | xz-9e | 1,656,213 | 0.00% | 2.813 | 65.26 | 89.83 | yes |
| Development | zstd-19 | 1,675,443 | -1.16% | 2.871 | 305.47 | 89.83 | yes |
| Development | zstd-22 | 1,675,440 | -1.16% | 2.803 | 323.28 | 89.83 | yes |
| Development | zstd-dict-19 | 1,986,230 | -19.93% | 0.924 | 286.12 | 89.83 | yes |
| Development | zstd-dict-22 | 1,986,230 | -19.93% | 0.968 | 293.27 | 89.83 | yes |

| Scope | Method | Archive bytes | Saving | Encode s | Decode MiB/s | RSS MiB | Exact |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Final | cx-0c679d66-3bd2 | 689,367 | 9.82% | 3.235 | 2.84 | 40.37 | yes |
| Final | gzip-9 | 991,935 | -29.76% | 0.276 | 125.12 | 36.93 | yes |
| Final | xz-9e | 764,445 | 0.00% | 1.607 | 61.89 | 40.30 | yes |
| Final | zstd-19 | 773,355 | -1.17% | 1.384 | 323.94 | 40.30 | yes |
| Final | zstd-22 | 773,355 | -1.17% | 1.384 | 319.00 | 40.30 | yes |
| Final | zstd-dict-19 | 938,652 | -22.79% | 0.457 | 309.48 | 40.30 | yes |
| Final | zstd-dict-22 | 938,652 | -22.79% | 0.454 | 310.58 | 40.37 | yes |

| Detail | Value |
| --- | --- |
| Final access | One frozen final benchmark; no subsequent tuning |
| Previously used test hash | True |
| Final record overlap | No complete record partition |
| Independence | This overlap check alone cannot establish statistical independence |
| Development files | 1 files; 5,422,721 bytes |
| Final files | 1 files; 2,704,417 bytes |

<details>
<summary>Candidate trials and build details</summary>

| Trial | Origin | Archive bytes | Payload bytes | Model bytes | Mode |
| --- | --- | --- | --- | --- | --- |
| cx-d237c18b-0567 | Muse-generated initial | 342,800 | 341227 | 1449 | codec-specific; see source |
| cx-bd245e2e-e3b0 | Muse-generated initial | 343,739 | 343615 | 0 | codec-specific; see source |
| cx-6b0f0d1a-3bd2 | Muse-generated repair | 296,484 | 292053 | 4307 | codec-specific; see source |
| cx-0c679d66-3bd2 | Muse-generated evolved | 296,484 | 292053 | 4307 | codec-specific; see source |
| cx-d5b7c992-3bd2 | Muse-generated evolved | 296,484 | 292053 | 4307 | codec-specific; see source |
| cx-aa474061-3bd2 | Muse-generated evolved | 296,484 | 292053 | 4307 | codec-specific; see source |
| cx-32576e6e-3bd2 | Muse-generated evolved | 296,484 | 292053 | 4307 | codec-specific; see source |
| cx-d5bfbbb6-3bd2 | Muse-generated evolved | 296,484 | 292053 | 4307 | codec-specific; see source |
| cx-089266ef-3bd2 | Muse-generated evolved | 296,484 | 292053 | 4307 | codec-specific; see source |
| cx-5b166c59-3bd2 | Muse-generated evolved | 296,484 | 292053 | 4307 | codec-specific; see source |
| cx-e8b3d4b2-3bd2 | Muse-generated evolved | 296,484 | 292053 | 4307 | codec-specific; see source |

| Detail | Value |
| --- | --- |
| Cloud image | lab@8e7985fc |
| Source SHA-256 | 0c679d66 |
| Model SHA-256 | 3bd2df8c |
| Host | INTEL(R) XEON(R) PLATINUM 8573C |
| Kernel | 7.0.0-1017-azure |
| Generation failures | 2 |

| Attempt | Failure |
| --- | --- |
| initial | Coder generation failed: muse: local session messaging disabled: registry_io: local Session registry IO failed during create registry root: Permission denied (os error 13) muse: workspace root: /work/.cache/coder/62d3712dd289359d0c7146496b9 |
| initial | Contained release build failed: ss(&amp;trans, ZSTD_LEVEL) { 256 +             match zstd_compress(&amp;trans, ZSTD_LEVEL) {     &#124;  error[E0425]: cannot find function &#96;zstd_decompress&#96; in the crate root    --&gt; /algomini-work/builds/68a2bfd22515708f |

Full identities and detailed evidence are retained in the campaign JSON files. Unknown provider usage remains reserved.

</details>
