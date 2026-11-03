# GitHub cloud workflow implementation

Implemented on `cloud/github-demo`; review in [PR #1](https://github.com/akaliutau/algomini/pull/1). Operational instructions are in [docs/cloud.md](../../docs/cloud.md).

## Delivered

- A two-field issue form, real repository-permission checks, immutable input snapshots and public checksum-pinned dataset descriptors.
- One queued paid workflow with conditional Git state updates, shared reservations seeded from both existing ledgers, per-attempt execution claims and no implicit retry after uncertain spending or test access.
- Bounded development research with reviewed CSV/text seeds, distinct Muse attempts, optional repair, actual release-client compilation and size-first selection. Selected-seed reuse is disclosed; it is not described as a newly generated algorithm.
- A committed source/model/client freeze before the single final file is fetched, complete six-baseline accounting, direct correctness controls and a public-console check after deleting its supplied original.
- One upserted issue report and one reviewable PR on success; failed measurements remain reports without release qualification.
- A Cargo-installable Rust client with bundled zstd, immutable codec/model versions, named aliases and single-block ALC1/ALG2 compatibility. Existing Python tools retain historical multi-file collections.
- Merge-triggered, reproducible snapshot builds and GitHub Releases with manifests, source tags, SHA-256 checksums, existing-asset verification and recovery of partial draft publication.

The original evaluator, selected telemetry codec, closed final measurements and native worker tree remain unchanged. Docker was run only on GitHub. No paid inference or new real-corpus final evaluation was performed during this implementation stage.

## Measured qualification

The successful [cloud qualification run](https://github.com/akaliutau/algomini/actions/runs/41711938316) tested source commit `f66f91f8f5739c6fde0a63e1e022fe2a0bdfec55`. App authentication, private GHCR publication, image build/containment and the separate provider preflight all passed.

| Check | Result |
| --- | --- |
| Local complete Python suite | 123 passed |
| Local Rust client tests | 3 passed |
| Original Rust worker tests | 3 passed |
| Actual private GitHub Cargo installation | Passed |
| Encode, remove original, decode | Exact reconstruction |
| Dynamic libzstd dependency | Absent; zstd is bundled |
| Clean native rebuild | Identical executable SHA-256 |
| Generated codec with nonempty model | Built, trained, counted and reconstructed through the public CLI |
| Final-test guard | No test fetch before claim; duplicate access rejected |
| GitHub retry guard | Old run/attempt cannot authorize another paid/test execution |
| Provider preflight | Authenticated catalog, selected model available, zero inference |
| Worker file/network/process access | Denied as required |
| GitHub queued concurrency syntax | Accepted by the live service |

The cloud host reports AMD EPYC 7763, two available CPUs, Linux `7.0.0-1017-azure`, Debian 12 container userspace and Python 3.12.16. The exact qualified image is:

```text
ghcr.io/akaliutau/algomini-lab@sha256:794aae57c654e1de19ff28273ea1eba045aadc2807a628878bea7f2947948c63
```

[github-qualification.json](github-qualification.json) binds Rust/compiler identities, source hashes and the containment/console checks. [github-checks.json](github-checks.json) identifies the jobs; [provider-preflight.json](provider-preflight.json) records only safe status/model fields. [local-validation.json](local-validation.json), [local-cargo-install.json](local-cargo-install.json) and [private-cargo-install.json](private-cargo-install.json) preserve the local checks.

Cloud CI deliberately deselects two historical installed-package tests whose binary/library pins belong to the original local reference. The full local suite includes them. The remaining Python contracts, new cloud native integration and both Rust packages run in CI. This preserves historical evidence instead of rewriting its pins for a different container.

The first hosted runs identified ordinary integration issues: capability-dropped root could not write runner-owned mounts; atomic JSON files were not readable by the host artifact uploader; the slim Python image lacked the legacy `/usr/bin/python3` path. The fixes grant access only to the job's work/output mounts, make the intended reports readable and provide the interpreter path. Landlock/seccomp, memory/time limits, read-only images and dropped capabilities remain enabled.

## First operational request

Merge the implementation PR to activate issue events on the default branch. Start with a blank issue without `codec-request` and dispatch `mode=reuse` for a zero-inference rehearsal, as described in the guide. Use the special two-field form for normal generation. Supply a public immutable manifest referring to the chosen development/final files; the private code repository does not make private raw dataset URLs anonymously downloadable.

The live paid issue → final measurement → bot PR → merged release path is intentionally not triggered by the implementation tests. It becomes available after human merge and an authorized request. The model account's existing ceiling and shared spending ledger govern that run. Research failures, a missed size target and incomplete claims remain visible; the pipeline never manufactures a benchmark win.

Each issue release is the exact qualified source snapshot, created only after its PR merges. It contains the codec versions known in that snapshot; it is not a runtime downloader or an automatically refreshed aggregate of every later merge. Larger-file streaming, independent plugin crates, crates.io publication and additional operating systems remain outside the 24-hour demo.
