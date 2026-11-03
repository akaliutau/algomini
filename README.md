# Algomini

A reproducible laboratory for generating and evaluating specialized Rust compressors.

The local pipeline has produced a verified telemetry codec with **18.02% smaller archives than xz** on all 14 held-out chunks. AI is used during research; installed codecs run without AI. Executable overhead makes net deployment savings negative on this small corpus. See the [measured result](reports/stage5/report.md).

The reusable laboratory separates campaign workspaces, deterministic probes and shared research memory. It runs locally without Docker. Its explicit paths and JSON APIs are intended for a later cloud job wrapper.

## Start a research campaign

Python 3.12+ is sufficient for preparation, probes and keyword retrieval:

```bash
python -m algomini lab list
python -m algomini campaign init .cache/campaigns/logs sample.jsonl --id logs --family json
python -m algomini campaign prepare .cache/campaigns/logs
python -m algomini campaign export .cache/campaigns/logs .
python -m algomini memory search 'key templates and exact numeric spellings' --family json
```

Samples are bounded at 2 MiB; research probes default to 64 KiB. Each campaign owns its copied sample, reports and cache. Shared skills/evidence remain read-only during preparation. These commands prepare research briefs; they do not start paid generation or claim unseen-data performance.

## Shared knowledge and laboratory tools

- Skills are committed under `.agents/skills/<mechanism>/SKILL.md`, with applicability/tool metadata and optional supporting resources.
- Experiments and failures live in versioned observation logs.
- `memory/` contains a portable catalog, optional semantic vectors and their source/model checksums. SQLite caches and model weights are rebuildable.
- Retrieval selects at most four relevant documents/6,000 excerpt characters; it never loads the entire skill library into a coder prompt.
- Trusted probes register in `labtools.py` and return versioned JSON. Their capabilities are searchable alongside skills.

Optional local semantic retrieval uses a small pinned Model2Vec model in a separate environment. The core CLI needs no scientific packages or model framework. Setup, extension contracts and cache behavior are in [the laboratory guide](docs/laboratory.md).

## Completed telemetry experiment

[Stages 0–5](docs/telemetry.md) cover provenance, bounded Muse coding, contained Rust workers, frozen evaluation, refinement, one final held-out result and verified compiled installation. The existing source/model/evaluator pins remain unchanged.

```bash
python -m algomini versions --check
python -m algomini selected-status --build
python -m algomini stage5
python -m pytest -q
```

`stage5` verifies and replays the stored final outcome. It does not repeat sealed measurements. The installed package decodes without a compiler; current runtime prerequisites are Linux x86_64, Python, Landlock and the pinned native libraries.

See [reproduction instructions](repro/README.md), [verification/install contracts](docs/verification.md), and the [two maintenance stages](docs/maintenance-plan.md). Cloud execution and a general paid-generation campaign controller remain later work.

## Complex-text integration

```bash
python -m algomini text-e2e
```

The [M2 integration](reports/midstage2/report.md) reconstructs exactly the first **1,000,000 Wikipedia bytes** through a fresh compiled installation after deleting the supplied original. It also exercises independent XML/JSON campaign preparation, shared semantic retrieval and four native baselines. The existing telemetry codec is **3.23% larger than xz** here; this verifies fallback and integration, not a new text-specialist compression win. Cached replay denies original-data reads and new codec/embedding work. See [fixture provenance](datasets/wikipedia/README.md).

The [Wikipedia optimization](reports/wikipedia-search/report.md) adds a text-specific adaptive context codec: **260,727 bytes versus xz's 290,783 bytes** on the examined 1 MB prefix, a **10.34% reduction** with all archive metadata counted. Size ranks first; decode speed is secondary. This is a development benchmark against the six configured baselines. The separate, frozen later-window outcome and reproduction commands are recorded in that report.
