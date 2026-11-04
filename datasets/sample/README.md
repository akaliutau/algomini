# Known JSON regression fixtures

These user-provided files reproduce the issue-2 workload:

| File | Bytes | SHA-256 |
| --- | ---: | --- |
| `train-1mb.json` | 1,026,294 | `a8866ee433936e02af9115d6c09ecc1394042b2e3c6ec38cbb198e027b5cec22` |
| `test-512kb.json` | 513,148 | `78ac520da1e0516f3a33d6ca13361fa0b364a3d791ac24b23e320288d3683402` |

The pinned copies were supplied through [the dataset repository](https://github.com/akaliutau/algomini-datasets/tree/e1a704f956464a77dee1bdb228cc8fe8364d11b8/json/microsoftedge). The original request is preserved in [issue-2 evidence](../../reports/campaigns/issue-2/request.json).

The development document has 3,168 objects but 197 distinct lexical record groups. Both files contain the same group population. They are useful correctness/performance regressions, including complete records, exact numeric/string spellings, repeat detection, console framing and source-free reconstruction. They do not establish independent unseen-data performance.

Run `python -m tools.check_samples --development datasets/sample/train-1mb.json --regression datasets/sample/test-512kb.json --workspace .cache/sample-check`. Future final evaluation should use new records. Research never automatically opens this regression path; the local helper requires the explicit `--regression` option.
