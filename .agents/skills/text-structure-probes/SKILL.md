---
name: text-structure-probes
description: Investigate repeated phrases, record templates and lexical structure in text, XML or JSON compression workloads using bounded deterministic probes.
---

Use `python -m algomini lab list` to discover the available probes. Run `byte-structure` before interpreting text, then `text-repetition`; use `json-lexemes` for complete JSON or JSONL samples. A bounded XML/JSON fragment need not parse as a complete document. Keep that limitation in the report rather than repairing or normalizing the sample.

Repeated words and schemas suggest dictionary, prefix or template experiments. Their occurrence counts do not establish a compression improvement: test the representation with literal tables, IDs, lengths and exceptions included. Preserve original whitespace, numeric spelling, entity escaping, key order and duplicate keys.

Treat tools as research capabilities and this skill as an investigation procedure. Keep probe versions, input hashes and negative findings with each campaign. Reuse a measured mechanism only after checking its applicability to the new workload. The final codec may use a reversible fallback when a text-specific transform is unsupported.

The [Wikipedia integration](../../../reports/midstage2/report.md) reconstructs exactly the first 1,000,000 enwik8 bytes with the existing telemetry codec, but its 300,163-byte collection is 3.23% larger than xz. Treat this as a fallback/integration check, not evidence that a CSV mechanism improves general text. Investigate a text-specific representation before claiming a compression benefit.

For repeated natural-language text, investigate a sparse adaptive partial-match context model with symbol exclusion. Update only contexts actually visited during prediction. Shared escape estimators can use context depth, distinct-symbol count, mean frequency and the previous byte's lexical class; adaptive counts are reconstructed during decoding and need no stored vocabulary. Compare context depth and estimator features with complete archives, not log-loss estimates alone. Begin with empty, single-byte, repeated, arbitrary-binary and truncated-input controls before the 1 MB benchmark.

The [Wikipedia search](../../../reports/wikipedia-search/report.md) records a Muse-generated model and explicit host derivatives. Its frozen order-six codec with lexical escape features used 260,727 bytes versus xz's 290,783 on the examined prefix; throughput was about 2.18 MiB/s. This mechanism trades CPU and memory for size. Its eligibility relies on this campaign's explicit size-first objective; the original telemetry speed requirement remains unchanged. Re-test on a different workload before recommending it.

Retain negative evidence: byte-pair substitution, word dictionaries, case folding and simple BWT/MTF preprocessing failed to improve this sample sufficiently. Inlining short symbol lists preserved size but slowed decoding. These findings are sample-specific. Use `tools/wiki_*probe.py` for the recorded research experiments; optional suffix-array probes use the separately pinned text-lab environment. Freeze selection before opening a reserved verification sample, and exclude its result from proposal retrieval.
