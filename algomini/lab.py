"""Editable development-data profiling; scoring lives in baselines/evaluator."""

from collections import Counter
from datetime import datetime
from pathlib import Path
import re
import tempfile

from .common import digest, write_json
from .dataset import bounded_sample, development_files


def profile(root, cfg, split="train"):
    import duckdb
    import numpy as np
    from scipy.stats import entropy
    from sklearn.feature_selection import mutual_info_regression

    path = development_files(root, cfg, split)[0]
    data = bounded_sample(path, cfg["lab"]["profile_bytes"])
    with tempfile.TemporaryDirectory(prefix="algomini-profile-") as directory, duckdb.connect(config={"threads": 1}) as connection:
        sample_path = Path(directory) / "sample.csv"
        sample_path.write_bytes(data)
        relation = connection.read_csv(str(sample_path), header=True, all_varchar=True)
        names, rows = relation.columns, relation.fetchall()
    if not rows:
        raise ValueError("CSV sample has no data rows")
    raw = np.frombuffer(data, dtype=np.uint8)
    histogram = np.bincount(raw, minlength=256)
    fields, numeric = [], {}
    strings = list(zip(*rows))
    for name, values in zip(names, strings):
        tokens = [v for v in values if v is not None]
        counts = Counter(tokens)
        field = {"name": name, "distinct_lexemes": len(counts),
                 "missing": len(values) - len(tokens),
                 "token_length_min": min(map(len, tokens), default=0),
                 "token_length_max": max(map(len, tokens), default=0),
                 "most_common": counts.most_common(3)}
        try:
            array = np.asarray(values, dtype=np.float64)
            if not np.isfinite(array).all():
                raise ValueError("Non-finite values")
            numeric[name] = array
            field.update({"min": float(array.min()), "max": float(array.max()),
                          "integer_lexeme_fraction": sum(bool(re.fullmatch(r"[+-]?\d+", v)) for v in tokens) / len(values)})
            if len(array) > 1:
                deltas = np.diff(array)
                field["zero_delta_fraction"] = float(np.mean(deltas == 0))
                field["mean_absolute_delta"] = float(np.mean(np.abs(deltas)))
        except (TypeError, ValueError):
            pass
        fields.append(field)
    duplicates = [[names[i], names[j]] for i in range(len(names)) for j in range(i + 1, len(names))
                  if strings[i] == strings[j]]
    varying = [name for name, array in numeric.items() if np.std(array) > 0]
    pairs = []
    if len(varying) > 1 and len(rows) > 3:
        matrix = np.column_stack([numeric[name] for name in varying])
        correlations = np.corrcoef(matrix, rowvar=False)
        ranked = sorted(((abs(float(correlations[i, j])), i, j)
                         for i in range(len(varying)) for j in range(i + 1, len(varying))), reverse=True)[:8]
        selection = np.linspace(0, len(rows) - 1, min(len(rows), 2048), dtype=int)
        for _, i, j in ranked:
            information = mutual_info_regression(matrix[selection, i:i + 1], matrix[selection, j],
                                                 random_state=cfg["seed"], n_neighbors=min(3, len(selection) - 1))[0]
            pairs.append({"fields": [varying[i], varying[j]], "pearson_r": float(correlations[i, j]),
                          "estimated_mutual_information_nats": float(information)})
    temporal = {}
    try:
        stamps = [datetime.fromisoformat(row[0]) for row in rows]
        deltas = Counter((b - a).total_seconds() for a, b in zip(stamps, stamps[1:]))
        temporal = {"field": names[0], "step_seconds_counts": deltas.most_common(5)}
    except (TypeError, ValueError):
        pass
    hypotheses = [f"Test one stored copy plus exact reconstruction of {a} and {b}; equality is observed only in this sample."
                  for a, b in duplicates]
    if temporal:
        hypotheses.append("Test a start timestamp plus step/exception representation, preserving original timestamp spelling.")
    hypotheses.append("Test raw-token column transposition and small residual predictors; count metadata and exceptions.")
    result = {"schema_version": 1, "probe_id": "structured-profile-v1", "split": split,
              "input": {"path": path.relative_to(root).as_posix(), "sha256": digest(data),
                        "bytes": len(data), "rows": len(rows), "columns": len(names)},
              "byte_entropy_bits": float(entropy(histogram, base=2)),
              "line_endings": {"lf": data.count(b"\n"), "crlf": data.count(b"\r\n")},
              "fields": fields, "identical_lexeme_columns": duplicates,
              "strongest_numeric_pairs": pairs, "timestamp": temporal, "hypotheses": hypotheses,
              "scope": "Exploratory sample; entropy/MI are estimates, not measured compression gains",
              "reproduce": ["python", "-m", "algomini", "profile", "--split", split]}
    write_json(root / cfg["report_dir"] / f"profile-{split}.json", result)
    return result


# Keep the Stage 0/1 helper API while research profiling remains editable.
from .baselines import baselines, measure, restore_tokens, train_dictionary, transpose_tokens
