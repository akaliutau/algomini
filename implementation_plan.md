**Algomini — proposed implementation plan for the Meta hackathon**

Reviewed on 2 November 2026 against `doc_design.md`, `event_rules.txt`, and `eng_best_practice.md`. The repository currently contains setup files and documentation; there is no runtime, evaluator, or search implementation to extend. This is a proposed build plan, not a claim that discovery or compression gains have already been demonstrated.

Recommend **Indie Developer / Push the Frontier**. Meta describes the Indie criteria as creativity, technical ambition, and whether the project works. The strongest submission is a working experiment in which Muse proposes a mechanism, measurements expose a weakness, Muse improves it, and a verified module extends the installed compressor. [Official event page](https://dev.meta.ai/events/global-hackathon).

The published deadline is **4 November, 21:00 ET**, which is **5 November, 02:00 London time**. Aim to submit by **4 November, 20:00 London time**. Use a 40-hour core schedule with eight hours of contingency; longer searches must fit inside that contingency. [Event dates](https://dev.meta.ai/events/global-hackathon).

**Changes needed in the design before implementation**

1. **Bring discovery forward.** The current schedule spends 16 hours on infrastructure before Muse population search. A first generated, compiled, measured codec must work by hour 6. The frozen wrapper and laboratory grow around that complete path.
2. **Resolve merge ownership.** The design's automatic merge conflicts with `eng_best_practice.md:8`. Deliver each stage on its own Git branch, with a reviewable diff, checks, and a merge request for the user. The hackathon publisher creates the module PR; the user merges it. Describe that boundary accurately in the demo.
3. **Specify the missing contracts.** Freeze the candidate type, worker protocol, model-training entry point, frame format, limits, and scoring formula. The two encode/decode functions alone do not define optional training or reproducible execution.
4. **Make the sealed test inaccessible.** A split label is insufficient. Neither the coder nor search workers can mount the sealed files. Build, training, encode, and decode all execute with containment; a fresh process alone does not prevent a decoder reading originals from disk.
5. **Separate archive gains from deployment gains.** Count model bytes, framing, exceptions, and raw blocks. Report the incremental decoder package separately and show whether its cost erases savings on the measured corpus. Never present an amortized estimate as a measured large-file result.
6. **Budget for proof and presentation.** The current plan leaves the required three-minute video implicit. Preserve time for the public README, contribution record, reproducibility, and video. Embeddings, a cloud deployment, and a package marketplace can follow the event.

**The result to aim for**

An issue describing a real telemetry workload starts one command. Muse generates and evolves Rust code. The system selects one validation winner, freezes its artifacts, and evaluates it once on independent unseen sessions. A successful result produces a plugin PR and a report with at least 10% fewer archive bytes than the strongest declared baseline, exact reconstruction, and acceptable resource usage. Installation works through manifest discovery without editing the frozen core.

Show a simple lossless domain transform followed by zstd as an additional reference. This tests whether the generated mechanism adds value beyond an obvious representation change. A compression win supports a claim about the tested distribution; scientific novelty needs a separate prior-work comparison. AlphaCode supports the generation/filtering/selection pattern, not an expectation that 24 candidates will produce a winning compressor. [AlphaCode reference](https://deepmind.google/blog/competitive-programming-with-alphacode/).

**Stages and merge gates**

Hours are time boxes from the start of implementation. Long deterministic measurements can run unattended. Each stage ends with its artifacts and checks ready for user merge review; prepare independent later work while a review is pending.

| Stage | Hours | Concrete delivery | Acceptance gate |
|---|---:|---|---|
| 0. Establish the experiment | 0–2 | Dataset provenance and split manifest; train-only profile; pinned tools and baseline commands; `run.toml`; Muse CLI/auth preflight | Select one real, licensed data family with evidence of exploitable structure. Define byte accounting, candidate limits, spending limits, and held-out access before broad search. |
| 1. Complete the first loop | 2–6 | Minimal Rust wrapper and identity codec; Python CLI; configurable CLI coder; one Muse-generated `codec.rs`; experiment record | One command profiles a tiny sample, invokes Muse, compiles its file, round-trips it, and records bytes against a baseline. A deliberately incorrect codec is rejected. |
| 2. Freeze the evaluator | 6–10 | All declared baselines; fair accounting; contained worker execution; manifest discovery; bounded framing and raw escape; hashes of trusted code/config; small correctness suite | Identity loses on compressible input; truncation and bad lengths are handled; timeout/output limits work; decoder cannot read originals. A second codec loads by manifest without core edits. Repeat measurements agree on byte counts. Core and evaluator are then frozen. |
| 3. Search broadly with evidence | 10–18 | Four initial mechanisms, expanding toward 12 only as useful; structured profiles; candidate cache; diversity-aware elites; searchable research records | Every candidate has a hypothesis, source hash, parent, outcome, resource measurements, and per-probe sizes. Keep 3–4 distinct promising candidates. A validation improvement is the goal for the next stage. |
| 4. Evolve the strongest mechanisms | 18–26 | Up to 12 evolved candidates; deterministic parameter sweeps; minimal failure examples; ablations; usage ledger | Select one correct candidate using validation alone. Show the measured effect of its key mechanism and explain the improvement from an earlier generation. Freeze code, model, parameters, and compiler settings. |
| 5. Verify and install | 26–30 | One sealed evaluation; manifest and compiled plugin; complete results/report; clean decode/install demonstration | All unseen inputs reconstruct exactly; size and resource gates pass; all required state is counted; core hashes match. Failed verification produces a research report and no installation. |
| 6. Connect the GitHub workflow | 30–34 | Issue schema; one Actions entry point invoking the local command; branch/PR publisher; report and mechanism notes | A research issue leads to a reviewable plugin PR with evidence and an issue reference. Trusted checks pass. The user controls merge and issue closure. |
| 7. Make the submission judgeable | 34–40 | Public setup/reproduction instructions; event contribution record; final three-minute video; submission text | A clean checkout reproduces the published checks. Video shows Muse's actual role, an improvement, unseen-data results, exact decode, and installation. Public repository and video links work. |
| Contingency | 40–48 | Fixes, repeatable packaging, recording/upload issues, or additional validation-only analysis | Protect the submission deadline. Do not revise a candidate after viewing its sealed result and reuse that test as unseen evidence. |

**Stage 0: choose a tractable research problem**

Implemented locally on `stage0/local-lab-foundation`: see `run.toml`, `reports/stage0/report.md`, and `repro/README.md`. The selected source is UCI Appliances Energy Prediction, with chronological day groups, a one-day gap at each boundary, byte-preserving development chunks, and a sealed manifest commitment. The helper CLI supports preflight, version capture/checking, dataset preparation, train-only profiling, and bounded baseline measurements. At Stage 0 completion paid generation was disabled; the provider ceiling was confirmed before Stage 1, which added the initial local execution boundary. Stage 2 owns the final evaluator hardening and freeze.

Start with numeric telemetry in CSV or JSON Lines from independent devices, sessions, or time periods. Inspect training data for cross-field relationships, deltas, regime changes, repeated schemas, exceptional records, and formatting overhead. Use real data for the headline result; synthetic fixtures exercise correctness and controlled hypotheses.

The initial lab stack is **DuckDB, NumPy, SciPy, and scikit-learn**, plus `file`/libmagic, `xxd`, `strings`, and the declared compression CLIs. Add ruptures for changing regimes, Binwalk for embedded binary formats, and Kaitai Struct to validate proposed binary layouts when those questions arise. The tool catalog and probe-report contract are documented in `doc_design.md`, under **Laboratory tools and probe reports**.

Each probe returns compact JSON with its hypothesis, input/split hashes, tool versions, parameters, measured bytes/resources, counterexamples, and reproduction arguments. Keep original bytes and record/token offsets alongside parsed values. Correlations and entropy estimates guide experiments; measured validation compression determines their value.

Split by independent source/session before extracting chunks, with a target of roughly 60/20/20 for train/validation/test. Keep related or duplicate material in one split. Search may inspect train and validation; sealed test content and statistics remain outside its filesystem. Record provenance, rights, hashes, group IDs, and all sampling rules.

Use tiny fixtures first, then approximately 32–64 KiB probes, then 256 KiB samples, then at most **1–2 MiB per compression invocation**. Evaluate final sessions as bounded chunks and publish the resulting framing policy. Do not silently compare chunked candidates with a different baseline workload. Match archive boundaries and access to shared training information; report the best baseline framing configuration declared before search.

Pin gzip `-9`, xz `-9e`, zstd `-19`/`-22`, and a zstd dictionary baseline trained on training data only where applicable. Record versions and command lines. Freeze numerical CPU/RSS/timeout limits on the chosen runner before population search. A proposed candidate target is at least 10 MiB/s decode and at most 256 MiB RSS; establish a stable timing method and report startup separately. Baseline resource use is also reported.

**Stage 1–2: keep the implementation small and the boundaries explicit**

Stage 1 is implemented locally on `stage1/first-codec-loop`: `python -m algomini stage1` runs the first complete generation/build/check loop, and `check-codec` verifies committed source without a model call. The experimental Muse module is in `plugins/muse-telemetry-v1/`; its measured result and failed generation attempts are retained in `reports/stage1/` and `reports/budget/`. Local execution uses Landlock and libseccomp, with no Docker. The user confirmed the $150 provider ceiling. This functional milestone does not establish unseen-data performance or a win over the simple structured reference; Stage 2 still owns final evaluator hardening and freeze.

Use Python's standard library, existing compression CLIs, and a fixed Rust workspace. Add analysis packages only when a concrete hypothesis benefits from them. Avoid a custom framework or entropy coder during the first loop.

The generic coder adapter accepts an argument-vector command template, candidate working directory, prompt file, timeout, and expected artifact path. Run it without shell interpolation. Its output contract is a file on disk, not prose to parse. Muse supports `muse exec --prompt-file ...`, JSONL diagnostics, and model-step limits; an exit code of zero does not establish code correctness. Verify installed flags during preflight and gate on independent compilation/tests. [Muse headless execution](https://dev.meta.ai/docs/muse-code/extending#headless-and-ci).

The current `.env.example` declares the CLI's documented `META_API_KEY`. Pass it explicitly to the coder process; compiled candidates receive no model credential. Keep the model and command selectable in configuration. [Muse authentication](https://dev.meta.ai/docs/muse-code/auth).

Fix these contracts before freezing the runtime:

- Candidate exports `Candidate` implementing the required encode/decode trait. Include a training method with a default empty-model implementation, keeping stateless codecs trivial; only training samples are supplied to that operation, and all serialized output is counted.
- The trusted wrapper owns length-delimited stdin/stdout operations, resource/output bounds, framing, checksums, codec/version identification, and raw escape. Diagnostic text goes to stderr.
- Only `codec.rs` is generated. Cargo metadata, wrapper, toolkit, build commands, evaluator, and final manifest generation are trusted. Candidate dependencies are fixed and cached; builds run offline without candidate-controlled build scripts.
- Coder workspaces expose the brief, permitted development samples, and fixed interface. Build and worker containers expose only their required inputs, have no credentials/network, and cannot write the evaluator or core. Decode gets compressed artifacts and counted models, with no original-file mount.
- The supervisor owns measured results, timeout handling, original length checks, and byte comparisons. Candidate self-reports cannot determine acceptance.

Use a small, meaningful test set: empty/single-byte/random inputs; repeated and boundary-length data; lexical numeric distinctions; whitespace and line endings; exceptions and unusual records; truncated/corrupt frames; hangs and excessive output. For JSON, preserve key order, duplicate keys, numeric spellings, and other original bytes. Exercise raw escape on unsupported records. Run this set before larger measurements.

**Stage 3–4: spend model calls on research decisions**

Stage 2 is implemented on `stage2/frozen-evaluator`. `python -m algomini stage2` verifies trusted code/config/native-toolchain pins, checks bounded framing and post-freeze manifest installation, and independently repeats fixed validation measurements against all six baselines and the structured reference. Complete ALC1 collections count shared models once; parent timing/RSS and Linux containment apply to every worker. See `docs/evaluator.md`, `reports/stage2/`, and `repro/evaluator-freeze.json`. The sealed test remains unopened; generated modules remain experimental pending later validation selection and one sealed verification.

Stage 3 is implemented on `stage3/evidence-search`. Six initial mechanisms and three bounded compiler repairs yield three eligible elites, including the seed. The leading generated adaptive column/byte-plane module uses 40,108 bytes versus the frozen xz baseline's 46,253 (13.29% gain), with passing resource gates. The simple structured reference uses 40,434; causal ablations remain Stage 4 work. Train-only lexical/representation probes, every failure/lineage, source/model/archive caches, SQLite FTS5 retrieval, and two evidence-backed project skills are retained. Nine Muse sessions report $0.8288 in token-based estimates. Replay makes no new paid calls, and the Stage 2 freeze is unchanged. See `docs/search.md` and `reports/stage3/`. No sealed verification or winning installation has occurred.

Stage 4 is implemented on `stage4/refinement-freeze`. Seven deterministic controls/sweeps, three Muse revisions and one compiler repair select the smaller-package mixed-column codec at 37,130 bytes (19.72% below xz). The original plane path contributes 398 bytes at fixed level 19; a single-condition removal of the evolved mixed-column choice costs 2,921 bytes at fixed level 22. The selected source, empty serialized model, parameters, compiler profile and acceptance thresholds are frozen in `repro/candidate-freeze.json` and `artifacts/stage4-selected/`. Fresh-cache compilation reproduces the executable, frozen-model decode is exact, and replay makes no paid calls. Four refinement sessions report $0.4727 in estimates; the global four research repairs are used. The sealed test is unopened. See `docs/refinement.md` and `reports/stage4/`.

The brief should explain measured residual structure, not just generic entropy. For promising field pairs, compare residual distributions under simple predictors, conditional predictability, record states, and exceptions. Reuse existing statistics/analysis tools when they shorten the work. Let Muse propose mechanisms beyond the initial examples.

Use a deterministic state machine: profile → propose → generate → compile/check → measure → select → mutate → freeze → verify → report. Cache by codec/model hashes, input hashes, parameters, toolchain, evaluator version, and limits. Resume from completed records after interruption; do not repeat model calls because a host process restarted.

Reject broken or dominated candidates cheaply. Deduplicate exact source variants, then preserve diversity using mechanism tags and compression profiles across probes. Give the next Muse call the relevant measurements, compact source, and smallest failure example. Use Python for parameter sweeps and routine transformations. Keep one exploration slot when selecting elites rather than spending every call on near-identical changes.

Record every experiment, including failures, in versioned `reports/<run>/observations.jsonl`, with hypothesis, applicability, counterexamples, source/parent hash, input/configuration hashes, tool versions, measurements, and reproduction arguments. Promote reusable procedures during stage review into `.agents/skills/<mechanism>/SKILL.md`, with YAML name/description, investigation steps, limitations, and links to evidence. Optional references and probe scripts live beside that file. This path supports Muse's native project-skill discovery; the generic adapter can also pass selected skill bodies explicitly.

Build a derived, gitignored `.cache/lab/index.sqlite` from those records and skills. Retrieve by feature/mechanism tags and SQLite FTS5 keyword search. Add local Sentence Transformers embeddings only if keyword retrieval misses relevant evidence; cache vectors by document hash and embedding-model version and use a cosine-similarity scan for the small collection. Git is the durable source; the index is rebuildable. See **Research memory and skills** in `doc_design.md` for the full layout.

By hour 18, if nothing improves validation compression, inspect the profiles and simplest lossless representations before commissioning more population breadth. By hour 26, stop expansion and select the strongest valid result. If there is no win, publish an honest research result; do not manufacture a successful installation or tune on the sealed test.

**Scoring and claims**

For each declared baseline configuration, total the complete compressed artifacts plus any shared dictionary once per evaluated corpus. Let `B` be the smallest of those corpus totals. Let `C` be the candidate archive total, including frame/block headers, selected payloads, raw escapes, exceptions, and its shared model once. The archive gain is `1 - C / B`; the target is at least 0.10.

Report per-session results alongside the aggregate. Also report the simple structured-transform reference. Publish the incremental decoder package size `D` and deployment saving `B - C - D` under an explicit assumption that the common runtime/baseline tools are already available. If that saving is negative, show the break-even volume as an estimate and qualify the deployment claim. Keep archive performance, deployment economics, and scientific novelty as distinct claims.

Before the sealed run, write a freeze record containing source/model/config hashes, split ID, tool versions, and acceptance thresholds. The trusted verifier mounts the test data only after that record exists. Its outcome is final for that candidate; report regressions and failures as well as wins.

**Budget and stopping rules**

Keep the design's $150 allocation: $30 framework, $80 discovery, $20 refinement, $20 reserve. Start with four diverse candidates, expand to at most 12 initial and 12 evolved candidates, and allow at most four repair sessions across the run. Stop on time/cost limits or three measured rounds with no improvement.

Measure actual usage where the installed CLI exposes it and reconcile with provider billing. Reserve capacity before starting the next call. Model-step and wall-time limits bound activity, not dollars; verify an account spending ceiling or prepaid-credit behavior before treating $150 as a hard cap. Keep the reserve available for a broken integration or final repair. A fixed number of candidates does not guarantee it fits the budget.

**The three-minute demonstration**

| Time | What the judge sees |
|---|---|
| 0:00–0:20 | The telemetry storage problem, the input issue, and the measured baseline. |
| 0:20–1:00 | Muse's hypothesis and generated Rust file, followed by a real evaluation result. |
| 1:00–1:35 | A weakness or failed candidate, the feedback, and a measured improvement in a later generation. |
| 1:35–2:15 | The frozen winner's unseen-data comparison, resource figures, and exact reconstruction. |
| 2:15–2:40 | The module PR and installation through the unchanged core. Show the user merge boundary accurately. |
| 2:40–3:00 | The verified gain, Meta model contribution, public reproduction command, and what was built during the event. |

Use recorded real run artifacts to compress waiting time and label time skips or replays. The video must clearly distinguish event-built work from third-party compressors and tools. The public README should expose the results, known scope, setup, and reproduction path; the internal design/rules documents are currently gitignored. The local event rules require a public repository, an attributable new contribution, and the three-minute demo.

**Defer until after the event**

Cloud Run, a second orchestrator, universal package installation/extras, dynamic libraries, an embedding service, multiple data families, sophisticated genetic programming over skills, and automatic merging. The local CLI, Docker worker, manifest loader, durable research records, and GitHub PR provide a complete first implementation with a clear route to those extensions.
