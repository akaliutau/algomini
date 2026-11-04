---
name: csv-lexical-probes
description: Investigate reversible telemetry CSV layouts when quoting, padding, numeric spellings or temporal repetition may affect compression.
---

Use `byte-structure` and `csv-structure` through `python -m algomini lab probe`. Preserve exact tokens, quoted delimiters, multiline fields, CRLF and the final newline state. Parsed numbers are evidence for predictors, never authority for reserialization: retain leading spaces, scientific notation, negative zero and decimal precision through a reversible lexical or exception path.

Compare repeated columns on data rows while retaining distinct headers. Detect relationships in the current block instead of assuming that a field name guarantees equality. Test numeric deltas, shared prefixes and exact token copying with all predictor choices, lengths and exceptions counted.

Use record-aware development views for consistent CSV; a byte fragment still needs an exact fallback. Compare complete archives against the strongest baseline at equal backend effort. Preserve the source, input hashes and negative results. The reviewed `telemetry-csv` seed supplies a reusable mechanism, not a guarantee of improvement on another dataset.
