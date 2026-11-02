---
name: adaptive-csv-layouts
description: Compare lossless column and byte-position layouts for numeric CSV when a single transform helps some blocks and regresses on others.
---

Use exact raw tokens rather than numerical reserialization. Generate alternative column-major NUL streams and position-major byte planes with counted token lengths. Store line endings, dimensions and the chosen mode in the payload; retain a tagged ordinary-zstd fallback for unsupported layouts. Bounds-check before allocating from decoded dimensions.

Measure each alternative after compression at comparable backend effort. The Stage 3 training probes used 7,447 bytes for plain transpose at zstd-9 versus 6,591 at zstd-19, so a layout comparison with different levels can hide the representation effect. The encoder budget is 30 seconds per bounded block; the frozen acceptance speed gate applies to decode.

The [generated adaptive source](../../../plugins/stage3-byteplanes-r1/codec.rs), SHA-256 `3d0f93fed0f457b33097f605c80dd314fbdb6c87bc2312fc4fca5acb9e3a499c`, selected NUL-column tag 1 on validation-000/006 and byte-plane tag 2 on validation-013. Its complete collection used 40,108 bytes versus xz 46,253 and the structured reference 40,434. These are three development probes, not unseen-data evidence.

For refinement, compare column-only, plane-only and adaptive variants while keeping the backend/metadata policy controlled. Per-block mode selection proves that an alternative was exercised; only those ablations can establish its causal saving. Record regressions as well as wins, preserve parents and source hashes, and use the fresh frozen evaluator process for official scores.

Keep deployment claims separate: the 383,192-byte incremental executable erases archive savings on this small corpus. The current package saving is negative. Scientific novelty and an unseen-data win remain unestablished.

Evidence: [training representations](../../../reports/stage3/representation-probes.json), [candidate validation](../../../reports/stage3/candidates/stage3-byteplanes-r1/evaluation.json), and [versioned observations](../../../reports/stage3/observations.jsonl). Reproduce with `python -m algomini.evaluator evaluate stage3-byteplanes-r1 --split validation`.
