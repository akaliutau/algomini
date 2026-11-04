"""Trusted Rust wrapper builds and independent codec correctness checks."""

import json
import fcntl
from pathlib import Path
import random
import shutil
import struct
import tempfile
import zlib

from algomini.common import digest, sha256, worker_environment, write_json
from algomini.environment import command_output
from algomini.execution import run_isolated


def core_hashes(root):
    directory = root / "rust/codec-worker"
    return {path.relative_to(directory).as_posix(): sha256(path)
            for path in sorted(directory.rglob("*")) if path.is_file() and "target" not in path.parts}


def build(root, cfg, source):
    source = Path(source)
    if source.suffix != ".rs" or not source.is_file() or source.is_symlink():
        raise ValueError("Candidate must be a regular, nonempty Rust file below 64 KiB")
    with source.open("rb") as stream:
        code = stream.read(65537)
    if not code or len(code) > 65536:
        raise ValueError("Candidate must be a regular, nonempty Rust file below 64 KiB")
    locks = root / ".cache/build-locks"
    locks.mkdir(parents=True, exist_ok=True)
    with (locks / (digest(code) + ".lock")).open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _build_locked(root, cfg, code)


def _build_locked(root, cfg, code):
    rustc = Path(command_output(["rustup", "which", "rustc"]))
    cargo = Path(command_output(["rustup", "which", "cargo"]))
    definition = {"source_sha256": digest(code), "core": core_hashes(root),
                  "rustc": command_output([str(rustc), "--version"]),
                  "builder_sha256": sha256(__file__),
                  "execution_sha256": sha256(root / "algomini/execution.py"),
                  "isolation_sha256": sha256(root / "algomini/_isolate.py")}
    fingerprint = digest(json.dumps(definition, sort_keys=True).encode())
    directory = root / ".cache/builds" / fingerprint
    record_path = directory / "build.json"
    binary = directory / "target/release/algomini-codec-worker"
    if record_path.exists():
        record = json.loads(record_path.read_text())
        if record["ok"] and binary.is_file() and sha256(binary) == record["binary_sha256"]:
            return binary, record
        if not record["ok"]:
            raise ValueError("Cached compilation failed: " + record["diagnostics"])
        raise ValueError("Cached binary failed checksum verification")
    shutil.copytree(root / "rust/codec-worker", directory, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("target"))
    (directory / "src/codec.rs").write_bytes(code)
    identifier = bytes.fromhex(definition["source_sha256"][:32])
    (directory / "src/identity.rs").write_text(f"pub const CODEC_ID: [u8; 16] = {list(identifier)};\n")
    (directory / ".cargo").mkdir(exist_ok=True)
    (directory / ".tmp").mkdir(exist_ok=True)
    env = worker_environment()
    env.update({"RUSTC": str(rustc), "CARGO_HOME": str(directory / ".cargo"),
                "CARGO_TARGET_DIR": str(directory / "target"), "CARGO_BUILD_JOBS": "1", "TMPDIR": str(directory / ".tmp")})
    result = run_isolated([cargo, "build", "--release", "--offline", "--locked", "--manifest-path", directory / "Cargo.toml"],
                          cwd=directory, ro=[rustc.parent.parent], rw=[directory], env=env,
                          timeout=120, memory_mib=2048, output_bytes=32 * 1024**2, mode="build")
    record = {**definition, "fingerprint": fingerprint,
              "ok": result["returncode"] == 0 and binary.is_file(),
              "diagnostics": (result["stdout"] + result["stderr"]).decode(errors="replace")[-12000:],
              "wall_seconds": result["wall_seconds"], "isolation": result["isolation"]}
    if record["ok"]:
        record["binary_sha256"] = sha256(binary)
        record["binary_bytes"] = binary.stat().st_size
    write_json(record_path, record)
    if not record["ok"]:
        raise ValueError("Candidate compilation failed: " + record["diagnostics"])
    return binary, record


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


def invoke(root, cfg, binary, operation, data, *, original_len=0, model=b"", source_sha256=None):
    if len(data) > cfg["limits"]["candidate_max_output_bytes"] + 65536 or len(model) > 65536:
        raise ValueError("Worker input/model exceeds bounds")
    request = b"ALQ1" + bytes([operation]) + struct.pack("<III", original_len, len(model), len(data)) + model + data
    if operation == 2:
        frame_metadata(data, model=model, source_sha256=source_sha256)
    timeout = cfg["limits"]["candidate_encode_timeout_seconds"] if operation in (1, 3, 4, 6) else cfg["limits"]["candidate_decode_timeout_seconds"]
    with tempfile.TemporaryDirectory(prefix="algomini-worker-") as directory:
        result = run_isolated([binary], cwd=directory, ro=[binary], data=request,
                              timeout=timeout, memory_mib=cfg["limits"]["candidate_memory_mib"],
                              output_bytes=cfg["limits"]["candidate_max_output_bytes"] + 65536 + 64)
    output = result["stdout"]
    if result["timed_out"]:
        raise ValueError("Candidate exceeded its wall-time limit")
    if len(output) < 9 or output[:4] != b"ALR1" or struct.unpack("<I", output[5:9])[0] != len(output) - 9:
        raise ValueError("Invalid worker response: " + result["stderr"].decode(errors="replace")[-1000:])
    if result["returncode"] != 0 or output[4] != 0:
        raise ValueError("Candidate rejected request: " + output[9:].decode(errors="replace")[:1000])
    if operation in (1, 6):
        frame_metadata(output[9:], model=model, source_sha256=source_sha256, restored=data)
    elif operation == 2:
        frame_metadata(data, model=model, source_sha256=source_sha256, restored=output[9:])
    elif operation == 3 and len(output) - 9 > 65536:
        raise ValueError("Trained model exceeds 64 KiB")
    elif operation == 5 and len(output) - 9 != original_len:
        raise ValueError("Algorithm roundtrip failed: decoded length mismatch")
    return output[9:], {key: result[key] for key in ("wall_seconds", "execution_seconds", "setup_seconds",
                       "peak_rss_kib", "user_seconds", "system_seconds", "isolation")}


def correctness(root, cfg, binary, sample, model=b""):
    cases = [b"", b"\0", b"a" * 63, bytes(range(256)), b"a,b\r\n1.00,-0\r\n",
             b'{"x":1.0,"x":-0}\n', random.Random(cfg["seed"]).randbytes(127), sample[:2048]]
    for index, data in enumerate(cases):
        encoded, _ = invoke(root, cfg, binary, 4, data, original_len=len(data), model=model)
        restored, _ = invoke(root, cfg, binary, 5, encoded, original_len=len(data), model=model)
        if restored != data:
            raise ValueError(f"Algorithm roundtrip failed on tiny case {index}, input SHA-256 {digest(data)}")
    return {"passed": True, "tiny_cases": len(cases), "raw_escape_bypass": True}


def evaluate(root, cfg, source, sample, training_samples=()):
    if len(sample) > cfg["lab"]["max_input_bytes"]:
        raise ValueError("Evaluation sample exceeds the 2 MiB cap")
    binary, compilation = build(root, cfg, source)
    training = b"".join(struct.pack("<I", len(value)) + value for value in training_samples)
    if len(training) > cfg["lab"]["max_input_bytes"]:
        raise ValueError("Combined training data exceeds the 2 MiB cap")
    model, _ = invoke(root, cfg, binary, 3, training)
    checks = correctness(root, cfg, binary, sample, model)
    encoded, encoding = invoke(root, cfg, binary, 1, sample, original_len=len(sample), model=model)
    restored, decoding = invoke(root, cfg, binary, 2, encoded)
    if restored != sample:
        raise ValueError("Framed archive failed exact byte reconstruction")
    if sha256(source) != compilation["source_sha256"]:
        raise ValueError("Candidate source changed during evaluation")
    model_len, payload_len = struct.unpack("<II", encoded[12:20])
    return {"source_sha256": sha256(source), "build": compilation, "correctness": checks,
            "input_sha256": digest(sample), "input_bytes": len(sample), "archive_bytes": len(encoded),
            "framing_bytes": 40, "model_bytes": model_len, "payload_bytes": payload_len,
            "trained_model_bytes": len(model), "trained_model_sha256": digest(model),
            "raw_escape": encoded[4] == 1, "archive_sha256": digest(encoded), "roundtrip": True,
            "encode": encoding, "decode": decoding,
            "timing_scope": "Includes local isolation setup and process startup; not warmed codec throughput"}
