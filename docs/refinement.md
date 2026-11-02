# Stage 4: measured refinement and candidate freeze

Stage 4 measures the existing Muse algorithm before revising it, then freezes one candidate using validation alone. All execution is local. The Stage 2 evaluator, native toolchain pins, baseline commands, thresholds and sealed commitment remain unchanged; no sealed file is read.

```bash
python -m algomini stage4 --measure-only
python -m algomini stage4
python -m algomini selected-status --build
```

The committed campaign replays verified outcomes without inference. Its policy is `refine.toml`: seven controlled layout/backend variants, at most three evolved mechanisms and one remaining project repair, with the original one-hour deadline retained across resumes. The generic CLI coder uses the $20 refinement allocation and the existing provider ceiling. After a candidate is frozen, missing request outcomes stop the command instead of starting new generation.

## Controlled experiments

Deterministic derivatives copy the exact Stage 3 Muse source and change only the encoder's allowed output choices or four backend-level call sites. The decoder is preserved. Every method id is 20 bytes, matching the parent, so collection-name overhead cannot explain a difference. These derivatives are labeled as host-generated experiments, not additional Muse generations.

| Layout at zstd-19 | Complete validation archive bytes |
|---|---:|
| Raw-zstd fallback only | 48,599 |
| Column-only choice | 40,506 |
| Plane-only choice | 40,537 |
| Adaptive column/plane choice | 40,108 |

Plane support saves 398 bytes versus column-only output, entirely on validation-013. Adaptation also saves 429 bytes versus plane-only output. The adaptive archive equals the controlled per-block minimum, including identical framing costs. The separate adaptive level sweep used 45,883 / 40,844 / 40,108 / 40,051 bytes at levels 9 / 15 / 19 / 22. See `reports/stage4/ablations.json` and the individual candidate records.

Muse then received the working source, these measurements, retrieved project knowledge and a training excerpt. Its first revision adds per-column raw/plane decisions, serializes the mode bitmap and exact token lengths, and retains the original whole-block alternatives. It uses 37,130 bytes. Two later dictionary/template revisions tie on size; the template required one compiler repair. All source, parent hashes, compiler diagnostics and usage receipts are retained.

Disabling only the mixed-column selector in the first revision, keeping zstd-22/decoder/framing unchanged, produces 40,051 bytes. The measured mechanism contribution is **2,921 bytes**: 1,392 / 1,236 / 293 across the three probes. Local column estimates are a heuristic; the final global min-size choice preserves the parent's strong fallbacks. See `reports/stage4/mixed-choice-ablation.json`.

Selection minimizes the complete archive among candidates passing every frozen gate, then prefers the smaller decoder package for exact archive-size ties, then source hash. The selected first revision has a 391,032-byte executable versus its tied dictionary descendant's 424,960. An unmerged draft that used source-hash ties is retained in `premerge-selection-01/`; it is superseded and was never evaluated on sealed data. Only the root `repro/candidate-freeze.json` is active.

## Frozen result

The selected plugin is `s4-98e12bcd61e8117b0`, source SHA-256 `98e12bcd61e8117b0c9f3cc6b4edfbc3ebcb13f11d332c39290903ed97cc8cf7`. Its 37,130-byte collection is **19.72% smaller** than the strongest declared baseline (xz-9e, 46,253 bytes). The structured reference uses 40,434 bytes; that comparison includes backend/framing differences, while the selector ablation holds them fixed. Decode speed/RSS gates pass. The 391,032-byte package makes deployment saving **−381,909 bytes** on this small corpus.

`repro/candidate-freeze.json` and its adjacent SHA-256 bind the exact source, serialized model, parameters embedded in the source, release build, executable checksum, evaluator id, validation inputs, thresholds and public sealed commitment. `artifacts/stage4-selected/` holds independent source/model/build/policy/validation snapshots. The model is exactly zero bytes and is still serialized and hashed. Rust release settings remain opt-level 2, one codegen unit, overflow checks, aborting panics and symbol stripping; native/compiler dependencies are bound by the Stage 2 freeze.

The freeze creator refuses replacement. Verification checks snapshot files, source/model bindings and the current trusted evaluator; `--build` also checks the offline executable. The publication workflow must pin the reviewed candidate-freeze id outside candidate control. Adjacent hashes are accidental-change checks, not signatures against a host user who can replace both files.

An independent repeat matches all eight archive signatures and passes resource gates. A fresh build in a separate empty cache reproduces the executable hash and decodes all three files using the frozen model without retraining. The bounded counterexample reducer is tested on a deliberately broken fixture and reduces it to one byte; that is explicitly a control, not a fabricated model bug. Actual compiler failures retain diagnostics instead of claiming an input counterexample.

Four Muse sessions (three evolved candidates and one repair) report **$0.4727** in token-based estimates; no incomplete-call reservation remains in this phase. Prices are the declared configuration values, not reconciled provider billing. The project's four research repair sessions are now used. Unused refinement/reserve funds remain unused.

## Reproduction and next boundary

```bash
python -m pytest -q
python -m algomini stage4
python -m algomini selected-status --build
python -m algomini.evaluator encode s4-98e12bcd61e8117b0 input.csv output.alc --model artifacts/stage4-selected/model.bin
python -m algomini.evaluator decode output.alc decoded/
```

Ordinary file encode is capped at 2 MiB. The research evaluator continues to expose only train/validation. Stage 5 needs its separately reviewed trusted sealed entry point: it must use this exact source/model/parameters/build, never train on sealed inputs, and record one final outcome. No novelty, unseen-data win, winning installation or cloud performance claim is established by Stage 4.
