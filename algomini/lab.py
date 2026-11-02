"""Bounded exploratory analysis and trusted baseline smoke measurements.

This runner is for installed, trusted tools. Candidate-code containment belongs
to Stage 2; this module must not be used to execute generated programs.
"""

from collections import Counter
from datetime import datetime
import os
from pathlib import Path
import re
import shutil
import signal
import struct
import subprocess
import tempfile
import time

from .common import digest, sha256, worker_environment, write_json
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


def measure(argv, data, cfg):
    if len(data) > cfg["lab"]["max_input_bytes"]:
        raise ValueError("Compression input exceeds the 2 MiB cap")
    limits = cfg["limits"]
    for tool in (argv[0], "prlimit", "/usr/bin/time"):
        if shutil.which(tool) is None:
            raise ValueError(f"Required local tool is missing: {tool}")
    with tempfile.TemporaryDirectory(prefix="algomini-time-") as directory:
        rss_path = Path(directory) / "rss.txt"
        command = ["prlimit", f"--as={limits['baseline_memory_mib'] * 1024**2}",
                   f"--cpu={limits['baseline_timeout_seconds']}", "--", "/usr/bin/time",
                   "-f", "%M", "-o", str(rss_path), "--", *map(str, argv)]
        start = time.perf_counter()
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   env=worker_environment(), start_new_session=True)
        try:
            output, error = process.communicate(data, timeout=limits["baseline_timeout_seconds"])
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            raise ValueError(f"Trusted tool exceeded the wall-time limit: {argv[0]}") from None
        elapsed = time.perf_counter() - start
        if process.returncode:
            raise ValueError(f"Trusted tool failed ({process.returncode}): {argv[0]}: {error.decode(errors='replace')[:300]}")
        if len(output) > cfg["lab"]["max_input_bytes"] * 2:
            raise ValueError("Trusted tool output exceeds the configured cap")
        return output, {"wall_seconds": elapsed, "peak_rss_kib": int(rss_path.read_text().strip())}


def transpose_tokens(data):
    """Small, reversible reference; preserve raw comma-separated tokens."""
    if b"\0" in data:
        raise ValueError("Raw-token reference requires NUL-free input")
    rows, endings = [], []
    for line in data.splitlines(keepends=True):
        ending = 2 if line.endswith(b"\r\n") else int(line.endswith(b"\n"))
        rows.append((line[:-ending] if ending else line).split(b","))
        endings.append(ending)
    if not rows or any(len(row) != len(rows[0]) for row in rows):
        raise ValueError("Raw-token reference requires a rectangular line/comma layout")
    cells = [cell for column in zip(*rows) for cell in column]
    return b"COL1" + struct.pack("<II", len(rows), len(rows[0])) + bytes(endings) + b"\0".join(cells)


def restore_tokens(encoded):
    if encoded[:4] != b"COL1":
        raise ValueError("Invalid raw-token reference header")
    rows, columns = struct.unpack("<II", encoded[4:12])
    endings, cells = encoded[12:12 + rows], encoded[12 + rows:].split(b"\0")
    if len(cells) != rows * columns or any(v > 2 for v in endings):
        raise ValueError("Invalid raw-token reference dimensions/endings")
    return b"".join(b",".join(cells[column * rows + row] for column in range(columns)) +
                    (b"", b"\n", b"\r\n")[ending] for row, ending in enumerate(endings))


def train_dictionary(root, cfg):
    paths = development_files(root, cfg, "train")[1:1 + cfg["lab"]["dictionary_samples"]]
    if len(paths) < 5:
        raise ValueError("Dictionary smoke training requires at least five training chunks")
    directory = root / ".cache/dictionaries"
    directory.mkdir(parents=True, exist_ok=True)
    samples, total = [], 0
    for i, path in enumerate(paths):
        data = bounded_sample(path, cfg["lab"]["dictionary_sample_bytes"])
        total += len(data)
        if total > cfg["lab"]["max_input_bytes"]:
            raise ValueError("Combined dictionary training samples exceed the 2 MiB cap")
        sample = directory / f"sample-{i:03d}.csv"
        sample.write_bytes(data)
        samples.append(sample)
    dictionary = directory / "appliances.dict"
    argv = ["zstd", "-q", "--train", "-T1", "--dictID=1", f"--maxdict={cfg['lab']['dictionary_bytes']}",
            "-o", str(dictionary), *map(str, samples)]
    # zstd refuses to overwrite unless the caller removes its own derived cache.
    dictionary.unlink(missing_ok=True)
    _, resources = measure(argv, b"", cfg)
    return dictionary, {"path": dictionary.relative_to(root).as_posix(), "sha256": sha256(dictionary),
                        "bytes": dictionary.stat().st_size, "training_bytes": total,
                        "training_sample_sha256": [sha256(path) for path in samples],
                        "training_split": "train", "dict_id": 1, "resources": resources,
                        "command": [str(v).replace(str(root) + "/", "") for v in argv]}


def baselines(root, cfg, split="train"):
    path = development_files(root, cfg, split)[0]
    data = bounded_sample(path, cfg["lab"]["baseline_probe_bytes"])
    dictionary, model = train_dictionary(root, cfg)
    results = []
    for baseline in cfg["baselines"]:
        encode = [part.replace("{dictionary}", str(dictionary)).replace("{input_bytes}", str(len(data)))
                  for part in baseline["encode"]]
        decode = [part.replace("{dictionary}", str(dictionary)) for part in baseline["decode"]]
        encoded, encoding = measure(encode, data, cfg)
        restored, decoding = measure(decode, encoded, cfg)
        if restored != data:
            raise ValueError(f"Baseline failed exact reconstruction: {baseline['id']}")
        model_bytes = model["bytes"] if baseline.get("dictionary") else 0
        results.append({"id": baseline["id"], "archive_bytes": len(encoded) + model_bytes,
                        "payload_bytes": len(encoded), "model_bytes": model_bytes,
                        "encoded_sha256": digest(encoded), "roundtrip": True,
                        "encode": encoding, "decode": decoding,
                        "encode_command": baseline["encode"], "decode_command": baseline["decode"]})
    reference = transpose_tokens(data)
    if restore_tokens(reference) != data:
        raise ValueError("Raw-token reference failed exact reconstruction")
    reference_args = ["zstd", "-q", "-T1", f"--stream-size={len(reference)}", "-19", "-c"]
    compressed, encoding = measure(reference_args, reference, cfg)
    restored, decoding = measure(["zstd", "-q", "-d", "-c"], compressed, cfg)
    if restore_tokens(restored) != data:
        raise ValueError("Compressed raw-token reference failed exact reconstruction")
    best = min(results, key=lambda value: value["archive_bytes"])
    result = {"schema_version": 1, "probe_id": "baseline-smoke-v1", "split": split,
              "input": {"path": path.relative_to(root).as_posix(), "bytes": len(data), "sha256": digest(data)},
              "dictionary": model, "results": results, "best_baseline": best["id"],
              "reference": {"id": "raw-token-transpose-zstd-19", "archive_bytes": len(compressed),
                            "transform_bytes": len(reference), "roundtrip": True,
                            "gain_vs_best_baseline": 1 - len(compressed) / best["archive_bytes"],
                            "encode": encoding, "decode": decoding, "command": reference_args},
              "timing_scope": "Includes process startup; not a warmed decoder throughput measurement",
              "scope": "One development probe; this is not sealed-test evidence or a generated codec",
              "reproduce": ["python", "-m", "algomini", "baselines", "--split", split]}
    write_json(root / cfg["report_dir"] / f"baselines-{split}.json", result)
    return result
