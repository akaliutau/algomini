# Two maintenance stages before cloud orchestration

The Stage 0–5 telemetry experiment is complete. Preserve its hash-pinned sources, configuration, package and final outcome. New work uses isolated campaigns and shares only reviewed research knowledge. Rust and the local containment backend remain unchanged. No local Docker or paid generation is needed for these stages.

## M1 — reusable Python laboratory and memory

Branch: `midstage1/reusable-lab`.

- Simplify CLI routing and make optional analytics/embedding dependencies explicit. Keep published telemetry commands compatible.
- Introduce small campaign workspaces with immutable input/policy identities, separate reports/caches and bounded local files. Campaigns share a read-only knowledge library; they never reset the completed telemetry experiment.
- Replace full memory rebuilds with per-source/per-section updates. Retrieve relevant sections with applicability filters, a strict context allowance, keyword ranking and optional locally computed semantic vectors.
- Commit skills, a portable catalog, source/model hashes and index configuration. Keep generated databases/model weights out of Git. Index only changed documents and retain failures/provenance.
- Add a small registry of deterministic, bounded probes with versioned input/output contracts. Include byte/text structure, exact JSON lexemes and reusable CSV analysis. The registry selects trusted implementations rather than executing arbitrary code named by sample data.
- Test campaign isolation, incremental indexing/deletion, semantic retrieval, filters/context bounds and probe correctness on tiny fixtures. Verify published freezes and run existing checks.

## M2 — isolated complex-text integration and reproduction

Branch: `midstage2/text-e2e`, stacked on M1.

- Prepare exactly the first 1,000,000 bytes of the published enwik8 Wikipedia benchmark. Record source provenance, published full-member checksums and the exact prefix SHA-256; never compress an input larger than 2 MiB.
- Exercise two independent campaign workspaces, probes, bounded shared-memory retrieval and durable observations. Use the already verified codec/package to test arbitrary-text fallback and installation, without commissioning another algorithm or changing the selected source.
- In a fresh temporary installation, encode the complete prefix, remove the supplied original, decode it and compare its byte count and SHA-256. Report comparison with declared native baselines without claiming a new unseen-data win.
- Prove replay does not repeat measurements or model calls. Save versions, commands, source hashes and acceptance evidence. Update the public architecture/reproduction docs and leave both stages ready for merge review.

## Boundaries

The reusable lab is a preparation/research API, separate from acceptance scoring. New tools cannot change the existing evaluator. Embedding inference is optional and local, isolated from the original Python environment; no heavyweight model framework is required by the core CLI. Cloud scheduling, distributed budgets, remote publishing and the Rust client remain later work. This work provides explicit workspace paths and JSON results that a future Docker/job wrapper can invoke directly.


## Delivered

M1 is committed as `6be3e91`: 87 Python and three Rust tests pass, the core CLI runs without site packages, and real local semantic retrieval is checked on a 256-skill synthetic library. Published telemetry pins and the original environment remain unchanged.

M2 passes exact reconstruction of all 1,000,000 prefix bytes through clean installation after original deletion, prepares independent XML/JSON campaigns, and compares four dictionary-free baselines. The existing codec is 3.23% larger than xz on this fixture; that limitation is retained in the reviewed skill and index. Cached replay denies corpus/credential reads and new codec/embedding work. The final suite passes 91 Python tests, including historical CLI failure handling; the core wheel installs and runs in an empty environment without scientific/model packages. Final checks and reproduction evidence live in `reports/midstage2/` and `repro/midstage2-environment.json`.
