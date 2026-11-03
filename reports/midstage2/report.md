# M2: isolated Wikipedia integration

Exact input: **1,000,000 bytes**, SHA-256 `369b688978f649681136198fb96db14c1616756260c55fb4b65e9bc049552cad`.
The fixture is the unmodified prefix of enwik8, the published March 2006 Wikipedia XML benchmark. Full-member published MD5/SHA-1 and archive/prefix SHA-256 are pinned.
Two independent campaign workspaces prepared XML and JSON research briefs. Their inputs/caches remained separate; retrieval stayed within four documents/6,000 excerpt characters.
A fresh compiled installation encoded the complete prefix, deleted its supplied original and reconstructed the exact hash in a separate directory. No compiler, paid generation, candidate training or Docker was used.

| Method | Complete collection bytes |
|---|---:|
| gzip-9 | 355402 |
| xz-9e | 290783 |
| zstd-19 | 300120 |
| zstd-22 | 300113 |
| Existing frozen candidate | 300163 |

Best dictionary-free baseline: **xz-9e**. Existing candidate archive gain: **-3.23%**.
This tests integration and exact fallback behavior. It is not a new text-optimized algorithm, a new sealed evaluation, or a claim of improved compression. Dictionary baselines are excluded because this fixture introduces no training corpus.
Raw/source/encoded fixture data stay in ignored caches. Campaign reports, native baseline resources, provenance and hashes are in `result.json` and `campaigns/`.
Replay: `python -m algomini text-e2e`. A new `--output` directory performs another integration run while preserving this record.

Final acceptance: **91 Python tests pass**; the three unchanged Rust tests passed in M1. The core wheel installs and runs in an empty environment with zero runtime dependencies and without scientific/model packages. Original frozen sources, reports, environment locks and spending remain unchanged. See `checks.json`, `package-check.json`, `replay-check.json` and `repro/midstage2-environment.json`.
