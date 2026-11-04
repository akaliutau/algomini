# Research stability and repository cleanup

This implementation fixes the JSON campaign's sampling, measurement feedback and model-cost blind spots, and adds a reviewed reversible JSON lexical codec. It keeps the working issue/PR/release workflow, account ceiling, original post-evaluation generation guard, conditional remote state and once-only final claims. Development losses now stop before final consumption.

The work is local on `stability/research-core`. No Docker, paid model inference, GitHub campaign or release publication ran during this implementation. Cloud execution starts after the user merges and pushes.

## Measured Rust result

The actual Rust client and all configured complete baselines were measured on the provided known fixtures. Models, native frames and collection metadata are counted. The JSON codec uses no learned model and selects the lexical representation on these inputs.

| Scope | Input bytes | Candidate archive | Best baseline | Gain |
| --- | ---: | ---: | ---: | ---: |
| Record-group validation | 513,933 | 5,045 | 5,241 | 3.74% |
| Full development | 1,026,294 | 8,269 | 8,801 | 6.04% |
| Known regression | 513,148 | 8,217 | 8,725 | 5.82% |

The 3,168 development objects contain 197 lexical groups. The new partition assigns 98 groups to training and 99 to validation, with zero shared groups. JSON record lexemes remain exact; only the surrounding development-view array separators are assembled. The complete original files are measured unchanged.

The regression file contains the same 197 groups as development and was already examined in issue-2. This demonstrates reconstruction and performance regression coverage, not fresh unseen-data performance. The old issue-2 verdict is unchanged. None of these figures establishes the 10% target or a theoretical compression minimum.

`tools/check_samples.py` reproduces the actual client checks. [samples.json](samples.json) records the final source/tool identities and measurements. Public console compression matches the counted worker archive, and decoding reconstructs after the supplied original copy is deleted.

## Behavioral changes

- Complete bounded profiling; JSON/JSONL/CSV record-aware development partitions with duplicate groups kept together; explicit single-document and opaque-byte fallbacks.
- A generic safe Rust lexical codec and `general`/`json-lexical` aliases, with ordinary zstd/raw paths for unsupported or unhelpful content.
- Baseline gap, complete trial scoreboard and payload/model costs in refinement prompts. Repairs receive the actual failing source artifact; infrastructure failures preserve their usage hold.
- Bounded losing-source/model and redacted CLI evidence in artifacts; durable development observations reused by later campaigns, with final results excluded from retrieval.
- A strict development gain gate, framing-floor skip for impossible tiny-input wins, claimed final execution receipts and final record-overlap disclosure.
- Release verification of consumed state, merged PR, complete checksums and matching manifests; rebuilds use their qualified image's configuration.
- Isolated pinned semantic retrieval in the cloud image, small separate dependency locks and fresh tool/skill index exports.

## Cleanup and extension

The dependency audit retired 22 unreachable Python orchestration modules, numbered-stage callers/plans and duplicate historical material. The installed Python package decreased from 45 modules / 6,729 lines to 25 modules / 3,263 lines, a 51.5% line reduction. All candidates were tracked, source-checked and backed up before removal. Relevant containment, budget, framing, secret handling and coding-CLI tests were migrated to maintained test modules. The original Rust worker remains as a compatibility fixture.

Historical source/results are accessible through [immutable Git references](../../docs/history.md). Budget ledgers, the consumed marker and known-test hashes were preserved. Development observations were retained in the maintained knowledge source. `cleanup-audit.json` records exact paths, hashes, recovery information and entry-point reachability.

The current documentation is the [README](../../README.md), [cloud guide](../../docs/cloud.md), [laboratory/skill extension guide](../../docs/laboratory.md), [local development guide](../../docs/development.md) and historical reference. Skills are committed Markdown procedures with family/tool metadata; tools are trusted registered probes; algorithms are immutable Rust source/model assets. Reviewed skill changes refresh the portable semantic index. Campaign evidence improves retrieval and candidate decisions; coding-model weights remain fixed.

## Validation record

Validation covers actual mixed-content/2-MiB native reconstruction, corruption and resource bounds, provider/spending guards, retries, no-win publication, final-access denial, deterministic rebuilds, known-fixture end-to-end research and an offline qualified release rebuild. Maximum-size binary and unterminated escaped-string inputs complete under native limits; the JSON lexer preserves malformed tails with linear scanning.

All 95 Python tests and 8 Rust tests passed. Native smoke passed 13 direct cases per codec for all three registered codecs, public-console reconstruction and containment checks. A clean Python wheel contains exactly the 25 maintained modules, and local Cargo installation exposes the current aliases. All four research skills validate; the portable hybrid index contains 57 documents / 75 sections. Cargo formatting, workflow lint and protected-ledger checks passed.

[validation.json](validation.json) records commands and counts, [smoke.json](smoke.json) binds the native sources/toolchain, and [environment.json](environment.json) captures local versions. These are local source snapshots based on the prior commit; their embedded hashes identify the tested working-tree code. GitHub CI and Cloud smoke must qualify the new image after merge/push; local success does not replace that host qualification. Begin with the documented `reuse` rehearsal before starting a new paid campaign.
