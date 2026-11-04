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

The two issue fields are **Plugin name** (for example `json-v1`) and **Dataset archive URL**. Supply a public HTTP/HTTPS link that downloads a ZIP. Any web host, branch URL, signed query and ordinary redirect is accepted; no host allowlist or commit-pinned URL is required:

```text
https://github.com/akaliutau/algomini-datasets/raw/refs/heads/main/gutenberg.org/books2.zip
```

Put at least two data files in the ZIP. You can create one from the existing regression fixtures:

```bash
python -m zipfile -c /tmp/samples.zip datasets/sample/train-1mb.json datasets/sample/test-512kb.json
```

Upload the ZIP to a public web location, copy its download link, and open **Develop a compression plugin**. A repository writer/maintainer/admin starts the workflow automatically; otherwise a maintainer adds `codec-run`. The workflow checks actual repository permission and sets a title such as **[codec] Develop plugin: json-v1**. One issue is one immutable request; edits, comments or reopening do not restart it.

The intake job downloads and validates the ZIP, then generates `dataset-manifest.json` automatically:

- With two data files, the larger is development data and the other is final-test data. Equal sizes use path order as a deterministic tie-breaker.
- With more files, approximately 70% go to development and 30% to final, by file count. The order is deterministic; exact duplicate files stay together, so the proportion can vary. At least two distinct files are required.
- Extensions infer `json`, `csv`, `xml`, `text` or `binary`; mixed/unknown formats use `binary`. A family is research guidance, not a restriction on exact reconstruction.
- Each partition sorts its members by path and concatenates their original bytes without separators. The manifest retains file names, sizes, individual hashes and ordered boundaries. This uses the existing single-input research engine; it does not change the Rust archive format.
- The ZIP download is at most 128 MiB, with 2–64 data files, at most 128 MiB expanded in total, and at most **64 MiB per file and partition**. README/license files, hidden entries and macOS metadata are ignored. Nested folders are supported; archive paths are never extracted onto the filesystem. Links, traversal, duplicate paths, encryption and unsupported compression methods are rejected. Use ordinary stored/deflated ZIPs.

The generated manifest includes the archive hash, member hashes, sizes, split policy and source URL. A license is not inferred: it is marked unspecified for upstream review. The result links the manifest so reviewers can inspect the split. Prefer independent shards or time ranges with new records; repeating a small dataset does not create independent evidence.

Only the intake job handles the complete ZIP for splitting and checksums. Development bytes and final bytes are uploaded as **separate artifacts**. Research receives development bytes only. The final artifact is retrieved by the verifier after the durable final claim. Raw inputs are excluded from checkpoint exports and PRs. Release rebuilds verify the archive hash and read only the declared development members.

Downloads use wget with redirect support, bounded size/time and the existing SHA-256 checks. The supplied URL is retained as given, including query strings and fragments. Archive links do not need a `.zip` filename: the issue field determines that ZIP bytes are expected. Each connection is restricted to public IP addresses through a local transport proxy, including redirect targets; there is no domain allowlist. The proxy connects to the IP it checked so DNS changes cannot redirect a connection into a private network. Wget receives no repository/provider credentials and TLS certificate verification remains enabled. Invalid datasets produce an issue explanation before inference or a spending reservation. Later downloads must match the recorded content hashes; changed remote bytes fail verification.

<details>
<summary>Compatibility: explicit two-file manifests</summary>

Existing issues using **Dataset manifest URL** remain supported. Their JSON descriptor has this shape; file links may use any public web host:

```json
{
  "schema_version": 1,
  "family": "json",
  "provenance": {"source": "Dataset title and upstream URL", "license": "Actual dataset license"},
  "train": {"url": "PUBLIC_DOWNLOAD_URL", "bytes": 12345, "sha256": "64_LOWERCASE_HEX_CHARACTERS"},
  "test": {"url": "DIFFERENT_PUBLIC_DOWNLOAD_URL", "bytes": 12346, "sha256": "64_LOWERCASE_HEX_CHARACTERS"}
}
```

Each original file is 0–64 MiB; the manifest is at most 32 KiB. Development and final descriptors must differ. `python -m tools.make_cloud_manifest --help` explains the existing local helper; it computes sizes/checksums and uploads nothing.

</details>

## Research and results

One repository-wide queue serializes campaigns. Each accepted request reserves $20 against shared accounting, preserving the $20 account reserve below the verified provider ceiling. Existing local budget ledgers seed a new state branch once. Unknown inference usage retains its $5 per-call reservation; estimates are distinguished from unknown holds.

The default policy allows two initial candidates, one evolved candidate and one repair, with a 90-minute research deadline. A repair uses an actual failing source artifact; infrastructure failures do not consume a repair as if they were codec bugs. The coding CLI has its own step/time limits. See [cloud/policy.json](../cloud/policy.json) and [run.toml](../run.toml).

Research uses **only the first 2 MiB of the development partition** for profiling, model training and candidate selection. That prefix and its hash/size are recorded in `sampling.json`. JSON arrays/JSONL and consistent CSV use complete record groups; duplicates stay on one side. CSV headers are retained and JSON array views wrap unchanged record lexemes. Single documents or opaque bytes have an explicit fallback sampling policy. Those views select candidates; the complete original development bytes are measured unchanged. Larger development/final files use the actual Rust client with independent 2 MiB chunks. Baselines compress each complete file as one normal stream. All framing/model bytes count, and full-file qualification still requires a win. Final content never participates in research sampling.

Prompts include relevant skills, probe findings and measured trial feedback: complete sizes, baseline gap, payload/model costs and known modes. Reviewed seeds are eligible; the report identifies reuse. A reused winner creates a **Qualify plugin** PR and may only change the registry/evidence because its Rust source is already present. A new winner creates a **Develop plugin** PR with its source/model. Losing candidates and redacted CLI diagnostics remain available. Successful and unsuccessful development observations feed later campaigns; final measurements are excluded from proposal retrieval.

A candidate must reconstruct exactly, fit the resource limits and beat the strongest complete baseline on **both validation and full development** before the final file is claimed. Otherwise the issue receives a terminal no-win report and the final input is not evaluated. Tiny inputs whose baseline is already below the native framing floor skip inference too.

For a qualifying candidate, the coordinator commits its source/model/client hashes, then consumes one durable final claim. A separate job retrieves the final input after the claim, measures complete archives, and verifies the public console after deleting its supplied original copy. No tuning follows. Positive archive gain opens one PR; failed final measurements remain reports. The 10% gain is a target, while strictly positive gain is the release threshold. Throughput is secondary; correctness and memory/time bounds remain mandatory.

Reports start with an outcome/algorithm table and use short display IDs, such as `cx-4c16b773-e3b0`. Full IDs and SHA-256 digests stay in the JSON receipts and registry; shortening the display does not rename codecs or change archive compatibility. Known algorithm summaries come from reviewed metadata and measured modes. New candidates can provide a source-comment note, labeled as such; unknown mechanisms remain described as custom Rust codecs. No extra model call is used for reporting. Source and generated-manifest links make the evidence directly reviewable.

Reports expose repeated test hashes and bounded record-overlap diagnostics. No exact record overlap is a useful check, not proof of statistical independence. Previously examined sample files remain regression evidence even when a newer codec compresses them better.

## Retry, interruption and rehearsal

Remote Git updates use conditional fast-forwards. Request identity is repository plus issue number, with a run/attempt lease for paid work and final access. Duplicate deliveries reuse the existing result. A new Actions attempt cannot reuse an old execution claim to infer again or reopen a consumed final file.

When only publication failed, rerun that publisher job while its artifacts are retained. It can recover a branch/PR or partial draft release without generation or test access. If research disappeared before producing usable terminal artifacts, retain the exposure and inspect provider usage before reconciliation. Create a new issue for new work; preserve existing test receipts. Artifacts have limited retention; durable reports, source and accounting live in Git. The state file has a 2 MiB bound, and recent cross-campaign development context is bounded to 128 records.

For a zero-inference rehearsal, create a **blank issue without `codec-request`**, using the same two Markdown headings. Then dispatch **Actions → Codec request**, supplying that issue and `mode: reuse`. The special issue form starts normal generation automatically, so use the blank issue for this rehearsal.

## Merge, release and install

Merging a qualified registry update triggers **Release qualified codecs**. It rebuilds the exact catalog in the recorded image, checks the executable hash and development archive fingerprint, and verifies reconstruction without opening the final file. Publication requires the durable consumed claim, a merged PR and a complete checksum set. Partial drafts recover only when existing assets match their digests.

Release titles use **Algomini v0.3.1** (for example), taking the Rust client version from the frozen source. Replaying publication also corrects older issue-based titles. The `codec-issue-N` tag identifies the frozen source snapshot reviewed through that PR. Its release contains the Linux x86_64 binary, registry, report, manifest and `SHA256SUMS`. Cargo installs that source tag as shown in the [README](../README.md). The issue comment links the release and install command; closing the issue is optional.

Each tag contains the codecs known in that qualified snapshot. Updates use another Cargo installation for the new tag. Runtime module downloads, crates.io publication, streaming with bounded memory beyond the 64 MiB PoC cap and other OS targets remain future work. `Cloud smoke` qualifies the current image, App access, offline compilation and containment, plus a separate zero-inference provider catalog check. CI runs every maintained Python/Rust test with no model credentials.

The 64 MiB file path requires client 0.3.1 and a campaign qualified after that change. Older pinned release tags/images keep their original limits; merging the client change does not rewrite previous qualifications.

Large-file CLI measurements run in fresh contained processes with a 1 GiB address-space cap and time allowances proportional to chunk count. The bounded codec worker remains at 2 MiB / 256 MiB. Encoding and decoding use different temporary directories; only the archive reaches decoding. The implementation buffers whole files for simplicity. Record-overlap diagnostics inspect only the first 2 MiB on large inputs and disclose that scope.
