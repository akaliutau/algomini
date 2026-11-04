# Historical experiments

The maintained application is the issue-driven cloud pipeline, the Rust release client and the local research laboratory. The former numbered-stage CLI and its one-off telemetry/Wikipedia runners are retired. Their source, reports, frozen inputs and environment records remain accessible at immutable Git revisions:

- [Completed local and cloud implementation](https://github.com/akaliutau/algomini/tree/f6ce5ad4295d192c9bdde6b6239ec97fa4d3b376).
- [Original JSON issue-2 terminal evidence](https://github.com/akaliutau/algomini/tree/ff2b01d/reports/campaigns/issue-2).
- The JSON diagnosis remains in `reports/investigations/issue-2/` as development research.

To inspect an old result, use `git show f6ce5ad:reports/stage5/report.md`. To reproduce an old experiment, use a separate checkout at that revision and its pinned environment. A current source tree is not interchangeable with a historical frozen verifier. Historical final results retain their original scope and are not rerun by the maintained pipeline.

The existing post-evaluation generation marker, budget ledgers and known-test hashes are retained as safeguards. Runtime containment, account reservations, conditional Git updates and single-use final claims remain active. The repository cleanup removes retired callers only after an import-reachability audit and migration of containment, budget, protocol and coder tests. `reports/stability/cleanup-audit.json` records the active entry points and candidate unused modules.
