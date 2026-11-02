"""Freeze one validation-selected source/model for a later single sealed run."""

from datetime import datetime, timezone
import json
from pathlib import Path
import struct
import tomllib

from . import codecs, evaluator, freeze, registry
from .common import digest, sha256, write_json


RECORD = "repro/candidate-freeze.json"
ANCHOR = "repro/candidate-freeze.sha256"
ARTIFACTS = "artifacts/stage4-selected"


def _file(root, relative):
    path = root / relative
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError("Frozen candidate artifact is missing or escapes the checkout")
    return path


def create(root, cfg, outcome, policy):
    if (root / RECORD).exists() or (root / ANCHOR).exists():
        raise ValueError("A candidate is already frozen; verify it instead of replacing the selection")
    pinned = freeze.verify(root, cfg)
    result_path = root / outcome["evidence"]
    result = json.loads(result_path.read_text())
    candidate = result["candidate"]
    if (result["split"] != "validation" or result["freeze_id"] != pinned["freeze_id"]
            or not all(result["score"]["gates"].values()) or outcome["source_sha256"] != candidate["source_sha256"]):
        raise ValueError("Only a correct validation-selected candidate passing every frozen gate may be frozen")
    inputs = [{"label": label, "bytes": len(data), "sha256": digest(data)} for label, data in evaluator.probes(root, cfg, "validation")]
    if inputs != result["inputs"]:
        raise ValueError("Selection validation inputs changed")
    plugin = registry.resolve(root, outcome["id"])
    if plugin["source_sha256"] != candidate["source_sha256"]:
        raise ValueError("Selected plugin source changed")
    binary, build = codecs.build(root, cfg, plugin["source_path"])
    if build["fingerprint"] != candidate["build"]["fingerprint"] or build["binary_sha256"] != candidate["build"]["binary_sha256"]:
        raise ValueError("Selected build differs from the measured executable")
    samples = evaluator.training_samples(root, cfg)
    training = b"".join(struct.pack("<I", len(value)) + value for value in samples)
    model, _ = codecs.invoke(root, cfg, binary, 3, training)
    if digest(model) != candidate["trained_model_sha256"] or [digest(value) for value in samples] != candidate["training_sample_sha256"]:
        raise ValueError("Selected model does not reproduce from the measured training inputs")
    directory = root / ARTIFACTS
    directory.mkdir(parents=True, exist_ok=True)
    source_path, model_path = directory / "codec.rs", directory / "model.bin"
    source_path.write_bytes(plugin["source_path"].read_bytes())
    model_path.write_bytes(model)
    write_json(directory / "validation.json", result)
    write_json(directory / "build.json", build)
    write_json(directory / "policy.json", policy)
    manifest_path = root / cfg["dataset"]["manifest"]
    manifest = json.loads(manifest_path.read_text())
    release = tomllib.loads((root / "rust/codec-worker/Cargo.toml").read_text())["profile"]["release"]
    files = {path.relative_to(root).as_posix(): sha256(path) for path in sorted(directory.iterdir()) if path.is_file()}
    record = {"schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(), "status": "frozen-for-single-sealed-verification",
              "evaluator_freeze_id": pinned["freeze_id"], "candidate_id": outcome["id"],
              "source": {"path": source_path.relative_to(root).as_posix(), "sha256": candidate["source_sha256"],
                         "plugin_source": plugin["source_path"].relative_to(root).as_posix()},
              "model": {"path": model_path.relative_to(root).as_posix(), "sha256": digest(model), "bytes": len(model),
                        "training_split": "train", "training_sample_sha256": candidate["training_sample_sha256"],
                        "counted_shared_model_bytes_on_validation": candidate["model_bytes"]},
              "parameters": outcome["definition"],
              "compiler": {"rustc": build["rustc"], "build_fingerprint": build["fingerprint"],
                           "binary_sha256": build["binary_sha256"], "binary_bytes": build["binary_bytes"],
                           "cargo_release_profile": release, "command": ["cargo", "build", "--release", "--offline", "--locked"],
                           "toolchain_and_native_dependencies": "Bound by evaluator_freeze_id"},
              "selection": {"split": "validation", "policy": "smallest complete archive passing every frozen gate; smaller decoder package, then source hash, break equal-size ties",
                            "inputs": result["inputs"], "archive_bytes": candidate["archive_bytes"],
                            "archive_sha256": candidate["archive_sha256"], "score": result["score"]},
              "dataset_manifest_sha256": sha256(manifest_path), "sealed_manifest_commitment": manifest["sealed"]["manifest_sha256"],
              "thresholds": {"minimum_archive_gain": cfg["objective"]["minimum_archive_gain"],
                             "minimum_decode_mib_per_second": cfg["limits"]["candidate_min_decode_mib_per_second"],
                             "memory_mib": cfg["limits"]["candidate_memory_mib"], "max_input_bytes": cfg["lab"]["max_input_bytes"]},
              "files": files, "verifier_sha256": sha256(__file__), "sealed_test_opened": False,
              "sealed_run_contract": "Load this exact source/model/parameters/build in the later trusted verifier. Never train on sealed inputs. One sealed outcome is final; no tuning or candidate replacement after viewing it.",
              "trust": "Publisher pins this record SHA-256 in a reviewed commit; adjacent hash alone is not authentication."}
    write_json(root / RECORD, record)
    identifier = sha256(root / RECORD)
    (root / ANCHOR).write_text(identifier + "\n")
    return verify(root, cfg, expected_id=identifier, rebuild=True)


def verify(root, cfg, *, expected_id=None, rebuild=False):
    identifier = sha256(root / RECORD)
    if identifier != (root / ANCHOR).read_text().strip() or (expected_id is not None and identifier != expected_id):
        raise ValueError("Candidate freeze checksum mismatch")
    record = json.loads((root / RECORD).read_text())
    freeze.verify(root, cfg, expected_id=record["evaluator_freeze_id"])
    if record["verifier_sha256"] != sha256(__file__):
        raise ValueError("Candidate freeze verifier changed")
    for relative, checksum in record["files"].items():
        if sha256(_file(root, relative)) != checksum:
            raise ValueError("Frozen candidate artifact checksum mismatch: " + relative)
    source = _file(root, record["source"]["path"])
    model = _file(root, record["model"]["path"])
    if (sha256(source) != record["source"]["sha256"] or sha256(model) != record["model"]["sha256"]
            or model.stat().st_size != record["model"]["bytes"]
            or registry.resolve(root, record["candidate_id"])["source_sha256"] != record["source"]["sha256"]):
        raise ValueError("Frozen candidate source/model binding changed")
    if rebuild:
        _, build = codecs.build(root, cfg, source)
        if build["fingerprint"] != record["compiler"]["build_fingerprint"] or build["binary_sha256"] != record["compiler"]["binary_sha256"]:
            raise ValueError("Rebuilt selected executable differs from its freeze")
    return {"ok": True, "candidate_freeze_id": identifier, "candidate_id": record["candidate_id"],
            "source_sha256": record["source"]["sha256"], "model_bytes": record["model"]["bytes"], "build_verified": rebuild}
