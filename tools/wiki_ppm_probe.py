"""Partial-match predictive log loss with symbol exclusion; not an archive benchmark."""

import math
from pathlib import Path

from algomini.common import digest, sha256, write_json


def analyze(data, maximum, *, full_update=False, increment=2):
    models = [{} for _ in range(maximum + 1)]
    zero = {byte: 1 for byte in range(256)}
    history, bits, visits = 0, 0., 0
    for position, value in enumerate(data):
        visited, excluded = [], set()
        zero_used = False
        for order in range(min(position, maximum), 0, -1):
            context = history & ((1 << (8 * order)) - 1)
            counts = models[order].setdefault(context, {})
            visited.append(counts)
            visits += 1
            available = {key: n for key, n in counts.items() if key not in excluded}
            if not available:
                continue
            total = sum(available.values())
            escape = len(available)
            if value in available:
                bits -= math.log2(available[value] / (total + escape))
                break
            bits -= math.log2(escape / (total + escape))
            excluded.update(counts)
        else:
            bits -= math.log2(zero[value] / sum(n for key, n in zero.items() if key not in excluded))
            zero_used = True
        if full_update:
            visited = [models[order].setdefault(history & ((1 << (8 * order)) - 1), {}) for order in range(1, min(position, maximum) + 1)]
        if zero_used or full_update:
            zero[value] += increment
            if sum(zero.values()) >= 16384:
                zero = {key: max(1, n // 2) for key, n in zero.items()}
        for counts in visited:
            counts[value] = counts.get(value, 0) + increment
            if sum(counts.values()) + len(counts) >= 16384:
                for key in counts:
                    counts[key] = max(1, counts[key] // 2)
        history = ((history << 8) | value) & ((1 << (8 * maximum)) - 1)
    return {"maximum_order": maximum, "ideal_arithmetic_bytes": bits / 8,
            "mean_context_visits": visits / len(data), "contexts": sum(map(len, models)),
            "symbol_entries": sum(sum(map(len, order.values())) for order in models)}


if __name__ == "__main__":
    root = Path.cwd()
    data = (root / ".cache/datasets/enwik8-first-1000000.xml").read_bytes()
    rows = []
    for maximum in (3, 4, 5):
        result = analyze(data, maximum)
        rows.append(result)
        print(result, flush=True)
    write_json(root / "reports/wikipedia-search/ppm-exclusion-logloss.json", {"source_sha256": sha256(__file__),
        "input_bytes": len(data), "input_sha256": digest(data), "results": rows,
        "scope": "Predictive model log loss; no encoded stream, framing or native speed measured"})
