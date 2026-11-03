"""Build and measure the actual Rust release client under existing containment."""

import json
import os
import platform
from pathlib import Path
import random
import shutil
import struct
import tempfile

from .. import archive, baselines, codecs, evaluator
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
    if registry.get("schema_version") != 1 or not 1 <= len(registry["codecs"]) <= 128:
        raise ValueError("Invalid release registry")
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
        codecs.frame_metadata(value[9:], model=model, source_sha256=source_sha256, restored=data)
    elif operation == 2:
        codecs.frame_metadata(data, model=model, source_sha256=source_sha256, restored=value[9:])
    elif operation == 3 and len(value) - 9 > 65536:
        raise ValueError("Candidate model exceeds 64 KiB")
    elif operation == 5 and len(value) - 9 != original_len:
        raise ValueError("Direct decoded length mismatch")
    return value[9:], measurement


def correctness(binary, item, cfg, sample, model):
    cases = [b"", b"\0", b"a" * 63, bytes(range(256)), b"a,b\r\n1.00,-0\r\n",
             b'{"x":1.0,"x":-0}\n', random.Random(cfg["seed"]).randbytes(127), sample[:2048]]
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
    kind = "trained on development training prefix only"
    try:
        baselines.measure(["zstd", "-q", "--train", "-T1", "--dictID=1", "--maxdict=8192", "-o", str(model), *map(str, paths)], b"", cfg, ro=paths, rw=[directory])
    except ValueError:
        model.write_bytes(training[:8192])
        kind = "raw training-prefix dictionary; sample too small for statistical dictionary training"
    return model, {"kind": kind, "bytes": model.stat().st_size, "sha256": sha256(model), "training_sha256": digest(training)}


def references(cfg, data, dictionary):
    rows = []
    for baseline in cfg["baselines"]:
        model = dictionary.read_bytes() if baseline.get("dictionary") else b""
        args = lambda key: [part.replace("{dictionary}", str(dictionary)).replace("{input_bytes}", str(len(data))) for part in baseline[key]]
        encoded, encoding = baselines.measure(args("encode"), data, cfg, ro=[dictionary] if model else [])
        decodes = []
        for _ in range(cfg["evaluation"]["decode_repetitions"]):
            restored, measurement = baselines.measure(args("decode"), encoded, cfg, ro=[dictionary] if model else [])
            if restored != data:
                raise ValueError("Cloud baseline reconstruction failed")
            decodes.append(measurement)
        row, _ = evaluator.counted(baseline["id"], [("sample.bin", encoded)], model, [{"label": "sample.bin", "input_bytes": len(data),
            "block_bytes": len(encoded), "encode": encoding, "decode": evaluator.resources(decodes, len(data))}])
        rows.append(row)
    best = min(rows, key=lambda row: row["archive_bytes"])
    return {"results": rows, "best_id": best["id"], "best_bytes": best["archive_bytes"]}


def measure(binary, item, cfg, data, model, baseline, *, checks):
    encoded, encoding = invoke(binary, item["id"], cfg, 6, data, model=model, source_sha256=item["source_sha256"])
    decodes = []
    for _ in range(cfg["evaluation"]["decode_repetitions"]):
        restored, measurement = invoke(binary, item["id"], cfg, 2, encoded, model=model, source_sha256=item["source_sha256"])
        if restored != data:
            raise ValueError("Cloud candidate reconstruction failed")
        decodes.append(measurement)
    used_model = model if encoded[4] == 2 else b""
    candidate, packed = evaluator.counted(item["id"], [("sample.bin", encoded)], used_model, [{"label": "sample.bin", "input_bytes": len(data),
        "input_sha256": digest(data), "block_bytes": len(encoded), "encode": encoding, "decode": evaluator.resources(decodes, len(data))}])
    candidate.update(source_sha256=item["source_sha256"], model_sha256=digest(model), correctness=checks, decoder_package_bytes=binary.stat().st_size)
    score = evaluator.score(cfg, baseline, candidate)
    return {"input_sha256": digest(data), "input_bytes": len(data), "candidate": candidate, "baselines": baseline, "score": score,
            "eligible": score["gates"]["exact_reconstruction"] and score["gates"]["decode_rss"],
            "size_first_win": score["archive_gain"] > 0 and score["gates"]["decode_rss"], "speed_priority": "secondary"}, packed


def console_roundtrip(binary, cfg, data, identifier, packed):
    with tempfile.TemporaryDirectory(prefix="algomini-console-") as temporary:
        directory = Path(temporary)
        original, encoded, restored = (directory / name for name in ("sample.bin", "sample.alc", "restored.bin"))
        original.write_bytes(data)
        result = run_isolated([binary, "compress", "--plugin", identifier, original, encoded], cwd=directory, ro=[binary, original], rw=[directory],
            timeout=cfg["limits"]["candidate_encode_timeout_seconds"], memory_mib=cfg["limits"]["candidate_memory_mib"])
        if result["returncode"] or encoded.read_bytes() != packed:
            raise ValueError("Public CLI bytes differ from measured worker archive")
        original.unlink()
        result = run_isolated([binary, "decompress", encoded, restored], cwd=directory, ro=[binary, encoded], rw=[directory],
            timeout=cfg["limits"]["candidate_decode_timeout_seconds"], memory_mib=cfg["limits"]["candidate_memory_mib"])
        if result["returncode"] or restored.read_bytes() != data:
            raise ValueError("Public CLI reconstruction failed after original deletion")
    return {"exact": True, "original_deleted": True, "same_archive_as_worker": True, "isolation": result["isolation"]}
