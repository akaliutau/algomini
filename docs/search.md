# Local Stage 3 research

```bash
python -m algomini research-profile
python -m algomini stage3
python -m algomini research-memory
python -m algomini research-memory 'decimal prefixes'
```

The default `search.toml` starts four distinct mechanisms: column layout, exact decimal deltas, cross-field predictors and temporal raw-token prefixes. It permits two additional distinct mechanisms when the initial pool is too small and validation or measured train-only representation diagnostics support further breadth. Broad search stops once three or four valid, diverse candidates are available; evolution and ablations belong to Stage 4. Every compression invocation stays below the frozen 2 MiB cap. All execution is local, with no Docker, and the sealed test is never loaded.

The editable search layer calls the existing generic CLI coder with `phase=discovery`. The budget ledger keeps the $80 discovery allocation separate from framework/refinement, enforces initial/evolved/repair limits and preserves the $20 reserve. Unknown usage retains the full reservation. Token-based charges are estimates from the declared prices, not reconciled provider billing. The user-confirmed $150 provider ceiling remains the external spending bound.

## Evidence and containment

Train-only profiles retain input hashes, original token offsets, quoting/padding/exponent classes, exact decimal-scale statistics, temporal prefixes and duplicate columns. The coder receives compact facts, at most 4 KiB of training bytes and a bounded retrieval context. Validation bytes never enter its workspace. Selected project skill text is passed explicitly in the prompt; the coder does not receive the repository filesystem. The frozen local Landlock/seccomp backend continues to separate coder, build and worker permissions.

Each generated source is saved verbatim before compilation. Tiny direct-algorithm checks and a roughly 64 KiB training screen reject broken implementations. Correct candidates register by manifest and run `python -m algomini.evaluator` in a fresh process. The trusted Stage 2 modules, scoring configuration and native toolchain pins remain unchanged. All six baselines and the structured reference are measured on the same fixed validation probes.

`python -m algomini representation-probes` measures handwritten, reversible header/column/byte-plane prototypes on one 64 KiB training extract, including transform metadata and exact reconstruction. It compares them with the existing Muse module before justifying more population breadth. These probes are research hypotheses, not generated-code scores. A prototype can justify investigation without beating the stronger structured reference.

Each candidate folder under `reports/stage3/candidates/` contains its immutable request, retrieval references, model/usage record, exact source, train screen, evaluation fingerprint, full frozen results and terminal outcome. Rejections retain their stage, diagnostics, any written source and parent hash. A repair is a new candidate with the failed source as its parent. Exact-source duplicates are recorded without another measurement.

`reports/stage3/observations.jsonl` is the durable experiment log. Every candidate has a hypothesis, mechanism tags, input/profile/source hashes, lineage, outcome, per-probe archive sizes, resource results and reproduction arguments. Raw generation traces stay in the ignored `.cache/coder/` tree; their usage/provenance summaries are versioned. Interpretation records distinguish source-based explanations from tested causal ablations.

## Resume and retrieval

Rerunning the same command reuses terminal outcomes and verified generation/evaluation caches. Cache identity includes exact source, frozen evaluator/config/toolchain id, model hash, build fingerprint, archive hash, validation hashes and training-sample hashes. Actual counted archive bytes and summarized measurements are checked as well. Modified cached sources or results stop the run instead of silently paying again. A reservation left by an interrupted process is retained; the coder will not launch the same paid attempt twice. Inspect an unresolved attempt before deciding whether to create a new request.

The run definition pins the profile, model/CLI settings and search policy. Changing these requires a new `run_id` in a separate policy file, for example `python -m algomini stage3 --policy other-search.toml`; preserve earlier records. Candidate manifest ids include that run id. A narrow exception, `stage3 --extend-budget`, explicitly records an increase of candidate/repair caps within the existing project limits, preserving the old policy/checkpoint and original deadline. It cannot change the model, data, evaluator, or wall-time limit. Stage 3 used this to raise its initial two-repair allowance to the project's four-repair cap; three repairs were actually used. The default one-hour wall budget persists across resumes. Replaying completed outcomes remains possible after the deadline; it cannot start new generation after the limit.

`research-memory` rebuilds `.cache/lab/index.sqlite` from versioned observation logs and `.agents/skills/*/SKILL.md`. SQLite FTS5 retrieves tags and text with source hashes and paths; changing source files invalidates the derived index. Queries are tokenized into quoted terms, and brief retrieval is capped at four documents/6,000 characters. The Git files are durable; the index can be deleted and rebuilt. No embedding model is required for this collection.

Curated project skills describe measured investigation procedures; compiled algorithms live in `plugins/`, and trained models remain counted archive state. The profiler's cached token-offset maps are also rebuildable. See [csv-lexical-probes](../.agents/skills/csv-lexical-probes/SKILL.md), [adaptive-csv-layouts](../.agents/skills/adaptive-csv-layouts/SKILL.md), and [the frozen evaluator contract](evaluator.md).

## Elite pool

Selection admits correct candidates with positive archive gain and passing decode speed/RSS. It first preserves distinct mechanisms in archive-size order, then reserves an exploration slot for a different per-probe compression profile. Exact source hashes are deduplicated. Mechanism labels guide population diversity; later ablations must establish which transformations actually contributed.

The Stage 1 module is a seed with its existing Stage 2 validation measurements. It is not regenerated or remeasured just to populate the pool. A Stage 3 candidate can have correct reconstruction while regressing on bytes; that outcome is retained and excluded from elites. A development gain does not establish an unseen-data win, a deployment saving, or scientific novelty. Stage 4 selects/refines using validation; Stage 5 owns one frozen sealed verification.
