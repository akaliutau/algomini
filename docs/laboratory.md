# Reusable campaigns, memory and probes

The maintained laboratory has explicit workspace paths and JSON results. Its preparation API is `Campaign.create(...).prepare()`; a future container/job runner can call the same API or CLI. Research samples, reports and caches belong to individual campaigns. The shared library supplies reviewed skills, tool descriptions and experiment evidence. Preparation makes no coder/API calls and never opens the completed telemetry holdout.

## Dependency boundaries and historical code

The core CLI, campaign management, probes and keyword retrieval use the standard library only. `python -S -m algomini lab list` exercises that boundary. `pyproject.toml` declares separate `analytics`, `coder`, `memory` and `test` extras. The original exact environment locks remain unchanged. The optional semantic backend lives in `.tools/memory-env`, rather than adding packages to that reference environment. No PyTorch is required.

`__main__.py` is a thin entry point; `cli.py` routes historical commands, `lab_cli.py` adapts the reusable APIs, and `foundation.py` owns historical Stage 0 reporting. `campaigns.py`, `labtools.py` and `memory.py` own workspaces, probes and retrieval respectively. The small `semantic.py` adapter launches `_embed.py` with its optional interpreter.

The Stage 2 trusted modules, Stage 4 selection verifier and Stage 5 sealed verifier/runtime are retained byte-for-byte because published evidence pins their hashes. Their historical reports, frozen source/model, Rust template and package remain reproducible. Editable research orchestration now uses family-filtered memory. New campaign preparation is independent of the closed telemetry search; a generic new paid-generation controller and distributed budget service remain future orchestration work.

## Campaigns

```bash
python -m algomini campaign init .cache/campaigns/logs sample.jsonl --id logs --family json
python -m algomini campaign prepare .cache/campaigns/logs
python -m algomini campaign export .cache/campaigns/logs .
```

Inputs are bounded at 2 MiB. The default research probe reads at most 64 KiB. The manifest binds the input bytes, family and sampling policy; an existing workspace refuses a different input/policy. Each workspace contains its copied sample, preparation report/checksum, observation log and its own derived memory database. The library's reviewed files are read-only during preparation; its optional model files are also read-only. Export explicitly copies reviewable evidence to `reports/campaigns/<id>/`, never the sample or credentials.

Replaying preparation verifies its input and saved result, then returns the recorded context without rerunning probes or retrieval. Use a new campaign for another sample or changed research policy. Campaigns currently represent research/integration samples, not independent held-out acceptance results.

## Skills and semantic retrieval

Commit `.agents/skills/<mechanism>/SKILL.md`, optional references/scripts, and `metadata.json` containing family, mechanism tags and probe IDs. A skill is a reusable investigation procedure; executable algorithms remain in codec packages. New observations are automatic; promoting them into a skill remains a reviewed action. One skill can serve many samples.

`memory.py` indexes bounded sections rather than truncating every document at its beginning. It updates changed sources, removes deleted documents, and reuses embeddings by text/model hash. Normal queries check file metadata; export forces content verification. Bump the index schema when changing persisted sectioning rules. SQLite connections close explicitly. Dataset families filter both keyword and semantic results; unclassified old observations are searchable without a family filter but are not assumed applicable to another family. Test-split observation records are excluded.

The context allowance is at most four documents/6,000 excerpt characters by default, with one relevant section per document. This is the retrieval allowance, not the complete coder prompt size. Keyword BM25 and optional semantic cosine rankings are combined with reciprocal rank fusion. A brief records selected source hashes, section IDs and embedding-model identity. Retrieval suggests experiments; it does not authorize assuming a previous result transfers.

The optional backend is Model2Vec's MIT-licensed `minishlab/potion-base-2M`, pinned by revision and file hashes in `repro/memory-model.json`. Its normalized 64-dimensional vectors are computed locally. Setup from the original pinned environment:

```bash
python -m venv --system-site-packages .tools/memory-env
.tools/memory-env/bin/python -m pip install -r repro/requirements-memory.lock
python tools/bootstrap_memory.py
python -m algomini memory index --semantic
python -m algomini memory search 'repeated phrases and document templates' --family text --semantic
python -m algomini memory export --semantic
```

Without the optional environment/model, ordinary retrieval reports keyword mode; `--semantic` requires the backend. Local inference receives public skill/evidence text and a sanitized environment, never the model API credential or sample files. The exact model revision, inference helper and package version determine the embedding identity.

Git contains `memory/index-spec.json`, `catalog.jsonl`, `vectors.jsonl` and `index-manifest.json`. The manifest binds portable exports by SHA-256. Fresh campaign caches import matching vectors instead of embedding the entire library again; changed/new sections alone need computation. SQLite databases and model weights remain ignored/rebuildable. The export is a snapshot: rerun it after reviewing changed skills or evidence. Stat scans and exact vector ranking are still linear in library size; distributed indexing and approximate-neighbor search are deliberately deferred until profiling justifies them.

## Extending the laboratory

```bash
python -m algomini lab list
python -m algomini lab probe json-lexemes sample.jsonl --family json
```

The initial registry supplies byte structure, text repetition, CSV structure and JSON lexical probes. CSV structure handles quoted delimiters and multiline fields, but reports parsed structure only; the original bytes remain authoritative. JSON analysis preserves duplicate-key counts and numeric spellings in its findings and marks incomplete fragments unsupported.

Add an ordinary trusted Python function and register a `Probe(id, description, families, function, version)`. The wrapper checks applicability/input bounds and returns version, implementation/input hashes, elapsed time, findings and scope. Results must be JSON-compatible. This is explicit trusted registration; sample data and catalog files cannot name arbitrary executable code. Tests can register temporary extensions without editing dispatch logic.

`memory export` publishes the current tool catalog under `laboratory/tools.json` and indexes its capabilities alongside skills. New tools should answer a concrete missing research question, include limitations, and receive tiny correctness checks. They may investigate new representations or predictors; they cannot change acceptance scores or frozen thresholds. Prefer measured representation costs over entropy/correlation estimates when deciding whether to generate a codec.
