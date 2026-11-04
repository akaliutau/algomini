# Local development

Run these commands from the repository root. Use Linux, Python 3.12+, Rust 1.92+ and the `gzip`, `xz`, `zstd` command-line tools. Native containment requires Landlock ABI 6+ and libseccomp; cloud smoke checks the actual host. Local work uses no Docker.

```bash
python -m pip install -r requirements.lock
python -m pip install --no-deps --no-build-isolation -e .
python -m pytest -q
cargo test --offline --locked --manifest-path crates/algomini-cli/Cargo.toml
cargo test --offline --locked --manifest-path rust/codec-worker/Cargo.toml
```

The original Rust worker remains as a small compatibility fixture. Its Python test adapter is under `tests/`, outside the installed package. The Python console entry is `algomini-lab`; `python -m algomini` also works. The Rust product console is `algomini`, avoiding a name collision in fresh installations.

Build Python wheels from a clean checkout/build directory. Old setuptools `build/lib` output can otherwise carry removed modules into a wheel. Docker excludes generated `build/` and `dist/` directories; local editable installation reads the maintained source tree.

Fetch Rust dependencies once with `cargo fetch --locked --manifest-path crates/algomini-cli/Cargo.toml` before an offline build. Build the source client with `cargo build --release --locked --manifest-path crates/algomini-cli/Cargo.toml`. Select `general`, `json-lexical`, `telemetry-csv` or `wiki-xml`, or a merged request's alias.

## Check the provided samples

```bash
python -m tools.check_samples \
  --development datasets/sample/train-1mb.json \
  --regression datasets/sample/test-512kb.json \
  --workspace .cache/sample-check

python -m algomini.cloud smoke --workspace .cache/cloud-smoke
```

The first command builds the actual client, measures record-aware validation and the complete development sample, and optionally checks a known regression file. It counts complete archives against all configured baselines and verifies public-console reconstruction after deleting a supplied input copy. It makes no AI calls and grants no release qualification. The sample files were already examined in issue-2; keep them as regression fixtures.

`tools.json_campaign_diagnostics` provides a separate Python representation/entropy investigation. Its prospective frame estimates are research guidance. Use `check_samples` for actual Rust archives and resource checks. Inputs are bounded at 2 MiB, and shared models at 64 KiB.

## Optional semantic environment

Preparation and keyword retrieval need only the standard library. Coding adds `python-dotenv`; tests use the small pinned test environment. The optional embedding environment has its own lock and no PyTorch/SciPy dependency:

```bash
python -m venv .tools/memory-env
.tools/memory-env/bin/python -m pip install -r repro/requirements-memory.lock
python tools/bootstrap_memory.py
python -m algomini memory export --semantic
```

The cloud image creates this environment and downloads only checksum-pinned model files. Embedding subprocesses receive knowledge text and a sanitized environment. Explicit semantic commands fail if their optional model/environment is missing; ordinary local search uses keywords when unavailable.

## Coding and configuration

```bash
python -m algomini bootstrap-muse
python -m algomini doctor
python -m algomini versions
```

`doctor` checks the configured coding environment; `--check-api` performs only an authenticated model-catalog GET. `versions` writes `.cache/environment.json` without replacing committed locks. Keep API credentials in ignored `.env` or process environment, and in repository secrets for Actions. Do not put credentials in dataset URLs or reports.

`cloud/policy.json` owns request limits, the provider ceiling/reserve, size target and baseline threshold. `run.toml` owns runtime limits, baseline commands, coding CLI/model/pricing and compatibility budget phases. Its schema-1 split/probe validation fields remain for compatibility; cloud development views are described explicitly in each `sampling.json`. Preserve budget, final-claim and containment guards when extending the system.

The coder boundary is configured by command arguments and a usage adapter in `algomini/coder.py`; the provider catalog preflight is in `environment.py`. A different provider/CLI needs the corresponding preflight and usage parsing, while retaining independent native compilation, budget holds and exact reconstruction. Research procedures and tool extension are documented in [the laboratory guide](laboratory.md).

## Before merging and pushing

Run the maintained tests, Cargo formatting and workflow lint. Pinned Actions use GitHub's queued concurrency; actionlint 1.7.12 needs the narrow exception for the newer `queue` field:

```bash
cargo fmt --check --manifest-path crates/algomini-cli/Cargo.toml
.tools/actionlint/actionlint -ignore 'unexpected key "queue" for "concurrency" section'
```

After merge/push, let CI and Cloud smoke qualify the new image. Start a blank-issue `reuse` rehearsal before a new paid campaign. Local native success does not prove the cloud kernel/image configuration. No local command here starts a GitHub campaign or publishes a release.
