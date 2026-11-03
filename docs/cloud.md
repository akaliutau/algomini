# Running codec requests on GitHub

The implementation uses GitHub Actions, a digest-pinned laboratory container and the Rust `algomini` client. It follows the [cloud demo plan](github-cloud-demo.md), keeping the original local evaluator, selected codecs and sealed results intact. Docker runs on GitHub only; local Python/Rust tests require no Docker.

## Setup after merging

The repository is `akaliutau/algomini` and is currently private. Configure these repository Actions secrets; the client ID can alternatively be an Actions variable:

| Name | Value |
| --- | --- |
| `ALGOMINI_APP_CLIENT_ID` | GitHub App client ID. |
| `ALGOMINI_APP_PRIVATE_KEY` | Complete PEM private key. |
| `META_API_KEY` | Existing dedicated Meta credential with the verified provider spending ceiling. |

Install the App on this repository with Contents, Pull requests and Issues read/write. No separate bot account, OAuth client secret, webhook server, Docker Hub account or crates.io token is required. The workflows mint installation tokens in coordinator/publisher jobs only. Research gets the Meta key alone; final verification and release builds get neither credential.

Enable Actions and grant its image job permission to publish GitHub Packages. GHCR access uses the job's automatically issued token. Keep the generated package linked to this repository. The first `Cloud smoke` run creates `codec-request`/`codec-run` labels and checks App access, offline compilation, fresh-process reconstruction and file/network/process denial. A separate job checks the Meta model catalog, with zero inference. It also publishes a private laboratory image tagged by source commit, then records its immutable digest. Set GitHub compute/storage budgets separately from the provider cap.

Require the `CI / checks` status and human review before merging codec PRs. Allow the App to write `automation-state` and `codec/issue-*` branches, not bypass review on `main`. The state branch is initialized automatically on the first accepted request. Do not delete or force-reset it: it contains spending holds and test-consumption receipts.

The workflow files must reach the default branch before issue events can start requests. Use `Cloud smoke` and `CI` results as the host qualification; a successful local suite alone is not proof of Docker/kernel compatibility.

## Supply the two dataset files

The first version accepts public, raw `raw.githubusercontent.com` URLs pinned to a full 40-character commit. Each file is at most 2 MiB. Dataset files and the manifest must be publicly retrievable without the repository bot's credentials; the code repository can remain private. No archive extraction or dataset-supplied commands run. Keep raw files out of this code repository.

For example, place `train.xml` and `test.xml` in a separate public dataset repository, retain provenance/license information, and obtain their committed raw URLs. Create the manifest locally:

```bash
python -m tools.make_cloud_manifest \
  --family xml --train /path/train.xml --test /path/test.xml \
  --train-url https://raw.githubusercontent.com/OWNER/DATASET/TRAIN_COMMIT/train.xml \
  --test-url https://raw.githubusercontent.com/OWNER/DATASET/TEST_COMMIT/test.xml \
  --source 'Dataset title and upstream URL' --license 'Applicable upstream license' \
  --output /tmp/codec-manifest.json
```

Use actual full commit hashes in place of `TRAIN_COMMIT`/`TEST_COMMIT`. This helper hashes the test file for its descriptor; it does not profile or select a codec from it. Publish this small JSON descriptor to the public dataset repository too, then copy its immutable raw URL. Candidate research never downloads the final file before its durable claim.

Create an issue using **Build a compression codec**, with only:

1. A logical plugin name such as `wiki-xml` or `telemetry-lab`.
2. The manifest URL.

A user with actual repository write/maintain/admin permission starts immediately. Otherwise a maintainer adds `codec-run`. Editing, reopening or adding other comments/labels does not restart the request. One issue is one immutable request; create another issue for new inputs. Reusing a recorded test hash is disclosed in the receipt/report and is never described as new unseen evidence.

## What the workflows do

`Codec request` authorizes and validates the issue, qualifies/fetches the cloud image, reserves $20 durably, and starts a fresh campaign. Both existing local ledgers seed the shared accounting once; the $150 provider ceiling retains a $20 account reserve. Subsequent cloud accounting comes from `automation-state`, not checkout caches. Coder calls retain the existing $5 per-call reservation and unknown-usage holds. The research job has a 90-minute deadline and at most two initial, one evolved and one repair session. See [cloud/policy.json](../cloud/policy.json).

The development file is split deterministically in half: the first half supplies training/model/dictionary bytes, the remainder selects among candidates. The complete development file is also reported after selection. Very small samples reuse their nonempty training bytes for the control probe and make no independent validation claim. Preparation retrieves at most four skill/evidence excerpts with the existing context bounds. The container uses keyword retrieval when the optional local semantic environment is absent; the portable semantic index and reviewed skill library remain committed and available for later exports.

Reviewed CSV/text codecs are seeds. Muse can improve or replace them. If the seed wins, the report explicitly calls that reuse; paid calls and failed candidates remain visible. New codecs are compiled into the real release client. Direct tiny tests bypass raw fallback. Reference measurements include gzip, xz, zstd levels 19/22 and both dictionary configurations; all frames, labels and shared models count. If the development prefix is too small for statistical dictionary training, the reference uses a counted raw prefix dictionary and discloses that fact.

After selection, the coordinator commits the source, model, client/build hashes and development evidence on `codec/issue-N`, then durably consumes the final test. A separate job fetches that one file and measures the exact executable. Its public `compress` command must emit the same counted archive; `decompress` reconstructs it in a fresh restricted process after the supplied original is deleted. No tuning follows. A size win opens a PR; failures still update the issue and retain terminal evidence without proposing a release.

The acceptance policy requires exact reconstruction, the existing memory/time caps and strictly fewer complete archive bytes than the best configured baseline. A 10% gain is a visible target. Speed is secondary, with the historical speed gate preserved as a diagnostic. This does not modify the closed telemetry experiment's gates.

## Retry and cancellation behavior

The authoritative identity is repository plus issue number. Remote Git updates are conditional fast-forwards. Duplicate events return the existing report/branch/PR; ordinary concurrency does not replace durable state. GitHub's repository-wide queue serializes paid campaigns. The `queue: max` field follows current GitHub syntax; actionlint 1.7.12 needs a narrow exception for that newer field.

Each spending/test authorization also records its Actions run and attempt. “Re-run failed jobs” cannot use an earlier attempt's claim to generate again or re-open the final file on a fresh runner. A lost run preserves its exposure and test receipt. If complete terminal artifacts exist, publication can recover without inference or test access. Otherwise the issue reports interruption; inspect provider usage and the durable record before reconciling it. Do not delete consumed-test markers to retry a failed measurement.

Artifacts are transports with limited retention. Frozen source/model/evidence and final reports are committed; the qualified binary and encoded collections stay in Actions artifacts until release. `Publish` runs after ordinary job failures. If the whole workflow disappears, a later delivery records the interruption without starting more research.

## Merge, publish and install

Merging a qualified registry update triggers `Release qualified codecs`. It rebuilds the exact catalog in the recorded container, checks the executable hash and the development archive fingerprint, and proves console reconstruction without reading the final file. The publisher verifies the durable consumed claim and merged PR, creates `codec-issue-N`, and publishes the binary, source/model registry, report, manifest and `SHA256SUMS`. An existing asset must match its digest; it is never overwritten with different bytes.

Each issue tag points to the exact frozen source commit reviewed through the merged PR. This keeps the Cargo install and release executable aligned even when later merges extend the main catalog. Release creation happens only after the qualifying PR merges. The issue comment links the release and prints its install command. It remains open unless a user closes it.

For this private repository, authenticate Git once before installing source:

```bash
gh auth login
gh auth setup-git
CARGO_NET_GIT_FETCH_WITH_CLI=true cargo install \
  --git https://github.com/akaliutau/algomini \
  --tag codec-issue-123 algomini-cli --locked --force

algomini plugins
algomini compress --plugin wiki-xml sample.xml sample.alc
algomini decompress sample.alc restored.xml
```

Replace `123` with a released issue number. Source installation needs Rust 1.92+ and a C compiler. Compression needs no Python, model key or network. Updates use another install command for the new tag. The first CLI supports single-file inputs up to 2 MiB and refuses to overwrite output. Historical multi-file collections retain their Python utility. Publishing to crates.io, runtime module downloading, streaming larger files and other OS targets remain outside this demo.

## Rehearsal and checks

For a zero-inference rehearsal, first create a **blank issue without the `codec-request` label**, using the same `### Plugin name` and `### Dataset manifest URL` headings. Then select `mode: reuse` under **Actions → Codec request → Run workflow** and supply that issue number. The special issue form starts normal generation automatically, so it is not the way to prepare an unclaimed rehearsal issue. Reuse mode uses reviewed seeds and says so in the report. A claimed issue never starts a second research run, whichever mode is subsequently selected.

Local checks, with no Docker:

```bash
python -m pytest -q
cargo test --offline --locked --manifest-path crates/algomini-cli/Cargo.toml
python -m algomini.cloud smoke --workspace .cache/cloud-smoke
```

CI additionally executes the original Rust worker tests inside the cloud image. Two historical installed-package integration tests require the original local binary/library pins and are deselected in the new cloud reference; they still run in the full local suite. New native cloud tests prove equivalent console reconstruction for the new runtime. Coverage includes malformed/public URL rules, shared budget/CAS behavior, duplicate claims, retry-attempt rejection, old/new frame compatibility, deterministic binary rebuilds, a real reusable campaign with test-fetch denial until freeze, one final read and output integrity. No test needs a model credential.
