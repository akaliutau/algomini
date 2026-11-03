"""Deterministic derivatives of a Muse codec; no new paid generation."""

import argparse
import json
from pathlib import Path
import re
import time
import tomllib

from algomini import memory, wiki_benchmark
from algomini.common import load_config, sha256, write_json


def run(root, cfg, parameters):
    parent = root / "reports/wikipedia-search/candidates/ppm-see/codec.rs"
    policy = tomllib.loads((root / "wiki-search.toml").read_text())
    data = (root / ".cache/datasets/enwik8-first-1000000.xml").read_bytes()
    rows = []
    for order, increment, use_see in parameters:
        if time.time() - (root / "wiki-search.toml").stat().st_mtime > policy["maximum_run_seconds"]:
            raise ValueError("Campaign time budget exhausted; retain completed results")
        tag = f"see-o{order}-i{increment}" if use_see else f"no-see-o{order}-i{increment}"
        directory = root / "reports/wikipedia-search/candidates" / tag
        directory.mkdir(parents=True, exist_ok=True)
        source = parent.read_text()
        for name, value in (("MAX_ORDER", str(order)), ("INCREMENT", str(increment)), ("USE_SEE", str(use_see).lower())):
            source, count = re.subn(rf"(pub const {name}: \w+ = )[^;]+;", rf"\g<1>{value};", source)
            if count != 1:
                raise ValueError("Expected one named parameter: " + name)
        path = directory / "codec.rs"
        if path.exists() and path.read_text() != source:
            raise ValueError("Existing parameter source changed")
        path.write_text(source)
        write_json(directory / "lineage.json", {"parent_sha256": sha256(parent), "origin": "host deterministic parameter derivative of Muse SEE source",
                   "parameters": {"maximum_order": order, "increment": increment, "use_see": use_see}, "paid_calls": 0})
        try:
            result = wiki_benchmark.measure(root, cfg, path, data)
            row = {"id": tag, "source": path.relative_to(root).as_posix(), "source_sha256": sha256(path),
                   "archive_bytes": result["candidate"]["archive_bytes"], "archive_gain": result["score"]["archive_gain"],
                   "decode_mib_per_second": result["score"]["minimum_decode_mib_per_second"],
                   "peak_rss_kib": result["score"]["peak_decode_rss_kib"], "eligible": result["eligible"],
                   "parameters": {"maximum_order": order, "increment": increment, "use_see": use_see}}
        except ValueError as error:
            row = {"id": tag, "source": path.relative_to(root).as_posix(), "source_sha256": sha256(path), "eligible": False, "failure": str(error)[-3000:]}
        write_json(directory / "outcome.json", row)
        memory.append(root, {"id": "wiki-" + tag, "tags": ["xml", "text", "ppm", "escape-estimation"],
                      "hypothesis": "Tune bounded adaptive context depth and update rate while preserving the generated mechanism",
                      "outcome": "measured" if "archive_bytes" in row else "rejected", "measurements": row,
                      "split": "validation", "evidence": str((directory / "outcome.json").relative_to(root))}, run_id="wikipedia-search")
        rows.append(row)
        print(json.dumps(row), flush=True)
    return rows


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--orders", nargs="+", type=int, default=[4, 5, 6, 7])
    parser.add_argument("--increments", nargs="+", type=int, default=[1, 2, 4])
    parser.add_argument("--no-see", action="store_true")
    args = parser.parse_args()
    if any(not 1 <= order <= 7 for order in args.orders) or any(value not in (1, 2, 4, 8) for value in args.increments):
        raise ValueError("Sweep exceeds declared safe source parameters")
    root, cfg = load_config("run.toml")
    run(root, cfg, [(order, increment, not args.no_see) for order in args.orders for increment in args.increments])
