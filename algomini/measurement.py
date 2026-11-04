"""Counted archives, resource summaries and bounded trusted baseline execution."""

import shutil
import statistics
import struct
import sys
import tempfile
import zlib
from pathlib import Path

from . import archive
from .common import digest
from .execution import run_isolated


def resources(measurements, input_bytes):
    elapsed = max(1e-9, statistics.median(value["execution_seconds"] for value in measurements))
    return {"repetitions": measurements, "median_execution_seconds": elapsed,
            "decode_mib_per_second": input_bytes / 1024**2 / elapsed,
            "peak_rss_kib": max(value["peak_rss_kib"] for value in measurements)}

def counted(method, blocks, model, rows):
    packed = archive.pack(method, blocks, model)
    if archive.unpack(packed) != (method, model, blocks):
        raise ValueError("Collection framing failed roundtrip")
    payload = sum(len(data) for _, data in blocks)
    return {"id": method, "archive_bytes": len(packed), "archive_sha256": digest(packed),
            "block_bytes": payload, "model_bytes": len(model),
            "collection_bytes": len(packed) - payload - len(model), "files": rows, "roundtrip": True}, packed

def score(cfg, baseline, candidate):
    baseline_bytes = baseline["best_bytes"]
    gain = 1 - candidate["archive_bytes"] / baseline_bytes
    speed = min(row["decode"]["decode_mib_per_second"] for row in candidate["files"])
    rss = max(row["decode"]["peak_rss_kib"] for row in candidate["files"])
    gates = {"exact_reconstruction": candidate["roundtrip"] and candidate["correctness"]["passed"],
             "archive_gain": gain >= cfg["objective"]["minimum_archive_gain"],
             "decode_speed": speed >= cfg["limits"]["candidate_min_decode_mib_per_second"],
             "decode_rss": rss <= cfg["limits"]["candidate_memory_mib"] * 1024}
    return {"archive_gain": gain, "minimum_decode_mib_per_second": speed, "peak_decode_rss_kib": rss,
            "deployment_saving_bytes": baseline_bytes - candidate["archive_bytes"] - candidate["decoder_package_bytes"],
            "deployment_assumption": "Common runtime/libzstd already available; one complete candidate executable is incremental",
            "gates": gates, "meets_development_targets": all(gates[key] for key in ("exact_reconstruction", "archive_gain", "decode_rss"))}

def frame_metadata(frame, *, model=b"", source_sha256=None, restored=None):
    if len(frame) < 40 or frame[:4] not in (b"ALG1", b"ALG2") or frame[5:8] != b"\0\0\0":
        raise ValueError("Invalid archive header")
    flag = frame[4]
    original, model_len, payload_len, checksum = struct.unpack_from("<IIII", frame, 8)
    if (flag not in (0, 1, 2) or (frame[:4] == b"ALG1" and flag == 2) or original > 2 * 1024**2
            or model_len > 65536 or payload_len > 4 * 1024**2 or len(frame) != 40 + model_len + payload_len):
        raise ValueError("Invalid archive dimensions/flags")
    if flag == 1 and (model_len or frame[24:40] != bytes(16) or payload_len != original):
        raise ValueError("Invalid raw archive")
    if flag == 2 and (model_len or not model or len(model) > 65536):
        raise ValueError("Missing or invalid external model")
    if flag != 1 and source_sha256 is not None and frame[24:40].hex() != source_sha256[:32]:
        raise ValueError("Archive codec id mismatch")
    if restored is not None and (len(restored) != original or zlib.crc32(restored) != checksum):
        raise ValueError("Decoded length/checksum mismatch")
    return {"original_bytes": original, "model_bytes": model_len, "payload_bytes": payload_len, "flag": flag}

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
