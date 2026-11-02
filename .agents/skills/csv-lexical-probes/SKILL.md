---
name: csv-lexical-probes
description: Investigate reversible telemetry CSV representations when quoting, padding, numeric spellings, or temporal repetition may affect compression.
---

Start with `python -m algomini research-profile`. It reads bounded training probes and records byte offsets, raw-token equality, prefixes and numeric lexical classes. Read the [measured profile](../../../reports/stage3/profile-train.json) before proposing a transform.

Keep the raw bytes as the reconstruction authority. This workload has double-quoted numbers, leading spaces, scientific notation and long decimal expansions. Decimal statistics unwrap these for analysis only. Store all spelling/style information or use a reversible exception path; numerical closeness does not permit rounding. Preserve CRLF and final-newline state.

When checking repeated columns, compare data records and preserve the header separately. The sampled `rv1`/`rv2` values match, but their names differ. A generic codec must detect the relationship in each block and retain exceptions rather than assume it from a field name.

Use token offsets from `.cache/lab/layout/<input-sha256>.json` to recover counterexamples from the original development file. The index is derived; the versioned profile retains input hashes and first-record offsets. The probe uses raw comma boundaries and is not a general quoted-CSV parser. Unsupported layouts need a reversible fallback.

Measure the complete encoded representation after zstd, including lengths, predictor choices, exceptions and shared models. Prefix-byte savings and estimated numeric residual bits are hypotheses, not archive gains. Compare with the frozen raw-token transpose reference before expanding a family.

Evidence: [training profile](../../../reports/stage3/profile-train.json), [first layout source](../../../plugins/stage3-column-layout/codec.rs), and its [validation result](../../../reports/stage3/candidates/stage3-column-layout/evaluation.json). The header/level explanations for that regression are source-based hypotheses pending ablation.
