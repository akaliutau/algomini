# JSON campaign diagnosis

This report records the diagnosis at revision `bfc3df9`, before the [stability implementation](../../stability/report.md). Its original diagnostic source remains available [at that revision](https://github.com/akaliutau/algomini/blob/bfc3df9/tools/json_campaign_diagnostics.py); the maintained helper now also bounds malformed-string lexing.

File size alone does not explain this loss. The evidence identifies weaknesses in sampling and search feedback, and a very repetitive dataset. A reversible lexical-layout reference already improves development compression by about 5%, with prospective production framing counted. This is research evidence, not a new final-test win or a qualified Rust codec.

The original [campaign report](../../campaigns/issue-2/report.md), freeze and final measurement are unchanged. This investigation opened only the declared development file, verified its SHA-256 and used the saved final numbers. It made no model calls and used no Docker.

## Actual campaign outcome

The 513,148-byte final file compressed to 8,725 complete bytes with xz and 9,172 with the selected Wikipedia seed: a 447-byte loss, or 5.12%. Correctness and all resource gates passed. Speed was secondary and did not cause rejection.

On the separate 513,147-byte development validation half:

| Trial | Complete bytes | Model bytes | Result |
| --- | ---: | ---: | --- |
| xz baseline | 8,729 | 0 | Best baseline |
| Wikipedia seed | 9,164 | 0 | Selected |
| Generated initial | 9,197 | 0 | Same block size as seed; longer codec identity |
| Generated repair | 9,197 | 0 | Same block size as seed; longer codec identity |
| Generated evolved | 10,051 | 2,175 | Smaller block, but model cost erased savings |

The evolved block was 7,792 bytes including its native frame, versus the seed's 9,113. Its 84-byte collection header and 2,175-byte model made the complete result worse. At unchanged block size, that model must be at most 852 bytes to beat xz. Possible experiments include compressing the serialized model, selecting fewer entries and learning state adaptively during decoding. None is an established win without measurement.

The initial/repair sizes are consistent with fallback to ordinary zstd. The checkpoint does not preserve their source or mode counters, so equal sizes alone cannot prove the mechanism. Future checkpoints should retain bounded, hashed candidate sources/models and generation diagnostics, including losing attempts.

One additional initial coding attempt failed. Its message includes a Muse session-registry permission error; the full diagnostic stream is absent, so that warning alone cannot establish the complete failure cause. Three other generated artifacts passed controls. This is not evidence that the model cannot find patterns. Recorded exposure is $5.5523537: $0.5523537 in token-based estimates plus a conservative $5 hold for the failed attempt with unknown usage. The hold is not a claim that the provider billed $5.

## Problems to fix

1. **The byte split destroys JSON validity.** The complete development document is valid, but both halves used for training/selection are invalid JSON. A transform requiring a complete array may be judged only on fallback. Use complete records/documents for structured selection, retaining exact lexical bytes, and keep fragment/binary cases as correctness controls. A streaming lexical transform can also handle fragments directly.
2. **The preparation probe also sees a fragment.** The first-64-KiB `json-lexemes` probe returns `supported=false`, supplying no schema evidence. Profile the complete bounded development file or explicitly extract complete records. Supply diversity/repetition information beyond the first 4-KiB coding excerpt.
3. **The development partitions have no new object bodies.** The 1,026,294-byte file contains 3,168 objects but only 197 distinct exact bodies; all 197 occur in both halves. Repeating this into larger files mostly tests long-distance copying. Use genuinely new records from a different source shard/time range, and disclose overlap. This investigation did not inspect the final file for record overlap.
4. **The coding loop omits measured feedback.** Later prompts contain the parent source and generic probes, but no baseline size, parent size, component costs or scoreboard. Feed development measurements and a specific next hypothesis into refinement. Distinguish structured mode from fallback, avoid repeated/no-op mechanisms and choose family-appropriate seeds.
5. **A development loss still consumes the final file.** `search()` selects the smallest correct/resource-eligible candidate regardless of development gain. Require a declared development gain before final consumption; otherwise end with a no-win report. Preserve model accounting and the existing baseline.

## Measured development headroom

[development-diagnostics.json](development-diagnostics.json) records input/tool hashes, entropy, validity, repetition and reversible-layout measurements. Reproduce with:

```bash
python -m tools.json_campaign_diagnostics \
  --input datasets/sample/train-1mb.json \
  --campaign reports/campaigns/issue-2 \
  --output .cache/issue-2-development-diagnostics.json
```

The reference groups raw lexemes into channels according to their preceding key. It stores channel lengths, exact lexemes, gaps and original order, then compresses the representation. It handles fragments without changing whitespace, key order, duplicate keys, numeric spellings or escapes. Its decoder needs no external schema or learned model.

| Development scope | Best complete raw baseline | Lexical + zstd, prospective complete bytes | Gain |
| --- | ---: | ---: | ---: |
| Training byte half | 8,725 | 8,328 | 4.55% |
| Validation byte half | 8,729 | 8,328 | 4.59% |
| Full development | 8,801 | 8,365 | 4.95% |

These estimates count the actual compressed representation plus 125 bytes for the current 52-character codec identity, ALC1 collection, native frame and mode byte. All layout metadata is compressed and counted. Raw and transformed decompression reconstructed exactly. Lexical + xz also measured 8,317 bytes on validation, but xz is not presently exposed to generated Rust codecs.

This does not establish the 10% target. The Python reference needs a Rust port and actual client/resource qualification; framing is explicitly prospective. Local tools were xz 5.8.4 and zstd 1.5.5. No new result was measured on the old final file.

## Entropy and theoretical limits

Independent-byte empirical entropy H0 is 4.7466 bits/byte, suggesting 608,923 ideal coding bytes for development. Yet xz already uses only 8,801 complete bytes. H0 misses repetition and dependencies; it is not a lower bound for unrestricted compression of this data. Order-four conditional byte entropy still suggests 56,525 bytes because it misses longer relationships. High orders fitted on one finite file can also overfit and omit expensive model state.

Shannon's source entropy accounts for dependencies and characterizes average coding limits for a specified source. It does not supply an exact minimum archive size for an unknown source from two files. See [Shannon, sections 6–9](https://people.math.harvard.edu/~ctm/home/text/others/shannon/entropy/entropy.pdf). Label laboratory results as empirical entropy or model-specific coding estimates, with their assumptions.

A useful panel should combine H0/conditional descriptors, prefix-trained or online predictive log loss on development validation, record/template repetition and measured reversible representations:

```text
estimated archive bytes = model bytes + predicted coding bits / 8 + framing
```

Use estimates to prioritize experiments. Actual independently decoded archives establish gains. The lexical experiment supplies a constructive research result, not a proof of the unknown optimum.

## Implementation sequence

**First, repair the research loop:** record-aware development sampling, complete bounded profiling, fallback counters, per-trial component accounting and measurement feedback. Preserve losing sources/diagnostics and investigate the lost Muse attempt. Stop before final consumption when development loses. Keep spending and test-consumption guards.

**Then, build the JSON mechanism:** port the measured lexical reference to safe Rust; compare record/template and smaller compressed-model variants under the real client; select on representative development validation; freeze and evaluate once on new final records. The old final file can no longer provide fresh independent evidence after these observations. Increasing file size or buying a more expensive model is not the first corrective step.

Nine focused diagnostic tests pass: exact reconstruction, fragments, escaping, duplicate keys, numeric spellings, entropy semantics, corrupt dimensions and development identity guarding. This branch adds the diagnostic and findings; it does not alter the paid pipeline or the old verdict.
