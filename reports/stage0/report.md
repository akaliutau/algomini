# Stage 0: local experiment foundation

Prepared locally from the pinned UCI Appliances Energy Prediction archive (CC BY 4.0).
Original CSV bytes are preserved. Chronological day groups use a one-day gap at each split boundary.

Training profile: 200,528 bytes, 330 records, 29 columns.
Baseline probe: 65,167 training bytes; all compression inputs stay below 2 MiB.

| Baseline | Payload bytes | Model bytes | Total bytes | Exact reconstruction |
|---|---:|---:|---:|---|
| gzip-9 | 9177 | 0 | 9177 | yes |
| xz-9e | 7428 | 0 | 7428 | yes |
| zstd-19 | 7641 | 0 | 7641 | yes |
| zstd-22 | 7642 | 0 | 7642 | yes |
| zstd-dict-19 | 7536 | 8192 | 15728 | yes |
| zstd-dict-22 | 7536 | 8192 | 15728 | yes |

The strongest baseline on this probe is **xz-9e**.
The trusted raw-token transpose + zstd-19 reference uses **6591 bytes**, a **11.27%** reduction on this training probe. Its framing is included.
This is a known representation reference implemented in Python, not a Muse-generated codec or sealed-test result.
No incremental decoder package saving has been established. Dictionary bytes are counted once for this one-probe workload; the final evaluator must account for sharing once across the declared corpus.

Measured structure: Test one stored copy plus exact reconstruction of rv1 and rv2; equality is observed only in this sample.; Test a start timestamp plus step/exception representation, preserving original timestamp spelling.; Test raw-token column transposition and small residual predictors; count metadata and exceptions.

API preflight: authenticated model catalog succeeded. No model inference was performed.
Candidate-code containment belongs to Stage 2. The current tools execute installed, trusted binaries only.
Before paid research, verify the provider spending ceiling and explicitly enable generation in run.toml.

Reproduce: `python -m algomini stage0 --check-api`. Check the captured environment with `python -m algomini versions --check`.
See `repro/README.md`, `summary.json`, `profile-train.json`, `baselines-train.json`, and `observations.jsonl`.
