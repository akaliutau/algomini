# Algomini

Automated algorithm discovery dedicated to minimizing data footprints.

Stage 0 supplies a pinned telemetry dataset, chronological splits, train-only analytics, compressor baselines, and Muse preflight. Stage 1 adds the Rust wrapper, configurable CLI coder, and independently checked Muse module. Stage 2 freezes the local evaluator, byte accounting, containment and manifest installation contract. Stage 3 adds bounded Muse search, resumable evidence caches, diverse elites and research skills. Stage 4 adds controlled ablations, refinement and one validation-selected source/model/build freeze. Stage 5 verifies that candidate once on the complete held-out corpus and permits compiled installation only after passing the frozen gates.

## Local setup

Use the dedicated Python environment, then install the captured versions:

```bash
conda activate algomini
python -m pip install -r requirements.lock
python -m algomini bootstrap-muse
```

Create `.env` from `.env.example` if it does not exist, and set `META_API_KEY`. The key is used for authenticated preflight and CLI generation; it is never included in reports, compiler/worker environments or subprocess arguments. The version-specific Muse bootstrap verifies the binary's SHA-256; its initial download is approximately 396 MiB. Stage 2 evaluation of committed modules needs no model credential.

```bash
python -m algomini doctor --check-api
python -m algomini stage0 --check-api
python -m pytest -q
```

`--check-api` authenticates `GET /v1/models` and checks availability of the configured model. Stage 0 makes no inference calls. The user confirmed a $150 provider ceiling before Stage 1; `run.toml` records that confirmation and enables bounded generation.

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
| `python -m algomini stage1` | Profile training data, invoke the CLI coder, compile its Rust file offline, reject broken codecs, and compare complete archive bytes |
| `python -m algomini check-codec plugins/muse-telemetry-v1/codec.rs` | Rebuild and independently check a committed codec without model credentials or inference |
| `python -m algomini stage2` | Verify the freeze, run fixed validation probes twice, check manifest installation and save acceptance evidence |
| `python -m algomini.evaluator freeze-status` | Verify trusted source/configuration, dataset manifest and native toolchain hashes |
| `python -m algomini.evaluator evaluate muse-telemetry-v1` | Evaluate a manifest plugin on the fixed validation probes with all six baselines and a structured reference |
| `python -m algomini register-codec my-codec-v1 path/to/codec.rs` | Add a source-verified plugin version without editing the frozen core |
| `python -m algomini.evaluator plugins` | List valid source-only plugin manifests |
| `python -m algomini stage3` | Run/resume bounded mechanism search using cached outcomes and the frozen evaluator |
| `python -m algomini research-profile` | Inspect exact training lexemes, quotes/padding, decimal scales, temporal prefixes and byte offsets |
| `python -m algomini representation-probes` | Test reversible training-only representations before expanding search |
| `python -m algomini research-memory 'decimal prefixes'` | Retrieve versioned findings and project skills through SQLite FTS5 |
| `python -m algomini stage4 --measure-only` | Reproduce fixed layout ablations and backend-level sweeps without generation or selection |
| `python -m algomini stage4` | Replay refinement outcomes and verify the frozen validation selection |
| `python -m algomini selected-status --build` | Verify the exact selected source/model/parameters and offline executable hash |
| `python -m algomini stage5` | Replay the final sealed result; verify/package/install the successful compiled module without inference |
| `python -m algomini install-codec PACKAGE DESTINATION --package-id SHA256` | Install a compiled plugin using its reviewed manifest pin |

Exploratory and evaluator commands accept `--split train` or `--split validation`; they expose no test split. The frozen evaluator stores shared model bytes once in an actual collection, counts all framing, and selects the strongest whole-corpus baseline. See [evaluator contracts and local encode/decode](../docs/evaluator.md).

The Stage 2 seed uses **43,084 bytes** versus xz's **46,253 bytes** on the three fixed validation probes. Stage 3's leading generated module, `stage3-byteplanes-r1`, uses **40,108 bytes**: **13.29% archive gain** versus xz and 326 bytes below the structured reference's **40,434**. It passes decode speed/RSS gates; its decoder package still erases savings on this small corpus. These are development results; Stage 4 supplies the controlled ablations and Stage 5 supplies the separate held-out result below.

Stage 4's selected mixed-column codec uses **37,130 bytes: 19.72% below xz**, with passing resource gates. A single-condition removal ablation measures **2,921 bytes** of savings from mixed-column choice. Its exact source, empty serialized model, parameters and build were frozen before the single Stage 5 sealed verification. The 391,032-byte decoder still makes deployment savings negative on this corpus. Four additional Muse sessions report **$0.4727** in token-based estimates. Stage 4 checks passed **65 Python and three Rust tests**, including a fresh-cache executable reproduction and frozen-model decode. See [Stage 4 evidence](../reports/stage4/report.md) and [refinement contracts](../docs/refinement.md).

Stage 5 passes every frozen gate on **all 14 held-out chunks (2,429,231 input bytes)**: **226,800 archive bytes versus xz’s 276,645, an 18.02% reduction**. The structured reference uses 244,693 bytes. Minimum fresh-process decode is **29.76 MiB/s**, with **32,040 KiB** peak RSS. A clean installation of the compiled plugin reconstructs every saved held-out hash without a compiler; native workers cannot read original files. Checks pass **79 Python and three Rust tests**; Stage 5 uses zero model calls and no Docker.

The measured deployment saving is **−341,187 bytes** after charging the 391,032-byte executable; the complete installable bundle costs more. This result covers later periods in one house and establishes no unseen-house generalization or scientific novelty. The sealed outcome is final; replay checks saved evidence without reopening the data. See [Stage 5 results](../reports/stage5/report.md) and [verification/install contracts](../docs/verification.md).

Stage 3 retains six initial mechanisms, three repairs and three eligible elites including the seed. Nine Muse sessions report **$0.8288** in token-based usage estimates. Replay succeeds with generation disabled and leaves the ledger unchanged. Its stage passed 56 Python tests and three trusted Rust tests; the leading candidate also passes independent direct edge-case checks. See [search contracts](../docs/search.md) and the [Stage 3 report](../reports/stage3/report.md).

## First generated codec loop

```bash
python -m algomini stage1
python -m algomini check-codec plugins/muse-telemetry-v1/codec.rs
```

Generation receives the API contract, compact measured evidence, and a training excerpt of at most 4 KiB. The configured CLI writes `codec.rs`; its exit status alone cannot establish correctness. Protected inputs must remain unchanged, and the harness compiles and evaluates the resulting source independently. Identical generation inputs reuse the verified cached file. The [budget ledger](../reports/budget/ledger.json) records reservations, reported token usage, and estimated costs. Unreported usage retains its complete reservation.

The Rust wrapper supplies a default empty-model trainer, encode/decode operations, bounded framing, CRC32, codec identity, raw escape, and safe libzstd helpers. Generated modules compile with `forbid(unsafe_code)` and no extra Cargo dependencies or build scripts. Direct algorithm checks prevent an incorrect decoder hiding behind raw escape. See the [worker protocol](../rust/codec-worker/README.md).

Everything runs on the local host. Builds and workers receive no credentials and cannot create network connections; the coder has the model credential and networking but no shell tool. Landlock restricts file contents and writes. Only coder mode receives root directory enumeration required by its native file mediator; path metadata remains visible. This backend requires Linux Landlock ABI 6 or newer and libseccomp; it fails closed when unavailable. Docker execution is reserved for a later cloud backend.

## Experiment and evidence

The first data family is [UCI Appliances Energy Prediction](https://archive.ics.uci.edu/dataset/374/appliances+energy+prediction), licensed CC BY 4.0. The dataset manifest records attribution, the source checksum, chronological day groups, one-day gaps, and development-file checksums. Derived chunks repeat the original header and preserve original record bytes. See [dataset provenance](../datasets/appliances/README.md).

The default training profile is bounded by 256 KiB and the exploratory baseline probe by 64 KiB. The fixed evaluator uses three development chunks of at most 256 KiB each. Every compression invocation is capped at 2 MiB, including combined training samples. Sealed data, raw source archives, and installed tools are excluded from Git; candidate workers cannot read source or original-file contents.

The main artifacts are:

- [Stage 0 report](../reports/stage0/report.md), [summary](../reports/stage0/summary.json), and [observations](../reports/stage0/observations.jsonl).
- [Training profile](../reports/stage0/profile-train.json) and [baseline measurements](../reports/stage0/baselines-train.json).
- [Run configuration](../run.toml) and [dataset manifest](../datasets/appliances/manifest.json).
- [Environment versions](../repro/environment.json), [Python lock](../requirements.lock), and [reproduction instructions](../repro/README.md).
- [Stage 1 report](../reports/stage1/report.md), [experiment summary](../reports/stage1/summary.json), and [Stage 1 environment](../repro/stage1-environment.json).
- [Stage 2 report](../reports/stage2/report.md), [acceptance summary](../reports/stage2/summary.json), and [evaluator freeze](../repro/evaluator-freeze.json).
- [Stage 3 report](../reports/stage3/report.md), [pool/results](../reports/stage3/results.json), [observations](../reports/stage3/observations.jsonl), and [Stage 3 environment](../repro/stage3-environment.json).
- [Stage 4 report](../reports/stage4/report.md), [results](../reports/stage4/results.json), [candidate freeze](../repro/candidate-freeze.json), and [selected artifacts](../artifacts/stage4-selected/codec.rs).
- [Stage 5 report](../reports/stage5/report.md), [full result](../reports/stage5/result.json), [sealed protocol](../repro/sealed-protocol.json), [compiled package manifest](../packages/s4-98e12bcd61e8117b0/manifest.json), and [clean installation proof](../reports/stage5/install-check.json).

The raw-token column-transposition reference is an ordinary lossless representation experiment. Its training result demonstrates a possible research direction; it establishes neither scientific novelty nor performance on unseen data.
