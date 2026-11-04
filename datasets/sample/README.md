# Regression samples

These user-provided files reproduce the issue-2 workload:

| File | Bytes | SHA-256 |
| --- | ---: | --- |
| `train-1mb.json` | 1,026,294 | `a8866ee433936e02af9115d6c09ecc1394042b2e3c6ec38cbb198e027b5cec22` |
| `test-512kb.json` | 513,148 | `78ac520da1e0516f3a33d6ca13361fa0b364a3d791ac24b23e320288d3683402` |

The pinned copies were supplied through [the dataset repository](https://github.com/akaliutau/algomini-datasets/tree/e1a704f956464a77dee1bdb228cc8fe8364d11b8/json/microsoftedge). The original request is preserved in [archived issue-2 evidence](../../reports/archive/campaigns/issue-2/request.json).

The development document has 3,168 objects but 197 distinct lexical record groups. Both files contain the same group population. They are useful correctness/performance regressions, including complete records, exact numeric/string spellings, repeat detection, console framing and source-free reconstruction. They do not establish independent unseen-data performance.

Run `python -m tools.check_samples --registry tests/fixtures/release/registry.json --development datasets/sample/train-1mb.json --regression datasets/sample/test-512kb.json --workspace .cache/sample-check`. Future final evaluation should use new records. Research never automatically opens this regression path; the local helper requires the explicit `--regression` option.

## Large text files

The user-provided text files exercise multi-chunk compression without AI calls:

| File | Bytes | SHA-256 |
| --- | ---: | --- |
| `Shakespeare-1.txt` | 5,422,721 | `a023115c2d4e2ee12221bdd780fdf2ac5a864fe225948656f51f8be462c7fffb` |
| `Dumas-1.txt` | 2,704,417 | `649dc5b18efc095035fd29216bdcdfca6744ed3dccacd7bfc11859451004e327` |

Their embedded headers identify Project Gutenberg eBooks 100 (Shakespeare) and 1184 (Dumas). Files are retained byte-for-byte as supplied. These are known local regression fixtures, not fresh acceptance data. A two-file ZIP assigns Shakespeare to development and Dumas to final; research sees only the first 2 MiB of Shakespeare. Full-file round trips use three and two independent codec chunks respectively.

```bash
python -m tools.check_samples --development datasets/sample/Shakespeare-1.txt \
  --registry tests/fixtures/release/registry.json \
  --regression datasets/sample/Dumas-1.txt --family text --plugin wiki-xml \
  --workspace .cache/large-text
```
