"""Frozen baseline commands and reversible raw-token reference."""

import fcntl
import json
from pathlib import Path
import shutil
import struct
import sys
import tempfile

from .common import digest, sha256, write_json
from .dataset import bounded_sample, development_files
from .execution import run_isolated


def measure(argv, data, cfg, *, ro=(), rw=()):
    if len(data) > cfg["lab"]["max_input_bytes"]:
        raise ValueError("Compression input exceeds the 2 MiB cap")
    limits = cfg["limits"]
    executable = shutil.which(argv[0])
    if executable is None:
        raise ValueError(f"Required local tool is missing: {argv[0]}")
    with tempfile.TemporaryDirectory(prefix="algomini-time-") as directory:
        # Conda native binaries also need their matching $ORIGIN/../lib.
        reads = [sys.prefix] if Path(executable).resolve().is_relative_to(Path(sys.prefix).resolve()) else []
        result = run_isolated([executable, *argv[1:]], cwd=directory, ro=[executable, *reads, *ro], rw=rw,
                              data=data, timeout=limits["baseline_timeout_seconds"],
                              memory_mib=limits["baseline_memory_mib"],
                              output_bytes=cfg["lab"]["max_input_bytes"] * 2, mode="baseline")
    if result["timed_out"]:
        raise ValueError(f"Trusted tool exceeded the wall-time limit: {argv[0]}")
    if result["returncode"]:
        raise ValueError(f"Trusted tool failed ({result['returncode']}): {argv[0]}: {result['stderr'].decode(errors='replace')[:300]}")
    return result["stdout"], {key: result[key] for key in ("wall_seconds", "execution_seconds", "setup_seconds",
                            "peak_rss_kib", "user_seconds", "system_seconds", "isolation")}


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
    if len(encoded) < 12 or encoded[:4] != b"COL1":
        raise ValueError("Invalid raw-token reference header")
    rows, columns = struct.unpack("<II", encoded[4:12])
    endings, cells = encoded[12:12 + rows], encoded[12 + rows:].split(b"\0")
    if rows == 0 or columns == 0 or len(endings) != rows or len(cells) != rows * columns or any(v > 2 for v in endings):
        raise ValueError("Invalid raw-token reference dimensions/endings")
    return b"".join(b",".join(cells[column * rows + row] for column in range(columns)) +
                    (b"", b"\n", b"\r\n")[ending] for row, ending in enumerate(endings))


def train_dictionary(root, cfg):
    paths = development_files(root, cfg, "train")[1:1 + cfg["lab"]["dictionary_samples"]]
    if len(paths) < 5:
        raise ValueError("Dictionary smoke training requires at least five training chunks")
    values = [bounded_sample(path, cfg["lab"]["dictionary_sample_bytes"]) for path in paths]
    total = sum(map(len, values))
    if total > cfg["lab"]["max_input_bytes"]:
        raise ValueError("Combined dictionary training samples exceed the 2 MiB cap")
    definition = {"samples": [digest(value) for value in values], "lab": cfg["lab"],
                  "zstd_sha256": sha256(shutil.which("zstd"))}
    directory = root / ".cache/dictionaries" / digest(json.dumps(definition, sort_keys=True).encode())
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _train_dictionary(root, cfg, directory, values, total)


def _train_dictionary(root, cfg, directory, values, total):
    record_path = directory / "training.json"
    dictionary = directory / "appliances.dict"
    if record_path.exists():
        record = json.loads(record_path.read_text())
        if not dictionary.is_file() or sha256(dictionary) != record["sha256"]:
            raise ValueError("Cached dictionary failed checksum verification")
        return dictionary, record
    samples = []
    for i, data in enumerate(values):
        sample = directory / f"sample-{i:03d}.csv"
        sample.write_bytes(data)
        samples.append(sample)
    argv = ["zstd", "-q", "--train", "-T1", "--dictID=1", f"--maxdict={cfg['lab']['dictionary_bytes']}",
            "-o", str(dictionary), *map(str, samples)]
    # zstd refuses to overwrite unless the caller removes its own derived cache.
    dictionary.unlink(missing_ok=True)
    _, resources = measure(argv, b"", cfg, ro=samples, rw=[directory])
    record = {"path": dictionary.relative_to(root).as_posix(), "sha256": sha256(dictionary),
                        "bytes": dictionary.stat().st_size, "training_bytes": total,
                        "training_sample_sha256": [sha256(path) for path in samples],
                        "training_split": "train", "dict_id": 1, "resources": resources,
                        "command": [str(v).replace(str(root) + "/", "") for v in argv]}
    write_json(record_path, record)
    return dictionary, record


def baselines(root, cfg, split="train"):
    path = development_files(root, cfg, split)[0]
    data = bounded_sample(path, cfg["lab"]["baseline_probe_bytes"])
    dictionary, model = train_dictionary(root, cfg)
    results = []
    for baseline in cfg["baselines"]:
        encode = [part.replace("{dictionary}", str(dictionary)).replace("{input_bytes}", str(len(data)))
                  for part in baseline["encode"]]
        decode = [part.replace("{dictionary}", str(dictionary)) for part in baseline["decode"]]
        encoded, encoding = measure(encode, data, cfg, ro=[dictionary])
        restored, decoding = measure(decode, encoded, cfg, ro=[dictionary] if baseline.get("dictionary") else [])
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
