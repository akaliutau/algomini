"""Bounded, deterministic escape-estimator refinements of the generated codec."""

import json
import time
import tomllib

from algomini import wiki_benchmark
from algomini.common import load_config, sha256, write_json


def derivative(source, *, rescale=1024, tail=False, classes=False, singletons=False):
    source = source.replace("self.hit[feat] + self.esc[feat] >= 1024", f"self.hit[feat] + self.esc[feat] >= {rescale}")
    if tail:
        source = source.replace("core::cmp::min(mean, 15)", "if mean < 16 { mean } else { 16 + core::cmp::min(7, (mean / 16).ilog2()) }")
        source = source.replace("* 16 + m)", "* 24 + m)")
    multiplier = (24 if tail else 16) * 8 * 16 * 2
    if classes:
        source = source.replace("see_feature(order, nvis, mean, has_excl)", "see_feature(order, nvis, mean, has_excl) * 4 + match ppm.history as u8 { b' ' | b'\\n' | b'\\r' | b'\\t' => 0, b'a'..=b'z' | b'A'..=b'Z' => 1, b'0'..=b'9' => 2, _ => 3 }")
        multiplier *= 4
    if singletons:
        feature = "see_feature(order, nvis, mean, has_excl)"
        extra = "(ppm.arena[idx as usize].syms.iter().filter(|e| !excl_has(&excl, e.sym) && e.count == INCREMENT).count() * 2 >= nvis as usize) as usize"
        source = source.replace(feature, f"({feature} * 2 + {extra})")
        multiplier *= 2
    source = source.replace("const SEE_N: usize = 4096;", f"const SEE_N: usize = {multiplier};")
    return source


if __name__ == "__main__":
    root, cfg = load_config("run.toml")
    policy = tomllib.loads((root / "wiki-search.toml").read_text())
    parent = root / "reports/wikipedia-search/candidates/see-o6-i2/codec.rs"
    data = (root / ".cache/datasets/enwik8-first-1000000.xml").read_bytes()
    trials = [("tail", {"tail": True}), ("fast-escape", {"rescale": 256}),
              ("slow-escape", {"rescale": 4096}), ("classes", {"classes": True}),
              ("singletons", {"singletons": True}),
              ("tail-classes", {"tail": True, "classes": True})]
    for name, parameters in trials:
        if time.time() - (root / "wiki-search.toml").stat().st_mtime >= policy["maximum_run_seconds"]:
            raise ValueError("Search deadline reached")
        directory = root / "reports/wikipedia-search/candidates" / ("refine-" + name)
        directory.mkdir(parents=True, exist_ok=True)
        source = directory / "codec.rs"
        code = derivative(parent.read_text(), **parameters)
        if source.exists() and source.read_text() != code:
            raise ValueError("Refinement source changed")
        source.write_text(code)
        write_json(directory / "lineage.json", {"parent_sha256": sha256(parent), "parameters": parameters,
                   "origin": "host deterministic escape-feature derivative of Muse source", "paid_calls": 0})
        try:
            result = wiki_benchmark.measure(root, cfg, source, data)
            row = {"id": directory.name, "source": str(source.relative_to(root)), "source_sha256": sha256(source),
                   "archive_bytes": result["candidate"]["archive_bytes"], "archive_gain": result["score"]["archive_gain"],
                   "decode_mib_per_second": result["score"]["minimum_decode_mib_per_second"],
                   "peak_rss_kib": result["score"]["peak_decode_rss_kib"], "eligible": result["eligible"], "parameters": parameters}
        except ValueError as error:
            row = {"id": directory.name, "eligible": False, "failure": str(error)[-2000:]}
        write_json(directory / "outcome.json", row)
        print(json.dumps(row), flush=True)
