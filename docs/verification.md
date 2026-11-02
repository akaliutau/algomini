# Stage 5: sealed verification and compiled installation

The Stage 4 candidate freeze `487251797c2b5facec5d9cd36a5621b9f119169728676b421cd93ceed2078f18` selects one source, model, parameter set and executable. Stage 5 adds a separate trusted verifier without changing the Stage 2 evaluator or selected algorithm. This stage needs no model credential, model inference, new dependencies or local Docker execution.

## Before opening held-out data

Run the tiny synthetic tests, then pin the implementation:

```bash
python -m pytest -q
python -m algomini selected-status --build
python -m algomini pin-sealed-verifier
```

`repro/sealed-protocol.json` and its SHA-256 bind the new verifier, package builder, installed runtime and post-seal generation guard. They also bind the previously frozen evaluator/candidate, private manifest commitment and training-only baseline dictionary. Pinning reads development artifacts only. Commit these files before the first sealed invocation; the receipt records that Git commit. The reviewed commit/protocol hash must be trusted independently. Adjacent checksums detect changes but cannot authenticate a package against someone able to replace both content and checksums.

## One final measurement

```bash
python -m algomini stage5
```

The verifier checks the source/model/compiler/evaluator pins and runs direct correctness checks on tiny development cases. It then exclusively creates and fsyncs `reports/stage5/consumed.json` before opening the private manifest. A process lock serializes callers. It checks the manifest commitment, every listed path, input bound, size and SHA-256, then evaluates **all** committed entries in their original order. There is no test profiling, probe selection, subsampling or candidate training. The frozen zero-byte model is loaded as an artifact. Each compression invocation remains at most 256 KiB for these prepared chunks, below the 2 MiB global cap.

The unchanged evaluator supplies all six baseline configurations, the structured reference, complete ALC1 byte accounting, fresh-process decode measurements and the four acceptance gates: exact reconstruction, at least 10% archive gain, at least 10 MiB/s on every chunk, and at most 256 MiB candidate RSS. Baseline dictionary training uses the original training split only; its checksum must match the pre-seal pin. The saved collection is decoded again with its counted model. Native decoders receive compressed bytes/model through stdin inside Landlock/seccomp containment; original files, credentials and networking are inaccessible.

The verifier saves `result.json`, `terminal.json` and, when evaluation completes, `archive.alc`. Terminal hashes bind the outcome and receipt; replay also checks the saved archive. Failure or interruption consumes the run. A failed gate yields a research report and no installation; an incomplete claimed run refuses retry. Do not delete receipts, substitute candidates, revise parameters or reuse these inputs as unseen evidence. New coder requests are blocked after consumption; previously completed generation records remain readable. Repeating `stage5` returns the stored outcome and may repeat packaging/decode checks, but never reads or measures the held-out inputs again.

## Successful package and clean decode

Only a result passing all four gates can produce `packages/<candidate-id>/`. The package contains the compiled worker, source, frozen model, candidate freeze, sealed evidence, attribution, a small standard-library Python runtime and a file-hash manifest. Installation checks a separately supplied manifest SHA-256 before copying or executing package code. No compiler is needed by the installed runtime. Linux x86_64, Python 3.12+, Landlock ABI >=6, libseccomp and the pinned native libzstd/libseccomp hashes are required. The bundled source supports audit; recompiling remains a separate reproduction step.

Use the reviewed package id printed in `reports/stage5/summary.json`:

```bash
python -m algomini install-codec packages/<candidate-id> installed/<candidate-id> --package-id <reviewed-manifest-sha256>
python -I -B installed/<candidate-id>/decode.py --plugin installed/<candidate-id> verify
python -I -B installed/<candidate-id>/decode.py --plugin installed/<candidate-id> encode input.csv output.alc
python -I -B installed/<candidate-id>/decode.py --plugin installed/<candidate-id> decode output.alc decoded/
```

Encode accepts one input of at most 2 MiB. Decode rejects malformed collections, wrong codec/model identities and existing output files. Stage 5 copies the package and saved archive to a temporary installation, starts isolated Python imports in that directory, decodes every held-out block and compares the recorded hashes. It also deletes a synthetic original before installed decode. The native workers retain the operating-system isolation boundary; changing Python's working directory alone would not establish filesystem isolation. `install-check.json` records this proof. Only the selected plugin manifest is promoted to `verified`; the trusted core stays frozen.

Archive gain and deployment saving are distinct. The frozen score charges the complete incremental executable under the existing-common-runtime assumption. The report additionally charges the entire standalone bundle, including Python files, source and evidence. Negative deployment savings must remain visible. These held-out periods come from one house; the result does not establish performance on unseen houses or scientific novelty. Timing on a cloud host remains unmeasured.
