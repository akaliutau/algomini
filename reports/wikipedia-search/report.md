# Wikipedia compression optimization

The selected text codec produces a **260,727-byte complete archive**, compared with **290,783 bytes for xz -9e**, on the previously examined first 1,000,000 enwik8 bytes: **10.34% smaller**. Exact reconstruction passes. Median fresh-process decode throughput is **2.18 MiB/s**, peak decode RSS **69.13 MiB**, and encoding takes **0.68 seconds**. This is a win against the six configured baselines on a development benchmark, not a claim about all Wikipedia or the best published enwik8 compressor.

After selection was committed, the same frozen codec produced **272,671 bytes versus xz's 302,421** on the reserved later 1 MB window: **9.84% smaller**, with exact reconstruction. It wins on size but misses the declared 10% target on this window. No source, parameters, model or selection changes followed that result.

The user explicitly made speed a secondary metric. This campaign ranks complete archive bytes first and throughput second for equal sizes. Exact reconstruction, the 256 MiB worker cap, and 30-second encode/10-second decode deadlines remain mandatory. The old telemetry 10 MiB/s gate is reported as false; its `meets_development_targets=false` field remains intact. Wikipedia eligibility follows the separate committed [objective](objective.json). The 10% archive target is met.

| Method | Complete archive bytes |
| --- | ---: |
| Selected adaptive PPM | **260,727** |
| xz -9e | 290,783 |
| zstd -22 | 300,113 |
| zstd -19 | 300,120 |
| zstd dictionary -19 | 337,023 |
| zstd dictionary -22 | 337,023 |
| gzip -9 | 355,402 |

The counted candidate contains 65 bytes of ALC1 collection metadata and a 260,662-byte ALG2 block, including its 40-byte native frame, mode byte, arithmetic-stream finalization and integrity checksum. The shared candidate model is empty. Its standalone executable is 389,232 bytes, accounted separately: deploying it for just this 1 MB input costs **359,176 net bytes** beyond the archive saving. Repeated use may amortize that executable; this run does not establish the break-even point across workloads.

## Mechanism and search

Muse produced sparse partial-match byte contexts with exclusion and arithmetic coding, then shared escape estimation. Host changes fixed an empty-input defect and ensured only visited contexts are updated. Deterministic derivatives compared context depths 4–7, update increments, escape-estimator adaptation and additional features. The selected source uses order six, increment two, frequency rescaling at 16,384, and escape buckets based on context order, visible symbol count, mean frequency, exclusion and the previous byte's lexical class. Decoding rebuilds the same adaptive state from decoded bytes. There are no hard-coded Wikipedia strings or external candidate training data. Raw/zstd fallback remains available.

Selection stopped after **88.03 minutes**, within the declared 120-minute search budget. Two coder sessions, each containing two model calls, cost an estimated **$0.25905695** from reported token counts and configured prices. The new shared ledger carries forward the original campaign's spending/reservations and preserves the account reserve. No Docker was used.

The first generated candidate failed empty-input controls. A mistakenly launched full development measurement was retained as **ineligible diagnostic evidence**; no reserved data was opened. All subsequent authoritative evaluations require tiny controls first, followed by exact native and counted-archive reconstruction. All 16 eligible native evaluations, explicit source lineage and the byte-first ranking are retained in [selection.json](selection.json), [candidates/](candidates/), and [evaluations/](evaluations/).

Useful ablation: the order-five model without shared escape estimation used 268,960 bytes; the generated shared estimator used 263,746. Order six used 263,655; adding lexical classes and extended mean buckets reached 260,727. Word dictionaries, case folding, byte-pair preprocessing and simple BWT/MTF variants did not improve enough. The optional Python entropy probes are predictive research evidence, not measured compressor results. Original generated source, host derivatives, prompts, hashes and rejected ideas are preserved.

## Frozen later-window verification

The first 1 MB prefix was already known and used for selection. The zstd reference dictionary was trained separately on bytes 1,000,000–2,048,576; its actual 8,192 bytes are included in dictionary archives. The selected candidate uses no dictionary. The reserved final window is bytes **10,000,000–11,000,000** of the same enwik8 member, as declared before search.

The exact source, empty model, build, development result, baseline policy, corpus manifest, objective, evaluator and final verifier are pinned by [the freeze](../../repro/wikipedia-freeze.json). The verifier requires that exact freeze to be committed before access, durably claims the run before reading the window, measures all six baselines with the same dictionary and preserves a single terminal outcome. Interruptions and failures consume the attempt. A cached replay never reads the window or measures again. No tuning follows the final outcome. This checks transfer to one later window of a public corpus, not independent generalization.

The pre-verification commit is **`ebc1473`**, and the frozen protocol ID is `36e4985c50d3d18a6faa93e4558136804ade4ae3c2128114ca4802f6f9ee15ca`. The [final result](verification/result.json), [consumed receipt](verification/consumed.json) and [terminal checksums](verification/terminal.json) preserve the only attempt. The input SHA-256 is `bce2c189245585a8ab00d7059b064449ac49c040470e92fc4a8c6911fc6a045e`.

| Window | Candidate bytes | Best baseline bytes | Gain | Decode MiB/s | Peak decode RSS |
| --- | ---: | ---: | ---: | ---: | ---: |
| Development, offset 0 | 260,727 | 290,783 | 10.34% | 2.18 | 69.13 MiB |
| Final, offset 10,000,000 | 272,671 | 302,421 | 9.84% | 2.37 | 72.21 MiB |

Both windows meet exact reconstruction, memory and hard time limits. The final size-first win is true, its 10% gain gate is false, and the legacy speed gate remains false. The result retains all these distinctions. Shared retrieval receives development findings only; the final-window observation is marked `split=test` and excluded from its catalog.

## Reproduce and use

The native runtime, toolchain and original worker code are bound by the existing evaluator freeze. [environment.json](environment.json), [the search policy](../../wiki-search.toml), generation records and the original dependency locks capture versions and limits. Optional exploratory suffix-array probes use [wikipedia-text-lab.lock](../../repro/wikipedia-text-lab.lock) in a separate environment; they are not runtime dependencies. Public fixture preparation is described in [the dataset guide](../../datasets/wikipedia/README.md).

```bash
python -m algomini versions --check
python -m algomini.wiki_benchmark plugins/wk-3169dda381d9c072/codec.rs
python -m algomini.wiki_verify status
python -m pytest -q
```

`wiki_verify run` performs the one final attempt when unconsumed; after completion it replays the stored outcome. Keep the committed receipt and terminal result when reproducing the published evidence. Independent fresh measurements should use a separate checkout/output namespace and explicitly declare their scope.

The source plugin is `wk-3169dda381d9c072`. The existing plugin encoder/decoder can select it by ID; it remains experimental and separate from the installed telemetry package. No telemetry freeze, sealed evidence or installed release was replaced. General release packaging and cloud execution remain later work.

For a local input of at most 2 MiB, use the dedicated native evaluator entry point. These commands require the pinned local compiler/runtime, make no model calls and refuse to overwrite output:

```bash
python -m algomini.evaluator encode wk-3169dda381d9c072 sample.xml sample.alc
python -m algomini.evaluator decode sample.alc restored/
```

Validation: **97 Python tests passed**; evaluator, telemetry candidate and environment version checks passed. An actual replay with corpus access and benchmark calls denied returned the stored final result unchanged. The updated skill and development observations were exported with the portable semantic index; final-window evidence is excluded from proposal retrieval.
