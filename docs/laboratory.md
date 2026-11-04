# Research, skills and extension

The maintained system separates research procedures, measurement capabilities and executable codecs:

| Component | Storage | Role |
| --- | --- | --- |
| Research skill | `.agents/skills/NAME/SKILL.md` and `metadata.json` | Retrieved guidance for choosing and testing mechanisms. |
| Laboratory tool | Trusted Python function registered in `algomini/labtools.py` | Bounded, deterministic observations about development bytes. |
| Compression algorithm | Immutable Rust source/model under `plugins/`, listed in `release/registry.json` | The executable codec compiled into the console client. |
| Evidence | Development observations, campaign reports and `automation-state` | Measured successes, failures and applicability. |
| Search index | `memory/catalog.jsonl`, `vectors.jsonl`, `index-manifest.json` | Portable, source/model-checked retrieval data. |

A skill is text; adding one does not install executable code or grant new permissions. A tool is explicitly registered trusted code. A codec is independently compiled and tested, then reaches releases through review and qualification.

## Use the laboratory

```bash
python -m algomini lab list
python -m algomini lab probe json-lexemes datasets/sample/train-1mb.json --family json
python -m algomini campaign init .cache/campaigns/example datasets/sample/train-1mb.json --id example --family json
python -m algomini campaign prepare .cache/campaigns/example
python -m algomini memory search 'JSON lexical channels model cost' --family json
```

Preparation is credential-free and makes no model calls. Each campaign owns its sample copy, preparation checksum, observations and derived cache. It refuses changed bytes/policy under an existing identity. A prepared workspace replays its recorded brief. Use a new workspace for changed inputs. By default probes inspect the complete sample, bounded at 2 MiB; `--probe-bytes` can request a smaller, explicitly reported prefix.

Built-in tools cover byte structure, repeated words, quoted CSV structure, JSON lexemes, repeated records and finite-order byte entropy. Parsed numbers and schemas are research evidence: original bytes remain the reconstruction authority. Unsupported fragments are reported rather than normalized into acceptance data.

## Define or improve a skill

Create `.agents/skills/json-example/SKILL.md`:

```markdown
---
name: json-example
description: Investigate a specific JSON mechanism when a measurable structure makes it applicable.
---

Describe the observation that should trigger this experiment, the reversible
representation, its counted costs, useful controls and known limitations.
Link maintained evidence or a helper when it changes the research decision.
```

Add `metadata.json`:

```json
{"families":["json"],"tags":["lexical","templates","model-cost"],"tools":["json-lexemes","content-redundancy"]}
```

Families are `csv`, `json`, `text`, `xml`, `binary`, or `*` for broadly applicable guidance. Tags support search; tool IDs refer to the current registry. Keep the skill focused on non-obvious decisions. Put substantial supporting material in references/scripts only when needed. Do not put dataset records, credentials or final-test findings in proposal guidance.

Improve a skill after a measured experiment: record what worked, what failed and which workloads support the conclusion. A regression on one corpus does not establish a universal prohibition. The existing `json-lexical-layouts`, `csv-lexical-probes`, `adaptive-csv-layouts` and `text-structure-probes` are concise examples.

Review the change and refresh the portable index:

```bash
python -m pytest -q tests/test_repository.py
python -m algomini memory index --semantic
python -m algomini memory export --semantic
python -m algomini memory search 'your mechanism and counterexample' --family json --semantic
```

Commit the skill, metadata and changed `memory/` exports together. SQLite caches and embedding weights are rebuildable and ignored.

## Add a laboratory tool

Implement an ordinary Python callable taking bounded bytes and returning JSON-compatible findings. Register `Probe(id, description, families, function, version)` in `labtools.py`. Registration is explicit: datasets and catalog entries cannot name arbitrary executable code. The wrapper supplies input/implementation hashes, tool version, scope and elapsed time.

A tool should answer a missing research question and state its limitations. Bound displayed strings and result sizes, retain exact input hashes, and use tiny correctness cases before a 1 MiB experiment. Increment the version when semantics change. Run its focused tests and `python -m algomini memory export --semantic` so the capability becomes searchable.

Entropy tools report empirical estimates. Independent-byte entropy misses long repeats; high-order in-sample estimates can overfit. Compare model cost plus predictive coding cost plus framing, then verify actual encoded/decoded archives. The [JSON diagnosis](../reports/investigations/issue-2/report.md) illustrates why an entropy number alone cannot certify the theoretical optimum.

## Add or improve an algorithm

Implement the `Codec` interface in [api.rs](../crates/algomini-cli/src/api.rs). Use safe Rust, bounded allocations and an exact inverse. Preserve arbitrary bytes, whitespace, quoting, duplicate keys, numeric spelling and line endings. A specialized transform needs a reversible byte path for unsupported content. Shared zstd helpers are in [toolkit.rs](../crates/algomini-cli/src/toolkit.rs).

Keep learned tables in the counted model/payload. Do not embed sample records in code. The pipeline trains only on the development training view, freezes the selected model and measures complete archives. It records payload, native frame, shared model, collection overhead and decoder size; known codec modes distinguish the JSON transform from zstd/raw fallback.

Register a local source version for review:

```bash
python -m tools.register_codec /path/codec.rs --name experiment
# Optional: --model /path/model.bin. Existing aliases require --replace.
python -m algomini.cloud smoke --workspace .cache/smoke
python -m tools.check_samples --development /path/development.json --family json --plugin experiment --workspace .cache/experiment
```

Registration hashes source and model, creates an immutable version and updates the alias locally. It neither publishes nor qualifies a release. The source/model are each at most 64 KiB; the catalog holds at most 128 codecs. Keep existing versions so their archives remain decodable. Adding a selectable seed also requires updating the family choice in `cloud/research.py` if it should participate automatically.

The request loop supplies measured parent feedback before refinement. Failed code can use a bounded repair; unknown provider usage retains its reservation. Validation and complete development must improve before final access. Keep the smallest qualifying complete archive; speed breaks secondary comparisons. A final loss remains a result, not permission to retune on that file.

## How improvement accumulates

Every completed campaign records development trial outcomes and failed attempts. The publisher saves bounded recent development observations in `automation-state`; the next claim imports them into its private knowledge workspace alongside reviewed Git skills/tools. Up to 128 recent observations travel between campaigns. Successful and unsuccessful mechanisms can therefore inform later proposals even when a no-win campaign has no PR. Final measurements are excluded from this retrieval channel.

Retrieval selects at most four relevant documents and 6,000 excerpt characters, one section per document. Family filters apply to keyword and semantic results. This is the retrieval allowance; the full coding prompt also includes bounded probes, parent source and measured feedback. The whole skill library is indexed, not injected into context.

Keyword BM25 and local semantic ranking combine through reciprocal-rank fusion. The pinned Model2Vec model uses 64-dimensional vectors; unchanged text/model hashes reuse embeddings. Git exports are portable; SQLite is a disposable incremental cache. Cloud images include the isolated, pinned embedding environment and model. Local preparation/search also work without it in keyword mode; explicit `--semantic` requires it. Exact vector ranking and source scans remain linear in library size, which is appropriate for this bounded demo.

Skills and algorithms become shared reviewed knowledge after merge. Coding-model weights stay fixed; improvement comes from better measurements, accumulated evidence, targeted retrieval, richer tools and controlled candidate refinement. A stronger model or more cycles can be evaluated later against the same development protocol instead of substituting for missing feedback.
