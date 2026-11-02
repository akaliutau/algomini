# Reproducing the local environment

The captured reference is **Linux x86_64, Ubuntu 24.04, Python 3.12.16, and Rust/Cargo 1.92.0**. `environment.json` records the complete Python distribution list, tool version strings and executable hashes, SQLite/libc versions, OS, CPU, source hashes, and capture time. Hardware differences affect timing even when compressed bytes match.

The Conda export was produced with Conda 25.9.1. `git_head` identifies the checkout head at capture; the source hashes also identify implementation files that had not yet been committed at that point.

The reproduction inputs are:

- `../requirements.lock`: exact installed Python distributions, including transitive dependencies.
- `conda-linux-64.lock`: explicit Conda artifact URLs and MD5 checksums for the dedicated Python environment.
- `system-packages.lock`: installed Ubuntu package versions for gzip, zstd, file, xxd, binutils, util-linux, and GNU time.
- `muse-release.json`: official, version-specific Muse artifact URLs, SHA-256 checksums, and sizes.
- `../run.toml`: dataset archive checksum, split policy, input/resource limits, baseline argument vectors, coder template, and budget.

On a clean Ubuntu 24.04 x86_64 machine with Conda and rustup installed:

```bash
sudo apt-get update
xargs -a repro/system-packages.lock sudo apt-get install --yes
conda create --name algomini --file repro/conda-linux-64.lock --yes
conda activate algomini
python -m pip install -r requirements.lock
rustup toolchain install 1.92.0 --profile minimal
rustup override set 1.92.0
python -m algomini bootstrap-muse
python -m algomini versions --check
```

Run these commands from the repository root. Put the model credential in `.env` or the `META_API_KEY` environment variable on that machine, then run:

```bash
python -m algomini stage0 --check-api
python -m pytest -q
```

The dataset archive is approximately 11.4 MiB and Muse approximately 396 MiB. Both downloads are checksum-verified and cached locally. The complete source is streamed for preparation; compression checks operate on bounded extracts. The Conda lock supplies the reference xz/liblzma build, while gzip and zstd come from the recorded Ubuntu packages. Preserve PATH ordering by activating the environment first.

Two setup findings are retained in the observation ledger: DuckDB's in-memory file interface required an extra filesystem dependency, so the bounded profile uses a temporary CSV path; high-level zstd compression of unknown-length stdin exceeded the baseline memory limit, so every zstd encoder receives the exact `--stream-size`. The same input length is available to the candidate interface.

If an Ubuntu package version has been superseded, use the [Ubuntu snapshot service](https://ubuntu.com/server/docs/how-to/software/snapshot-service/) with a timestamp appropriate to the environment capture. The reference recipe records versions; a cloud image build and timing comparison still require validation on that target. Keep any changed environment as a new experimental reference.

`versions --check` compares exact Python/package and CLI version strings. It does not require identical kernel or CPU metadata. Binary hashes are available for stricter comparison, and source/data hashes identify the experiment inputs. `stage0` verifies an existing version reference before running; use `versions` deliberately when updating that reference.

No credentials, environment-variable dumps, key fingerprints, or authenticated response bodies are exported. The only credential check is a bounded, redirect-disabled request to the official Meta model catalogue. The recorded result includes status and selected-model availability.

The user confirmed a $150 provider limit before enabling Stage 1 generation. Step/time limits alone cannot establish a dollar ceiling. The local budget ledger reserves capacity before a call, records token-based estimates when reported, and retains the full reservation when usage is incomplete.

## Stage 1 local backend

`stage1-environment.json` adds the current harness and Rust source hashes and compiler details. `stage1-system-packages.lock` records the added compiler, C development library, libzstd, and libseccomp packages. The Python dependency lock is unchanged.

The local backend requires Linux with **Landlock ABI >=6** and libseccomp. The reference host reports ABI 8. This is a host-kernel requirement; selecting an Ubuntu base image alone does not supply it. File contents and writes are restricted, while path metadata remains visible. Worker networking and forks are denied; Cargo's local socketpair IPC is permitted inside the build boundary.

For an existing generated module, reproduce the build and measurements without invoking Muse:

```bash
xargs -a repro/stage1-system-packages.lock sudo apt-get install --yes
python -m algomini check-codec plugins/muse-telemetry-v1/codec.rs
```

Local development uses no Docker. Cloud Docker packaging and an equivalent container execution backend remain later work; the current backend fails instead of executing uncontained code on an incompatible host.

## Stage 2 frozen evaluator

`stage2-environment.json` records the current environment and harness snapshot. `stage2-system-packages.lock` captures native compiler/runtime packages; the Python and Conda locks remain unchanged. `evaluator-freeze.json` and its SHA-256 pin the trusted code/config, public dataset manifest, actual tool binaries, linked native dependencies and Rust libraries. Tool/library hashes are compared without requiring the original installation prefix. The recorded kernel/CPU describe timing context; each job requires Landlock ABI >=6 on the actual host.

On the reproduced environment, prepare the pinned dataset once if absent, then run without API credentials:

```bash
xargs -a repro/stage2-system-packages.lock sudo apt-get install --yes
python -m algomini prepare
python -m pytest -q
cargo test --offline --locked --manifest-path rust/codec-worker/Cargo.toml --target-dir .cache/rust-tests
python -m algomini.evaluator freeze-status
python -m algomini stage2
```

The last command verifies an existing freeze and repeats the fixed development measurements. It makes no model calls and does not open sealed files. It does not overwrite Stage 0/1 reports or environment references. All eight methods (six baselines, structured reference, candidate) must repeat their byte counts and archive hashes; process timing/RSS can vary. Measurements and a saved candidate collection live in `reports/stage2/` and `reports/evaluations/`.

Use the dedicated `python -m algomini.evaluator` entry point for future trusted scoring. See [the evaluator contract](../docs/evaluator.md) for the shared-model format, conservative parent timing, source-only plugin registration, integrity scope and cloud migration requirements. A native hash mismatch requires a new reviewed experimental reference; do not regenerate this freeze merely to suppress a mismatch. No cloud Docker image or cloud performance result is claimed by Stage 2.
