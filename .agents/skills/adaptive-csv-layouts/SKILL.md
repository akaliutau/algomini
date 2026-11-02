---
name: adaptive-csv-layouts
description: Compare lossless column and byte-position layouts for numeric CSV when a single transform helps some blocks and regresses on others.
---

Use exact raw tokens rather than numerical reserialization. Generate alternative column-major NUL streams and position-major byte planes with counted token lengths. Store line endings, dimensions and the chosen mode in the payload; retain a tagged ordinary-zstd fallback for unsupported layouts. Bounds-check before allocating from decoded dimensions.

Measure each alternative after compression at comparable backend effort. The Stage 3 training probes used 7,447 bytes for plain transpose at zstd-9 versus 6,591 at zstd-19, so a layout comparison with different levels can hide the representation effect. The encoder budget is 30 seconds per bounded block; the frozen acceptance speed gate applies to decode.

The [generated adaptive source](../../../plugins/stage3-byteplanes-r1/codec.rs), SHA-256 `3d0f93fed0f457b33097f605c80dd314fbdb6c87bc2312fc4fca5acb9e3a499c`, selected NUL-column tag 1 on validation-000/006 and byte-plane tag 2 on validation-013. Its complete collection used 40,108 bytes versus xz 46,253 and the structured reference 40,434. These are three development probes, not unseen-data evidence.

For refinement, compare column-only, plane-only and adaptive variants while keeping the backend/metadata policy controlled. Per-block mode selection proves that an alternative was exercised; only those ablations can establish its causal saving. Record regressions as well as wins, preserve parents and source hashes, and use the fresh frozen evaluator process for official scores.

Stage 4 supplied the causal check: at level 19, column-only uses 40,506 bytes and adaptive 40,108, so plane support saves 398 bytes on the third probe. Keep backend effort, decoder, framing and method-name length fixed. The evolved per-column mixture at level 22 uses 37,130 bytes; disabling only its mixed-choice condition gives 40,051, a 2,921-byte contribution. Prefer per-column alternatives only after accounting for modes, lengths and their final global compressed size; keep the parent whole-block fallbacks.

The [frozen mixed-column source](../../../artifacts/stage4-selected/codec.rs), SHA-256 `98e12bcd61e8117b0c9f3cc6b4edfbc3ebcb13f11d332c39290903ed97cc8cf7`, is selected for one later sealed verification. Equal archive sizes prefer a smaller decoder package; dictionary/template descendants added code without improving these probe sizes. Do not reopen search or replace the frozen source after viewing sealed results.

Keep deployment claims separate: Stage 3's 383,192-byte executable and Stage 4's selected 391,032-byte executable both erase archive savings on this small corpus. The Stage 4 package saving is −381,909 bytes. Scientific novelty and an unseen-data win remain unestablished.

Evidence: [training representations](../../../reports/stage3/representation-probes.json), [candidate validation](../../../reports/stage3/candidates/stage3-byteplanes-r1/evaluation.json), and [versioned observations](../../../reports/stage3/observations.jsonl). Reproduce with `python -m algomini.evaluator evaluate stage3-byteplanes-r1 --split validation`.

Stage 4 evidence: [fixed-level ablations](../../../reports/stage4/ablations.json), [mixed-choice removal](../../../reports/stage4/mixed-choice-ablation.json), and [active candidate freeze](../../../repro/candidate-freeze.json). Verify with `python -m algomini selected-status --build`.
