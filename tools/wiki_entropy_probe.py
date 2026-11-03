"""Inspect BWT/MTF context entropy; estimates are not measured archive sizes."""

from collections import Counter, defaultdict
import math
from pathlib import Path

from tools.wiki_probes import bwt, mtf
from algomini.common import digest, sha256, write_json


def symbols(data):
    output, zeros = [], 0
    for rank in data:
        if rank == 0:
            zeros += 1
            continue
        while zeros:
            zeros -= 1
            output.append(zeros & 1)
            zeros >>= 1
        output.append(rank + 1)
    while zeros:
        zeros -= 1
        output.append(zeros & 1)
        zeros >>= 1
    return output


def analyze(data):
    ranks = mtf(bwt(data)[4:])
    coded = symbols(ranks)
    rows = []
    for mode in ("global", "previous-run", "previous-rank-8", "previous-rank-32"):
        counts, previous = defaultdict(Counter), 0
        for symbol in coded:
            context = {"global": 0, "previous-run": int(previous > 1),
                       "previous-rank-8": min(previous, 7), "previous-rank-32": min(previous, 31)}[mode]
            counts[context][symbol] += 1
            previous = symbol
        bits = sum(-n * math.log2(n / sum(row.values())) for row in counts.values() for n in row.values())
        rows.append({"model": mode, "empirical_entropy_bytes": bits / 8, "contexts": len(counts)})
    return {"input_bytes": len(data), "input_sha256": digest(data), "rle_symbols": len(coded), "estimates": rows}


if __name__ == "__main__":
    root = Path.cwd()
    data = (root / ".cache/datasets/enwik8-first-1000000.xml").read_bytes()
    results = [analyze(data[:size]) for size in (65536, 1000000)]
    result = {"schema_version": 1, "sources": {name: sha256(root / "tools" / name) for name in ("wiki_probes.py", "wiki_entropy_probe.py")},
              "results": results, "scope": "Empirical lower-bound guidance; no arithmetic stream, framing, model overhead or speed measured"}
    write_json(root / "reports/wikipedia-search/context-entropy.json", result)
    for row in results:
        print(row, flush=True)
