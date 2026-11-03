"""Measure adaptive escape estimation while preserving the partial-match symbol model."""

from collections import defaultdict
import math
from pathlib import Path

from algomini.common import sha256, write_json


def analyze(data):
    models = [{} for _ in range(6)]
    zero = {byte: 1 for byte in range(256)}
    estimators = [dict(), dict()]
    flag_bits = [0., 0.]
    conditional_bits = baseline_flag_bits = 0.
    history = 0
    for position, value in enumerate(data):
        visited, excluded = [], set()
        for order in range(min(position, 5), 0, -1):
            context = history & ((1 << (8 * order)) - 1)
            counts = models[order].setdefault(context, {})
            visited.append(counts)
            available = {key: n for key, n in counts.items() if key not in excluded}
            if not available:
                continue
            total, types = sum(available.values()), len(available)
            hit = value in available
            baseline_flag_bits -= math.log2((total if hit else types) / (total + types))
            mean = max(1, total // types)
            features = [(order, min(types - 1, 15), min(mean, 15), bool(excluded)),
                        (order, min(types.bit_length() - 1, 7), min(mean.bit_length() - 1, 7), bool(excluded))]
            for variant, feature in enumerate(features):
                stats = estimators[variant].setdefault(feature, [min(mean, 32), 1])
                flag_bits[variant] -= math.log2(stats[0 if hit else 1] / sum(stats))
                stats[0 if hit else 1] += 1
                if sum(stats) >= 1024:
                    stats[:] = [max(1, n // 2) for n in stats]
            if hit:
                conditional_bits -= math.log2(available[value] / total)
                break
            excluded.update(counts)
        else:
            conditional_bits -= math.log2(zero[value] / sum(n for key, n in zero.items() if key not in excluded))
            zero[value] += 2
            if sum(zero.values()) >= 16384:
                zero = {key: max(1, n // 2) for key, n in zero.items()}
        for counts in visited:
            counts[value] = counts.get(value, 0) + 2
            if sum(counts.values()) + len(counts) >= 16384:
                for key in counts:
                    counts[key] = max(1, counts[key] // 2)
        history = ((history << 8) | value) & ((1 << 40) - 1)
    return {"baseline_ideal_bytes": (conditional_bits + baseline_flag_bits) / 8,
            "escape_estimators": [{"variant": index, "ideal_bytes": (conditional_bits + loss) / 8,
                                    "learned_feature_groups": len(estimators[index])} for index, loss in enumerate(flag_bits)]}


if __name__ == "__main__":
    root = Path.cwd()
    result = analyze((root / ".cache/datasets/enwik8-first-1000000.xml").read_bytes())
    write_json(root / "reports/wikipedia-search/escape-estimation.json", {"source_sha256": sha256(__file__),
               **result, "scope": "Predictive log loss; no serialized stream or native resource claim"})
    print(result)
