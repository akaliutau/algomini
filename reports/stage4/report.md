# Stage 4: refinement and frozen candidate

Candidate freeze: `487251797c2b5facec5d9cd36a5621b9f119169728676b421cd93ceed2078f18`.
Selected **s4-98e12bcd61e8117b0**, source `98e12bcd61e8117b0c9f3cc6b4edfbc3ebcb13f11d332c39290903ed97cc8cf7`. All ranking uses fixed validation probes; the sealed test is unopened.

| Candidate | Experiment | Outcome | Complete bytes | Gain |
|---|---|---|---:|---:|
| s4-2ab3e34ddd0c55b01 | raw-zstd-19 | measured | 48599 | -5.07% |
| s4-cec08bd29e18744aa | column-zstd-19 | measured | 40506 | 12.43% |
| s4-47f493c6ff94be619 | plane-zstd-19 | measured | 40537 | 12.36% |
| s4-f9dec61e727886d86 | adaptive-zstd-19 | measured | 40108 | 13.29% |
| s4-19666ea483ebdf4f6 | adaptive-zstd-9 | measured | 45883 | 0.80% |
| s4-b1218d7c50181a184 | adaptive-zstd-15 | measured | 40844 | 11.69% |
| s4-33a3537586ff382dd | adaptive-zstd-22 | measured | 40051 | 13.41% |
| s4-98e12bcd61e8117b0 | column-local-choice | measured | 37130 | 19.72% |
| s4-3bef7bc2eb6280877 | token-dictionaries | measured | 37130 | 19.72% |
| s4-ac6e981fd454df60f | lexical-templates | rejected | — | — |
| s4-9be1d5c14a57e50f9 | lexical-templates | measured | 37130 | 19.72% |

At zstd-19, byte planes save **398 bytes** versus column-only output, and adaptive representation saves **8491 bytes** versus raw-zstd output. Decoder/framing/backend effort and 20-byte method names are controlled.
Disabling only the evolved mixed-column selector at zstd-22 increases the archive from **37,130 to 40,051 bytes**: its controlled contribution is **2,921 bytes**.
Selected complete archive: **37,130 bytes**, **19.72%** below the strongest declared baseline. Minimum decode speed **23.67 MiB/s**, peak RSS **32,100 KiB**. Deployment saving is **-381,909 bytes** on this small corpus.
An independent repeat matches all eight complete archive sizes/hashes and passes resource gates. Every deterministic derivative is labeled as host-generated from exact Muse source; model revisions retain source lineage and usage records.
Equal-size archives prefer the smaller decoder package, then source hash. An earlier unmerged source-hash tie draft is retained under `premerge-selection-01/`; no sealed data was opened and no extra model call was made to revise it.
The negative-control minimizer reduces a deliberately broken fixture to one byte. Any model correctness failures retain their own bounded counterexamples; compiler errors retain source/diagnostics rather than inventing input failures.
Muse refinement sessions: 4; known token-based usage estimate $0.4727; retained reservations $0.00. Stop: bounded_refinement_complete.
The exact source, serialized model, parameters, release compiler settings, native evaluator id, thresholds and public sealed commitment are frozen in `repro/candidate-freeze.json` and `artifacts/stage4-selected/`.
No scientific novelty, deployment saving, or unseen-data win is claimed. Stage 5 must use this exact frozen model without training on sealed data, and record one final sealed outcome.
Reproduce: `python -m algomini stage4`; verify: `python -m algomini selected-status --build`. Completed replay uses cached requests/results and never makes another model call.
