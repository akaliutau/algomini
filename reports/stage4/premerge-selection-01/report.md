# Stage 4: refinement and frozen candidate

Candidate freeze: `45c57f7d99bf0af49f1f225a63476649b305811bad5bb433a5caf55d7f257f22`.
Selected **s4-3bef7bc2eb6280877**, source `3bef7bc2eb62808778f213c91ff9bc334e20c6da8beb1c87bd52fd4cb39b52fb`. All ranking uses fixed validation probes; the sealed test is unopened.

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
Selected complete archive: **37,130 bytes**, **19.72%** below the strongest declared baseline. Minimum decode speed **24.81 MiB/s**, peak RSS **31,616 KiB**. Deployment saving is **-415,837 bytes** on this small corpus.
An independent repeat matches all eight complete archive sizes/hashes and passes resource gates. Every deterministic derivative is labeled as host-generated from exact Muse source; model revisions retain source lineage and usage records.
The negative-control minimizer reduces a deliberately broken fixture to one byte. Any model correctness failures retain their own bounded counterexamples; compiler errors retain source/diagnostics rather than inventing input failures.
Muse refinement sessions: 4; known token-based usage estimate $0.4727; retained reservations $0.00. Stop: bounded_refinement_complete.
The exact source, serialized model, parameters, release compiler settings, native evaluator id, thresholds and public sealed commitment are frozen in `repro/candidate-freeze.json` and `artifacts/stage4-selected/`.
No scientific novelty, deployment saving, or unseen-data win is claimed. Stage 5 must use this exact frozen model without training on sealed data, and record one final sealed outcome.
Reproduce: `python -m algomini stage4`; verify: `python -m algomini selected-status --build`. Completed replay uses cached requests/results and never makes another model call.
