"""Counted byte-pair substitution prototypes; no embedded vocabulary."""

from pathlib import Path
import numpy as np

from tools.wiki_ppm_probe import analyze
from algomini import baselines
from algomini.common import digest, load_config, sha256, write_json


def transform(data, maximum):
    used = set(data)
    free = [byte for byte in range(256) if byte not in used]
    rules = []
    for symbol in free[:maximum]:
        if len(data) < 2:
            break
        values = np.frombuffer(data, dtype=np.uint8).astype(np.uint16)
        counts = np.bincount((values[:-1] << 8) | values[1:], minlength=65536)
        pair = int(counts.argmax())
        if counts[pair] < 16:
            break
        left, right = pair >> 8, pair & 255
        result = data.replace(bytes((left, right)), bytes((symbol,)))
        if len(data) - len(result) <= 3:
            break
        data = result
        rules.append((symbol, left, right))
    return data, rules


def restore(data, rules):
    for symbol, left, right in reversed(rules):
        data = data.replace(bytes((symbol,)), bytes((left, right)))
    return data


if __name__ == "__main__":
    for tiny in (b"", b"a", b"a" * 99, bytes(range(256)), b'<x>repeat words &amp; bytes</x>\r\n' * 20):
        encoded, rules = transform(tiny, 64)
        assert restore(encoded, rules) == tiny
    root, cfg = load_config("run.toml")
    raw = (root / ".cache/datasets/enwik8-first-1000000.xml").read_bytes()
    rows = []
    for maximum in (16, 32, 64):
        encoded, rules = transform(raw, maximum)
        assert restore(encoded, rules) == raw
        zstd, _ = baselines.measure(["zstd", "-q", "-T1", f"--stream-size={len(encoded)}", "--ultra", "-22", "-c"], encoded, cfg)
        estimate = analyze(encoded, 5)
        row = {"maximum_merges": maximum, "actual_merges": len(rules), "representation_bytes": len(encoded),
               "table_bytes": len(rules) * 3 + 5, "zstd_bytes_with_table": len(zstd) + len(rules) * 3 + 5,
               "ppm_ideal_bytes_with_table": estimate["ideal_arithmetic_bytes"] + len(rules) * 3 + 5}
        rows.append(row)
        print(row, flush=True)
    write_json(root / "reports/wikipedia-search/byte-pair-probes.json", {"source_sha256": sha256(__file__),
               "input_sha256": digest(raw), "results": rows,
               "scope": "Exact reversible transform; zstd measured; PPM remains a predictive log-loss estimate"})
