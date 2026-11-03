---
name: text-structure-probes
description: Investigate repeated phrases, record templates and lexical structure in text, XML or JSON compression workloads using bounded deterministic probes.
---

Use `python -m algomini lab list` to discover the available probes. Run `byte-structure` before interpreting text, then `text-repetition`; use `json-lexemes` for complete JSON or JSONL samples. A bounded XML/JSON fragment need not parse as a complete document. Keep that limitation in the report rather than repairing or normalizing the sample.

Repeated words and schemas suggest dictionary, prefix or template experiments. Their occurrence counts do not establish a compression improvement: test the representation with literal tables, IDs, lengths and exceptions included. Preserve original whitespace, numeric spelling, entity escaping, key order and duplicate keys.

Treat tools as research capabilities and this skill as an investigation procedure. Keep probe versions, input hashes and negative findings with each campaign. Reuse a measured mechanism only after checking its applicability to the new workload. The final codec may use a reversible fallback when a text-specific transform is unsupported.
