# Benchmarks and evidence

The headline results are local measurements of the actual Rust client on supplied regression files. They use existing reviewed codecs, make no model calls, and do not constitute a fresh cloud final-test qualification or prove that a new algorithm was generated for each file.

## Complete-file results

| Scope | File | Input bytes | Algomini archive bytes | Best baseline archive bytes | Saving |
| --- | --- | ---: | ---: | ---: | ---: |
| Development | `Shakespeare-1.txt` | 5,422,721 | 1,434,371 | 1,656,213 · xz-9e | 13.39% |
| Known regression | `Dumas-1.txt` | 2,704,417 | 644,368 | 764,445 · xz-9e | 15.71% |
| Development | `train-1mb.json` | 1,026,294 | 8,269 | 8,801 · xz-9e | 6.04% |
| Known regression | `test-512kb.json` | 513,148 | 8,217 | 8,725 · xz-9e | 5.82% |

Saving is `1 - candidate_archive_bytes / best_baseline_archive_bytes`. The percentage compares compressed archive sizes, not input sizes or execution speed.

The text results use the `wiki-xml` codec: adaptive sparse PPM with exclusion, secondary escape estimation and arithmetic coding, with a zstd fallback. The client processes Shakespeare in three independent 2 MiB chunks and Dumas in two. The JSON results use the `json-lexical` codec, which groups raw lexemes into reversible channels before zstd compression. Original spelling, ordering and gaps are retained.

## What is counted

Archive sizes include compressed payloads, native frames, container metadata and any shared model. A shared model is stored once per archive. The complete decoder executable size is recorded separately in the artifacts; the reported saving does not subtract its installation cost.

The configured controls are gzip-9, xz-9e, zstd-19, zstd-22 and two zstd variants using a dictionary derived only from development training data. The strongest measured complete baseline is selected for each comparison. Large-file baselines compress the whole input as one continuous stream; only the candidate's file wrapper uses independent codec chunks.

Every measured archive is decoded and compared with the original bytes. The public Rust console must produce the counted archive and restore the original after its input copy is removed. Timing and peak RSS are measured outside the candidate process. Archive size ranks first; speed is secondary, while time/memory limits remain mandatory.

## Research and final data

Research uses the first 2 MiB of the development partition. It creates training/validation views for model training, candidate comparison and refinement. The selected candidate is also measured on the complete development file.

In the cloud, passing validation and full development permits a durable final claim. A separate verifier evaluates the complete final file using frozen source, model and client identities. It cannot feed that outcome back into coding. A positive archive saving can open a release PR; 10% is the target.

The local reproduction helper has a different scope: its explicit `--regression` argument opens an already-known fixture. That result is regression coverage, not a new final claim.

The JSON development fixture contains 3,168 objects but only 197 distinct lexical record groups. Its paired regression file contains the same group population. Duplicate groups stay together in development sampling, and overlap is disclosed. This dataset demonstrates reversible layout improvements; it does not establish performance on independent unseen records.

The supplied books have also been inspected and measured locally. Use new, independent data for a new acceptance claim. See [sample identities and provenance](../datasets/sample/README.md) and [the cloud evaluation protocol](cloud.md#research-and-results).

## Reproduce the measurements

Use the [local development environment](development.md). These commands invoke the actual Rust client and configured baseline tools, with no inference and no local Docker:

```bash
python -m tools.check_samples \
  --development datasets/sample/Shakespeare-1.txt \
  --regression datasets/sample/Dumas-1.txt \
  --family text --plugin wiki-xml --workspace .cache/large-text

python -m tools.check_samples \
  --development datasets/sample/train-1mb.json \
  --regression datasets/sample/test-512kb.json \
  --family json --plugin json-lexical --workspace .cache/json-check
```

Each workspace writes `result.json` containing input hashes, sampling, source/model identities, the toolchain, complete sizes, timings, RSS and console reconstruction checks. Fresh runs may change timing and executable size as the runtime evolves; recorded archive sizes belong to the source snapshots identified in their artifacts.

| Evidence | Contents |
| --- | --- |
| [Text measurements](../reports/large-files/samples.json) | Complete baseline comparisons, chunk counts, source/build identities and resource measurements |
| [Large-file validation](../reports/large-files/validation.json) | Checked limits, commands and test coverage |
| [19 MiB reconstruction](../reports/large-files/19mib.json) | Ten-chunk round trip using synthetic repeated text; not an independent ratio benchmark |
| [JSON measurements](../reports/stability/samples.json) | Complete development and known-regression measurements |
| [JSON investigation](../reports/investigations/issue-2/report.md) | Sampling, dictionary costs and empirical entropy analysis behind the layout work |
| [Qualification and release](cloud.md#merge-release-and-install) | How a cloud run's passing result becomes an installable source tag and executable |

## Interpreting a result

A smaller transformed payload may lose after adding model and framing costs. Finite-order empirical entropy is a useful probe, not a proven universal compression minimum. Independent chunks can miss repetitions across their boundaries. A win on one corpus does not guarantee a win elsewhere.

The laboratory retains unsuccessful development trials to inform later experiments. A reused codec can legitimately win a new qualification; its report identifies that origin. The coding model's weights remain fixed. The [extension guide](laboratory.md#how-improvement-accumulates) explains how evidence, skills and tools improve subsequent searches.
