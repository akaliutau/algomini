# Reproducibility

The maintained cloud pipeline pins its Python/Rust base images by digest in `cloud/Dockerfile`, source commits in request snapshots, and the compiled laboratory image in every qualification. Releases rebuild that exact source/model/catalog inside the recorded image and verify the executable and development archive hashes.

- `requirements.lock`: exact core build/coder/test dependencies.
- `requirements-memory.lock`: the separate optional embedding environment.
- `memory-model.json`: embedding model revision, file checksums, dimensions and library version.
- `muse-release.json`: coding CLI artifacts and checksums.
- `crates/algomini-cli/Cargo.lock`: the Rust client and bundled-zstd dependencies.
- `environment.json`: retained historical local reference; current captures are written under `.cache/`.

The image records installed OS packages and both Python environments in `cloud/image-*.lock`. Native reports bind tool/compiler hashes, driver sources, input/model identities, CPU/kernel details and counted archives. Host-dependent timing remains a measurement, not a portable constant.

Run `python -m algomini versions` for a local environment capture. See [development checks](../docs/development.md) and [historical Git revisions](../docs/history.md). Historical frozen experiments require their own revision and environment; their original reports are not rewritten to fit a newer runtime.
