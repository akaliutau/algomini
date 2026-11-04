---
name: adaptive-csv-layouts
description: Compare lossless column and byte-position layouts for numeric CSV when one representation helps some records and regresses on others.
---

Compare exact token columns, byte-position planes and tagged zstd fallback. Store dimensions, token lengths, line endings, modes and exceptions. Check dimensions and decoded lengths before allocating. Numerical reserialization is unsuitable when lexical identity matters.

Hold backend effort, metadata and method identity costs constant during ablations. Measure column-only, plane-only and adaptive variants on the same development views. Historical development experiments found useful per-column choices, but only an ablation demonstrates their contribution on a new workload.

Record which modes actually ran, including fallback, and retain regressions. A smaller transformed stream does not establish a smaller complete archive. Shared models count once and decoder deployment cost is reported separately. Stop selection before final access; retain the selected source/model hashes for review and reproducible builds.
