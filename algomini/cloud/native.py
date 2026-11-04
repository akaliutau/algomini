"""Build and measure the actual Rust release client under existing containment."""

import copy
import json
import os
import platform
from pathlib import Path
import random
import shutil
import struct
import tempfile
import zlib

from .. import archive, measurement as metrics
from ..common import digest, sha256, worker_environment, write_json
from ..environment import command_output
from ..execution import run_isolated


def host_environment():
    cpu = next((line.partition(":")[2].strip() for line in Path("/proc/cpuinfo").read_text().splitlines()
                if line.startswith("model name")), platform.machine())
    return {"kernel": platform.release(), "machine": platform.machine(), "python": platform.python_version(),
            "cpu": cpu, "affinity_cpus": len(os.sched_getaffinity(0)), "os_release": platform.freedesktop_os_release()}


def driver_files(library):
    crate = Path(library) / "crates/algomini-cli"
    return [crate / name for name in ("Cargo.toml", "Cargo.lock", "build.rs")] + sorted((crate / "src").glob("*.rs"))


def runtime(library, image):
    library = Path(library).resolve()
    tools = {}
    for name, argv in {"rustc": ["rustup", "which", "rustc"], "cargo": ["rustup", "which", "cargo"]}.items():
        path = Path(command_output(argv))
        tools[name] = {"path": str(path), "sha256": sha256(path), "version": command_output([str(path), "--version"])}
    trusted = [library / name for name in ("run.toml", "cloud/policy.json")]
    trusted += sorted((library / "algomini").glob("*.py")) + sorted((library / "algomini/cloud").glob("*.py"))
    return {"schema_version": 1, "image": image, "tools": tools,
            "trusted_sources": {str(path.relative_to(library)): sha256(path) for path in trusted},
            "driver": {str(path.relative_to(library)): sha256(path) for path in driver_files(library)},
            "adapter_sha256": sha256(__file__), "execution_sha256": sha256(Path(__file__).parents[1] / "execution.py"),
            "isolation_sha256": sha256(Path(__file__).parents[1] / "_isolate.py")}


def assets(library, registry):
    result = {}
    root = Path(library).resolve()
    if registry.get("schema_version") != 1 or not 0 <= len(registry["codecs"]) <= 128:
        raise ValueError("Invalid release registry")
    identifiers = {item["id"] for item in registry["codecs"]}
    if any(identifier not in identifiers for identifier in registry["aliases"].values()):
        raise ValueError("Release alias points to an unknown codec")
    for item in registry["codecs"]:
        for field, checksum, maximum in (("source", "source_sha256", 65536), ("model", "model_sha256", 65536)):
            relative = Path(item[field])
            path = root / relative
            if (relative.is_absolute() or ".." in relative.parts or not path.resolve().is_relative_to(root / "plugins")
                    or path.is_symlink() or not path.is_file() or path.stat().st_size > maximum or sha256(path) != item[checksum]):
                raise ValueError("Release asset path, size or checksum mismatch")
            result[item[field]] = path.read_bytes()
    return result


def build(library, workspace, registry, *, asset_root=None, image="local"):
    library, workspace = Path(library).resolve(), Path(workspace).resolve()
    # Reports/request metadata do not affect code generation or reproducible builds.
    registry = {"schema_version": 1, "aliases": registry["aliases"], "codecs": [
        {key: item[key] for key in ("id", "source", "source_sha256", "codec_id", "model", "model_sha256")}
        for item in registry["codecs"]]}
    files = assets(asset_root or library, registry)
    definition = {"runtime": runtime(library, image), "registry": registry}
    key = digest(json.dumps(definition, sort_keys=True).encode())
    directory = workspace / "builds" / key
    record, binary = directory / "build.json", directory / "algomini"
    if record.exists():
        value = json.loads(record.read_text())
        if sha256(binary) != value["binary_sha256"] or value["definition"] != definition:
            raise ValueError("Cloud build cache changed")
        return binary, value
    directory.mkdir(parents=True, exist_ok=True)
    for path in driver_files(library):
        destination = directory / path.relative_to(library)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)
    for relative, data in files.items():
        destination = directory / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    write_json(directory / "release/registry.json", registry)
    cargo_home = directory / ".cargo"
    cargo_home.mkdir(exist_ok=True)
    cache = Path(os.environ.get("CARGO_HOME", Path.home() / ".cargo")) / "registry"
    if not (cargo_home / "registry").exists():
        (cargo_home / "registry").symlink_to(cache.resolve(), target_is_directory=True)
    temporary = directory / ".tmp"
    temporary.mkdir(exist_ok=True)
    target = workspace / "target"
    target.mkdir(exist_ok=True)
    rustc = Path(definition["runtime"]["tools"]["rustc"]["path"])
    cargo = definition["runtime"]["tools"]["cargo"]["path"]
    env = worker_environment()
    env.update(RUSTC=str(rustc), CARGO_HOME=str(cargo_home), CARGO_TARGET_DIR=str(target), CARGO_BUILD_JOBS="1",
               TMPDIR=str(temporary), SOURCE_DATE_EPOCH="1", RUSTFLAGS=f"--remap-path-prefix={workspace.resolve()}=/algomini-work")
    measured = run_isolated([cargo, "build", "--release", "--offline", "--locked", "--manifest-path", directory / "crates/algomini-cli/Cargo.toml"],
        cwd=directory, ro=[rustc.parent.parent, cache, directory, target], rw=[directory, target], env=env,
        timeout=300, memory_mib=2048, output_bytes=8 * 1024**2, mode="build")
    if measured["returncode"] or measured["timed_out"]:
        raise ValueError("Contained release build failed: " + measured["stderr"].decode(errors="replace")[-3000:])
    shutil.copyfile(target / "release/algomini", binary)
    binary.chmod(0o755)
    value = {"definition": definition, "binary_sha256": sha256(binary), "binary_bytes": binary.stat().st_size,
             "build_seconds": measured["wall_seconds"], "isolation": measured["isolation"]}
    write_json(record, value)
    return binary, value


def invoke(binary, identifier, cfg, operation, data, *, original_len=0, model=b"", source_sha256=None):
    if len(data) > cfg["limits"]["candidate_max_output_bytes"] + 65536 or len(model) > 65536:
        raise ValueError("Cloud worker request exceeds bounds")
    request = b"ALQ1" + bytes([operation]) + struct.pack("<III", original_len, len(model), len(data)) + model + data
    timeout = cfg["limits"]["candidate_encode_timeout_seconds"] if operation in (1, 3, 4, 6) else cfg["limits"]["candidate_decode_timeout_seconds"]
    with tempfile.TemporaryDirectory(prefix="algomini-cloud-worker-") as temporary:
        measurement = run_isolated([binary, "__worker", identifier], cwd=temporary, ro=[binary], data=request,
            timeout=timeout, memory_mib=cfg["limits"]["candidate_memory_mib"], output_bytes=4 * 1024**2 + 65600)
    value = measurement.pop("stdout")
    error = measurement.pop("stderr")
    if (measurement["returncode"] or measurement["timed_out"] or len(value) < 9 or value[:5] != b"ALR1\0"
            or struct.unpack("<I", value[5:9])[0] != len(value) - 9):
        raise ValueError("Contained release worker failed: " + (value[9:] + error).decode(errors="replace")[-1500:])
    if operation in (1, 6):
        metrics.frame_metadata(value[9:], model=model, source_sha256=source_sha256, restored=data)
    elif operation == 2:
        metrics.frame_metadata(data, model=model, source_sha256=source_sha256, restored=value[9:])
    elif operation == 3 and len(value) - 9 > 65536:
        raise ValueError("Candidate model exceeds 64 KiB")
    elif operation == 5 and len(value) - 9 != original_len:
        raise ValueError("Direct decoded length mismatch")
    return value[9:], measurement


def correctness(binary, item, cfg, sample, model):
    cases = [b"", b"\0", b"a" * 63, bytes(range(256)), b"a,b\r\n1.00,-0\r\n",
             b'{"x":1.0,"x":-0}\n', b'[{"escaped":"a\\\"b","number":1e+03},',
             b'{"x":true}\n{"x":null}\r\n', b'"a,b",c\r\n"two\nlines",-0.00\r\n',
             '<x>\u00e9\u6f22</x>\r\n'.encode(), 'hello\u00e9'.encode('utf-16'),
             random.Random(cfg["seed"]).randbytes(127), sample[:2048]]
    for data in cases:
        encoded, _ = invoke(binary, item["id"], cfg, 4, data, model=model)
        decoded, _ = invoke(binary, item["id"], cfg, 5, encoded, model=model, original_len=len(data))
        if data != decoded:
            raise ValueError("Candidate failed tiny direct roundtrip")
    return {"passed": True, "direct_cases": len(cases)}


def reference_dictionary(workspace, cfg, training):
    directory = Path(workspace) / "dictionary"
    directory.mkdir(exist_ok=True)
    paths = []
    for index in range(16):
        path = directory / f"sample-{index:02d}"
        path.write_bytes(training[index * len(training) // 16:(index + 1) * len(training) // 16])
        paths.append(path)
    model = directory / "dictionary.bin"
    if not training:
        model.write_bytes(b"")
        return model, {"kind": "unavailable: empty development training partition", "bytes": 0, "sha256": digest(b""), "training_sha256": digest(training)}
    kind = "trained on development training prefix only"
    try:
        metrics.measure(["zstd", "-q", "--train", "-T1", "--dictID=1", "--maxdict=8192", "-o", str(model), *map(str, paths)], b"", cfg, ro=paths, rw=[directory])
    except ValueError:
        model.write_bytes(training[:8192])
        kind = "raw training-prefix dictionary; sample too small for statistical dictionary training"
    return model, {"kind": kind, "bytes": model.stat().st_size, "sha256": sha256(model), "training_sha256": digest(training)}


def references(cfg, data, dictionary):
    if len(data) > cfg["limits"].get("file_max_input_bytes", cfg["lab"]["max_input_bytes"]):
        raise ValueError("Baseline input exceeds the configured file cap")
    rows, skipped = [], []
    for baseline in cfg["baselines"]:
        if baseline.get("dictionary") and not dictionary.stat().st_size:
            skipped.append({"id": baseline["id"], "reason": "No nonempty development training dictionary"})
            continue
        model = dictionary.read_bytes() if baseline.get("dictionary") else b""
        args = lambda key: [part.replace("{dictionary}", str(dictionary)).replace("{input_bytes}", str(len(data))) for part in baseline[key]]
        encoded, encoding = metrics.measure(args("encode"), data, cfg, ro=[dictionary] if model else [])
        decodes = []
        for _ in range(cfg["evaluation"]["decode_repetitions"]):
            restored, measurement = metrics.measure(args("decode"), encoded, cfg, ro=[dictionary] if model else [])
            if restored != data:
                raise ValueError("Cloud baseline reconstruction failed")
            decodes.append(measurement)
        # Split only the counted container, never the baseline compression stream.
        blocks = [("sample.bin", encoded)] if len(encoded) <= archive.MAX_BLOCK else [
            (f"sample.bin.part-{index//archive.MAX_BLOCK:03d}", encoded[index:index+archive.MAX_BLOCK])
            for index in range(0, len(encoded), archive.MAX_BLOCK)]
        row, _ = metrics.counted(baseline["id"], blocks, model, [{"label": "sample.bin", "input_bytes": len(data),
            "block_bytes": len(encoded), "encode": encoding, "decode": metrics.resources(decodes, len(data))}])
        rows.append(row)
    best = min(rows, key=lambda row: row["archive_bytes"])
    return {"results": rows, "best_id": best["id"], "best_bytes": best["archive_bytes"], "skipped": skipped}


def measure(binary, item, cfg, data, model, baseline, *, checks):
    if len(data) > archive.CHUNK_BYTES:
        return measure_large(binary, item, cfg, data, model, baseline, checks=checks)
    encoded, encoding = invoke(binary, item["id"], cfg, 6, data, model=model, source_sha256=item["source_sha256"])
    decodes = []
    for _ in range(cfg["evaluation"]["decode_repetitions"]):
        restored, measurement = invoke(binary, item["id"], cfg, 2, encoded, model=model, source_sha256=item["source_sha256"])
        if restored != data:
            raise ValueError("Cloud candidate reconstruction failed")
        decodes.append(measurement)
    used_model = model if encoded[4] == 2 else b""
    candidate, packed = metrics.counted(item["id"], [("sample.bin", encoded)], used_model, [{"label": "sample.bin", "input_bytes": len(data),
        "input_sha256": digest(data), "block_bytes": len(encoded), "encode": encoding, "decode": metrics.resources(decodes, len(data))}])
    candidate.update(source_sha256=item["source_sha256"], model_sha256=digest(model), correctness=checks, decoder_package_bytes=binary.stat().st_size)
    meta = metrics.frame_metadata(encoded, model=model, source_sha256=item["source_sha256"])
    candidate.update(payload_bytes=meta["payload_bytes"], native_frame_bytes=40,
                     raw_escape=meta["flag"] == 1, trained_model_bytes=len(model))
    if meta["flag"] == 1:
        candidate["encoding_mode"] = "raw"
    elif item.get("mechanism") == "json-lexical-v1":
        candidate["encoding_mode"] = {0: "zstd", 1: "json-lexical"}.get(encoded[40], "unknown")
    else:
        candidate["encoding_mode"] = "codec-specific; see source"
    score = metrics.score(cfg, baseline, candidate)
    return {"input_sha256": digest(data), "input_bytes": len(data), "candidate": candidate, "baselines": baseline, "score": score,
            "eligible": score["gates"]["exact_reconstruction"] and score["gates"]["decode_rss"],
            "size_first_win": score["archive_gain"] > 0 and score["gates"]["decode_rss"], "speed_priority": "secondary"}, packed


def file_cli(binary, cfg, data, *, identifier=None):
    """Each encode/decode has a fresh directory; no encoder side files reach decoding."""
    encoding = identifier is not None
    info = archive.unpack_chunked(data) if not encoding and data.startswith(b"ALC2") else None
    size = len(data) if encoding else info["original_bytes"] if info else 0
    large = size > archive.CHUNK_BYTES
    if size > cfg["limits"].get("file_max_input_bytes", archive.MAX_FILE):
        raise ValueError("Input exceeds the configured PoC file cap")
    chunks = max(1, (size + archive.CHUNK_BYTES - 1) // archive.CHUNK_BYTES)
    limit = archive.MAX_CHUNKED_ARCHIVE if large else 4 * 1024**2 + 65536
    with tempfile.TemporaryDirectory(prefix="algomini-file-") as temporary:
        directory = Path(temporary)
        original = directory / ("sample.bin" if encoding else "sample.alc")
        output = directory / ("sample.alc" if encoding else "restored.bin")
        original.write_bytes(data)
        argv = [binary, "compress", "--plugin", identifier, original, output] if encoding else [binary, "decompress", original, output]
        result = run_isolated(argv, cwd=directory, ro=[binary, original], rw=[directory],
            timeout=cfg["limits"]["candidate_encode_timeout_seconds" if encoding else "candidate_decode_timeout_seconds"] * chunks,
            memory_mib=cfg["limits"].get("file_memory_mib", 1024) if large else cfg["limits"]["candidate_memory_mib"],
            output_bytes=limit)
        if result["returncode"] or result["timed_out"] or not output.is_file() or output.is_symlink():
            raise ValueError("Contained file CLI failed: " + result["stderr"].decode(errors="replace")[-1000:])
        with output.open("rb") as stream:
            value = stream.read(limit + 1)
        if len(value) > limit:
            raise ValueError("File CLI exceeded its output cap")
        original.unlink()
    result.pop("stdout"); result.pop("stderr")
    return value, result


def measure_large(binary, item, cfg, data, model, baseline, *, checks):
    packed, encoding = file_cli(binary, cfg, data, identifier=item["id"])
    info = archive.unpack_chunked(packed)
    if info["method"] != item["id"] or info["model"] != model or info["original_bytes"] != len(data) or info["checksum"] != zlib.crc32(data):
        raise ValueError("Chunked archive differs from the frozen codec/model/input")
    decodes = []
    for _ in range(cfg["evaluation"]["decode_repetitions"]):
        restored, measurement = file_cli(binary, cfg, packed)
        if restored != data:
            raise ValueError("Large file reconstruction failed")
        decodes.append(measurement)
    frames = info["frames"]
    metadata = [metrics.frame_metadata(frame, model=model, source_sha256=item["source_sha256"],
                restored=data[index*archive.CHUNK_BYTES:(index+1)*archive.CHUNK_BYTES]) for index, frame in enumerate(frames)]
    frame_bytes = sum(map(len, frames))
    candidate = {"id": item["id"], "archive_bytes": len(packed), "archive_sha256": digest(packed),
        "block_bytes": frame_bytes, "model_bytes": len(model), "collection_bytes": len(packed)-frame_bytes-len(model),
        "files": [{"label": "sample.bin", "input_bytes": len(data), "input_sha256": digest(data), "block_bytes": frame_bytes,
                   "encode": encoding, "decode": metrics.resources(decodes, len(data))}],
        "roundtrip": True, "correctness": checks, "source_sha256": item["source_sha256"], "model_sha256": digest(model),
        "decoder_package_bytes": binary.stat().st_size, "payload_bytes": sum(row["payload_bytes"] for row in metadata),
        "native_frame_bytes": 40*len(frames), "raw_escape": all(row["flag"] == 1 for row in metadata),
        "trained_model_bytes": len(model), "encoding_mode": "chunked", "chunk_count": len(frames), "chunk_bytes": archive.CHUNK_BYTES}
    file_cfg = copy.deepcopy(cfg)
    file_cfg["limits"]["candidate_memory_mib"] = cfg["limits"].get("file_memory_mib", 1024)
    score = metrics.score(file_cfg, baseline, candidate)
    return {"input_sha256": digest(data), "input_bytes": len(data), "candidate": candidate, "baselines": baseline, "score": score,
            "eligible": score["gates"]["exact_reconstruction"] and score["gates"]["decode_rss"],
            "size_first_win": score["archive_gain"] > 0 and score["gates"]["decode_rss"], "speed_priority": "secondary"}, packed


def console_roundtrip(binary, cfg, data, identifier, packed):
    encoded, _ = file_cli(binary, cfg, data, identifier=identifier)
    if encoded != packed:
        raise ValueError("Public CLI bytes differ from the counted archive")
    restored, result = file_cli(binary, cfg, packed)
    if restored != data:
        raise ValueError("Public CLI reconstruction failed after original deletion")
    return {"exact": True, "original_deleted": True, "same_archive_as_worker": len(data) <= archive.CHUNK_BYTES,
            "same_archive_as_measurement": True, "isolation": result["isolation"]}
