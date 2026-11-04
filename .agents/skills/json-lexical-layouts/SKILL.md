---
name: json-lexical-layouts
description: Investigate byte-exact JSON and JSONL compression using record groups, lexical channels and counted model costs, including incomplete fragments.
---

Start with `python -m algomini lab list`, then inspect `json-lexemes`, `content-redundancy` and `entropy-models` on bounded development data. A complete array and a byte fragment require different parsing strategies. Keep duplicate record groups in one development partition. Report repeated data; a different file hash does not establish new records.

Compare the reviewed `json-lexical` seed with a measured alternative. Its reversible channels group raw lexemes by the preceding key and retain all gaps, order and spelling. Preserve whitespace, duplicate keys, numeric lexemes and escapes. Keep a byte fallback for nested, malformed, binary or unsupported inputs; a successful fallback is not evidence that the structured path helped.

Read the development scoreboard before refining. Count payload, serialized model, native frame and collection identity. A smaller payload can lose after adding a dictionary. Compare smaller or compressed models, within-stream repeated-record tables and adaptive state that the decoder can reconstruct. Literal tables and exceptions must be counted; do not embed sample records in Rust source.

Treat finite-order entropy as a model-specific descriptor, not a universal minimum. The issue-2 investigation found development headroom through lexical layout despite very strong xz compression. Re-measure on the current development views and preserve losing sources/mode diagnostics. Final inputs remain outside research; a previous final file reused for regression is already examined evidence.

Keep lexing linear on malformed input: preserve an unterminated quoted tail once rather than restarting scans at each escaped quote. Test a maximum-size escaped-string fragment under the native time/memory limits.
