# Algomini

Automated algorithm discovery dedicated to minimizing data footprints.

Stage 0 supplies a local experiment foundation: a pinned telemetry dataset, chronological splits, train-only analytics, reproducible compressor baselines, and Muse preflight. The generated Rust codec loop is Stage 1.

## Local setup

Use the dedicated Python environment, then install the captured versions:

```bash
conda activate algomini
python -m pip install -r requirements.lock
python -m algomini bootstrap-muse
```

Create `.env` from `.env.example` if it does not exist, and set `META_API_KEY`. The key is read only for the authenticated preflight request and is never included in reports or subprocess arguments. The version-specific Muse bootstrap verifies the binary's SHA-256; its initial download is approximately 396 MiB.

```bash
python -m algomini doctor --check-api
python -m algomini stage0 --check-api
python -m pytest -q
```

`--check-api` authenticates `GET /v1/models` and checks availability of the configured model. Stage 0 makes no inference calls. `run.toml` keeps generation disabled until a provider spending ceiling is verified.

## Local tools

| Command | Output |
|---|---|
| `python -m algomini bootstrap-muse` | Download or verify the pinned official Muse binary under `.tools/` |
| `python -m algomini doctor --check-api` | Package/tool checks, CLI contract, credential presence, and authenticated model availability |
| `python -m algomini prepare` | Checksum-verified source, bounded development chunks, and sealed manifest commitment |
| `python -m algomini profile` | Training-byte statistics, field dependencies, timestamp steps, and research hypotheses |
| `python -m algomini baselines` | Exact round-trips, complete bytes, timing, and peak RSS for six baseline configurations |
| `python -m algomini versions` | Refresh Python, Conda, system-package, and CLI version records |
| `python -m algomini versions --check` | Fail if installed Python/package or CLI versions differ from the captured reference |
| `python -m algomini stage0 --check-api` | Complete local preparation, profile, baselines, observations, and report |

Exploratory `profile` and `baselines` commands accept `--split train` or `--split validation`. The CLI exposes no exploratory test split. Stage 0 executes installed, trusted tools; candidate-code isolation is implemented in Stage 2.

## Experiment and evidence

The first data family is [UCI Appliances Energy Prediction](https://archive.ics.uci.edu/dataset/374/appliances+energy+prediction), licensed CC BY 4.0. The dataset manifest records attribution, the source checksum, chronological day groups, one-day gaps, and development-file checksums. Derived chunks repeat the original header and preserve original record bytes. See [dataset provenance](datasets/appliances/README.md).

The default training profile is bounded by 256 KiB and the baseline probe by 64 KiB. Every compression invocation is capped at 2 MiB, including the combined dictionary-training samples. Sealed data, raw source archives, and installed tools are excluded from Git and from the planned candidate-worker mounts.

The main artifacts are:

- [Stage 0 report](reports/stage0/report.md), [summary](reports/stage0/summary.json), and [observations](reports/stage0/observations.jsonl).
- [Training profile](reports/stage0/profile-train.json) and [baseline measurements](reports/stage0/baselines-train.json).
- [Run configuration](run.toml) and [dataset manifest](datasets/appliances/manifest.json).
- [Environment versions](repro/environment.json), [Python lock](requirements.lock), and [reproduction instructions](repro/README.md).

The raw-token column-transposition reference is an ordinary lossless representation experiment. Its training result demonstrates a possible research direction; it establishes neither scientific novelty nor performance on unseen data.
