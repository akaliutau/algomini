# GitHub workflow

The repository is `akaliutau/algomini`. Merge the implementation before pushing and starting a new request: issue events use workflows on the default branch. Docker runs on GitHub; local development uses Python and Rust directly.

## Credentials

Supply repository Actions secrets:

| Name | Purpose |
| --- | --- |
| `ALGOMINI_APP_CLIENT_ID` | GitHub App client ID; an Actions variable with the same name also works. |
| `ALGOMINI_APP_PRIVATE_KEY` | Complete private-key PEM for that App. |
| `META_API_KEY` | Dedicated Meta credential with the verified $150 provider ceiling. |

Install the App on this repository with Contents, Issues and Pull requests read/write, plus Metadata read. A separate bot account, webhook service, OAuth client secret and crates.io token are unnecessary. The normal Actions token publishes the private GHCR image. Keep the package linked to the repository and allow its Actions jobs to read/write packages.

Coordinator and publisher jobs mint App installation tokens. Research receives the model key, and its coding process is isolated. Final verification and release builds receive no model key or App token. Native workers have no network access and cannot read original files or parent credentials. Configure GitHub compute/storage budgets separately from the provider limit.

Require CI and human review before merging codec PRs. The App needs access to `automation-state` and `codec/issue-*` branches; it does not need to bypass main review. Do not delete or reset `automation-state`: its spending records and consumed-test receipts are authoritative.

## Dataset and issue

The two issue fields are **Plugin name** and **Dataset manifest URL**. The manifest defines the format hint and two files:

```json
{
  "schema_version": 1,
  "family": "json",
  "provenance": {"source": "Dataset title and upstream URL", "license": "Actual dataset license"},
  "train": {"url": "IMMUTABLE_PUBLIC_URL", "bytes": 12345, "sha256": "64_LOWERCASE_HEX_CHARACTERS"},
  "test": {"url": "DIFFERENT_IMMUTABLE_PUBLIC_URL", "bytes": 12346, "sha256": "64_LOWERCASE_HEX_CHARACTERS"}
}
```

Use real values. Supported families are `json`, `csv`, `xml`, `text` and `binary`; the family guides investigation rather than restricting codec correctness. Each file is 0–2 MiB; the manifest is at most 32 KiB. Development and final descriptors must differ. URLs must be anonymously accessible `raw.githubusercontent.com` paths pinned to a full 40-character commit, with no credentials, query or fragment. A private code repository does not grant anonymous access to private dataset files. No dataset archive extraction or dataset-supplied commands run.

Create the descriptor from existing local files:

```bash
python -m tools.make_cloud_manifest \
  --family json --train datasets/sample/train-1mb.json --test datasets/sample/test-512kb.json \
  --train-url https://raw.githubusercontent.com/OWNER/DATASET/FULL_COMMIT/train.json \
  --test-url https://raw.githubusercontent.com/OWNER/DATASET/FULL_COMMIT/test.json \
  --source 'Actual dataset title and upstream URL' --license 'Actual license' \
  --output /tmp/codec-manifest.json
```

`--train` and `--test` are local file paths used for size/checksum calculation. Their URL arguments identify those same bytes in the cloud. Publish the descriptor in the dataset repository and use its own immutable raw URL in the issue. The helper uploads nothing. Prefer different source shards or time ranges with new records. Repeating a small dataset into larger files does not create independent evidence.

The special **Build a compression codec** form starts generation when opened by a repository writer/maintainer/admin. Otherwise a maintainer adds `codec-run`. Actual repository permission is checked. One issue is one immutable request; edits, comments or reopening do not restart it.

## Research and results

One repository-wide queue serializes campaigns. Each accepted request reserves $20 against shared accounting, preserving the $20 account reserve below the verified provider ceiling. Existing local budget ledgers seed a new state branch once. Unknown inference usage retains its $5 per-call reservation; estimates are distinguished from unknown holds.

The default policy allows two initial candidates, one evolved candidate and one repair, with a 90-minute research deadline. A repair uses an actual failing source artifact; infrastructure failures do not consume a repair as if they were codec bugs. The coding CLI has its own step/time limits. See [cloud/policy.json](../cloud/policy.json) and [run.toml](../run.toml).

The laboratory profiles the complete bounded development sample. JSON arrays/JSONL and consistent CSV use complete record groups; duplicates stay on one side. CSV headers are retained and JSON array views wrap unchanged record lexemes. Single documents or opaque bytes have an explicit fallback sampling policy. Those views select candidates; the complete original development bytes are also measured unchanged.

Prompts include relevant skills, probe findings and measured trial feedback: complete sizes, baseline gap, payload/model costs and known modes. Reviewed seeds are eligible; the report identifies reuse. Losing candidates and redacted CLI diagnostics remain available. Successful and unsuccessful development observations feed later campaigns; final measurements are excluded from proposal retrieval.

A candidate must reconstruct exactly, fit the resource limits and beat the strongest complete baseline on **both validation and full development** before the final file is claimed. Otherwise the issue receives a terminal no-win report and the final file remains unopened. Tiny inputs whose baseline is already below the native framing floor skip inference too.

For a qualifying candidate, the coordinator commits its source/model/client hashes, then consumes one durable final claim. A separate job downloads the final file once, measures complete archives, and verifies the public console after deleting its supplied original copy. No tuning follows. Positive archive gain opens one PR; failed final measurements remain reports. The 10% gain is a target, while strictly positive gain is the release threshold. Throughput is secondary; correctness and memory/time bounds remain mandatory.

Reports expose repeated test hashes and bounded record-overlap diagnostics. No exact record overlap is a useful check, not proof of statistical independence. Previously examined sample files remain regression evidence even when a newer codec compresses them better.

## Retry, interruption and rehearsal

Remote Git updates use conditional fast-forwards. Request identity is repository plus issue number, with a run/attempt lease for paid work and final access. Duplicate deliveries reuse the existing result. A new Actions attempt cannot reuse an old execution claim to infer again or reopen a consumed final file.

When only publication failed, rerun that publisher job while its artifacts are retained. It can recover a branch/PR or partial draft release without generation or test access. If research disappeared before producing usable terminal artifacts, retain the exposure and inspect provider usage before reconciliation. Create a new issue for new work; preserve existing test receipts. Artifacts have limited retention; durable reports, source and accounting live in Git. The state file has a 2 MiB bound, and recent cross-campaign development context is bounded to 128 records.

For a zero-inference rehearsal, create a **blank issue without `codec-request`**, using the same two Markdown headings. Then dispatch **Actions → Codec request**, supplying that issue and `mode: reuse`. The special issue form starts normal generation automatically, so use the blank issue for this rehearsal.

## Merge, release and install

Merging a qualified registry update triggers **Release qualified codecs**. It rebuilds the exact catalog in the recorded image, checks the executable hash and development archive fingerprint, and verifies reconstruction without opening the final file. Publication requires the durable consumed claim, a merged PR and a complete checksum set. Partial drafts recover only when existing assets match their digests.

The `codec-issue-N` tag identifies the frozen source snapshot reviewed through that PR. Its release contains the Linux x86_64 binary, registry, report, manifest and `SHA256SUMS`. Cargo installs that source tag as shown in the [README](../README.md). The issue comment links the release and install command; closing the issue is optional.

Each tag contains the codecs known in that qualified snapshot. Updates use another Cargo installation for the new tag. Runtime module downloads, crates.io publication, streaming beyond 2 MiB and other OS targets remain future work. `Cloud smoke` qualifies the current image, App access, offline compilation and containment, plus a separate zero-inference provider catalog check. CI runs every maintained Python/Rust test with no model credentials.
