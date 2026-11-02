# Stage 3: evidence-driven local search

Frozen evaluator: `e42cb5487a653169342d9eddbcd492613038c36d36b01370a3c2ffddc3ff7340`.
Four distinct initial mechanisms are screened on tiny/training inputs before fixed validation. No Docker or sealed test access.

| Candidate | Mechanism | Outcome | Corpus bytes | Gain vs baseline |
|---|---|---|---:|---:|
| muse-telemetry-v1 | length-varint-transpose | measured | 43084 | 6.85% |
| stage3-column-layout | column-layout | measured | 46305 | -0.11% |
| stage3-decimal-deltas | decimal-deltas | measured | 48600 | -5.07% |
| stage3-cross-field | cross-field | rejected | — | — |
| stage3-cross-field-r1 | cross-field | rejected | — | — |
| stage3-cross-field-r2 | cross-field | measured | 43026 | 6.98% |
| stage3-temporal-prefixes | temporal-prefixes | measured | 49200 | -6.37% |
| stage3-timestamp-dedup | timestamp-dedup | measured | 48601 | -5.08% |
| stage3-byteplanes | byteplanes | rejected | — | — |
| stage3-byteplanes-r1 | byteplanes | measured | 40108 | 13.29% |

Elites: stage3-byteplanes-r1, stage3-cross-field-r2, muse-telemetry-v1.
Stop: initial_pool_complete.
The pool favors correct modules with positive archive gain and passing speed/RSS gates, preserving distinct mechanisms and an exploration slot.
Source duplicates are recorded and not remeasured. Requests, source, lineage, failures, train screens, frozen validation, resource measurements and cache fingerprints live beside each candidate.
Retrieval uses versioned observations and project skills with a rebuildable SQLite FTS5 index. No embeddings were needed.
Discovery known token-based usage estimate: $0.8288; retained incomplete-call reservations: $0.00.
Measured archive gains are development results. Deployment package cost is reported separately; no unseen-data or scientific-novelty claim is made.
Reproduce: `python -m algomini stage3`. Completed records reuse verified generation/evaluation caches; replay makes no new paid calls.

Leading candidate **stage3-byteplanes-r1** uses **40,108 bytes** versus the best baseline's **46,253** and the structured reference's **40,434**.
Minimum per-probe decode: **22.98 MiB/s**; peak RSS: **31,676 KiB**. Complete incremental executable: **383,192 bytes**; deployment saving: **-377,047 bytes**.
The structured reference comparison is narrower than the baseline gain; Stage 4 ablations must determine what each transform contributed.
