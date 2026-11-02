"""Single-use sealed verifier. No candidate search, training, or tuning."""

from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re

from . import archive, baselines, codecs, evaluator, freeze, selection
from .common import digest, load_config, sha256, write_json
from .dataset import bounded_sample, development_files


EXPECTED_CANDIDATE = "487251797c2b5facec5d9cd36a5621b9f119169728676b421cd93ceed2078f18"
PROTOCOL = "repro/sealed-protocol.json"
PROTOCOL_SHA = "repro/sealed-protocol.sha256"
OUTPUT = "reports/stage5"
CONSUMED = OUTPUT + "/consumed.json"
CODE = ("sealed", "installed", "installation", "coder")


def _code(root):
    return {f"algomini/{name}.py": sha256(root / f"algomini/{name}.py") for name in CODE}


def pin(root, cfg):
    if (root / PROTOCOL).exists() or (root / PROTOCOL_SHA).exists():
        raise ValueError("Sealed protocol already pinned; verify it instead of replacing it")
    selected = selection.verify(root, cfg, expected_id=EXPECTED_CANDIDATE, rebuild=True)
    frozen = json.loads((root / selection.RECORD).read_text())
    _, dictionary = baselines.train_dictionary(root, cfg)
    validation = json.loads((root / selection.ARTIFACTS / "validation.json").read_text())
    if dictionary["sha256"] != validation["baselines"]["dictionary"]["sha256"]:
        raise ValueError("Training-only baseline dictionary differs from the measured reference")
    protocol = {"schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
                "candidate_freeze_id": selected["candidate_freeze_id"], "evaluator_freeze_id": frozen["evaluator_freeze_id"],
                "code": _code(root), "sealed_manifest_commitment": frozen["sealed_manifest_commitment"],
                "baseline_dictionary_sha256": dictionary["sha256"],
                "baseline_dictionary_training_sha256": dictionary["training_sample_sha256"],
                "corpus": "All test entries, in committed manifest order; no probes, omissions or subsampling",
                "per_input_cap": min(cfg["split"]["chunk_bytes"], cfg["lab"]["max_input_bytes"]),
                "maximum_blocks": archive.MAX_BLOCKS, "decode_repetitions": cfg["evaluation"]["decode_repetitions"],
                "model": "Load the exact frozen model; never invoke candidate training",
                "once": "Claim before opening private manifest; failures/interruption consume the run; later commands return the stored result only",
                "installation": "Package/installation only after every frozen archive/resource/correctness gate passes",
                "trust": "Pin this protocol id and its pre-seal Git commit outside candidate control; adjacent hash alone is not authentication"}
    write_json(root / PROTOCOL, protocol)
    identifier = sha256(root / PROTOCOL)
    (root / PROTOCOL_SHA).write_text(identifier + "\n")
    return {"ok": True, "protocol_id": identifier, "candidate_freeze_id": EXPECTED_CANDIDATE}


def verify_protocol(root, cfg):
    identifier = sha256(root / PROTOCOL)
    if identifier != (root / PROTOCOL_SHA).read_text().strip():
        raise ValueError("Sealed protocol checksum mismatch")
    protocol = json.loads((root / PROTOCOL).read_text())
    if protocol["code"] != _code(root) or protocol["candidate_freeze_id"] != EXPECTED_CANDIDATE:
        raise ValueError("Pinned sealed verifier/runtime source changed")
    selection.verify(root, cfg, expected_id=EXPECTED_CANDIDATE, rebuild=True)
    freeze.verify(root, cfg, expected_id=protocol["evaluator_freeze_id"])
    return protocol, identifier


def claim(root, receipt):
    path = root / CONSUMED
    path.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation makes a missing/partial final result fail closed.
    with path.open("x") as stream:
        json.dump(receipt, stream, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def read_samples(root, cfg, commitment):
    base = root / cfg["dataset"]["sealed_dir"]
    private = base / "manifest.json"
    if base.is_symlink() or private.is_symlink() or not private.is_file():
        raise ValueError("Sealed manifest must be a regular file in its declared directory")
    with private.open("rb") as stream:
        raw = stream.read(1024**2 + 1)
    if len(raw) > 1024**2 or digest(raw) != commitment:
        raise ValueError("Sealed manifest commitment mismatch")
    manifest = json.loads(raw)
    entries = manifest.get("test", [])
    if manifest.get("schema_version") != 1 or not isinstance(entries, list) or not 0 < len(entries) <= archive.MAX_BLOCKS:
        raise ValueError("Invalid sealed manifest dimensions")
    samples, rows, labels = [], [], set()
    cap = min(cfg["split"]["chunk_bytes"], cfg["lab"]["max_input_bytes"])
    for entry in entries:
        relative = Path(entry["path"])
        path = root / relative
        label = path.name
        if (relative.is_absolute() or path.parent.resolve() != base.resolve() or path.is_symlink()
                or not path.is_file() or not archive.valid_label(label) or label in labels
                or not isinstance(entry["bytes"], int) or not 0 < entry["bytes"] <= cap
                or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])):
            raise ValueError("Invalid sealed entry path, dimensions or hash")
        labels.add(label)
        with path.open("rb") as stream:
            data = stream.read(cap + 1)
        if len(data) != entry["bytes"] or digest(data) != entry["sha256"]:
            raise ValueError("Sealed file size/checksum mismatch: " + label)
        samples.append((label, data))
        rows.append({"label": label, "bytes": len(data), "sha256": digest(data), "groups": entry["groups"]})
    return samples, rows


def candidate_corpus(root, cfg, frozen, binary, model, samples, checks):
    rows, blocks = [], []
    for label, data in samples:
        encoded, encoding = codecs.invoke(root, cfg, binary, 6, data, model=model, source_sha256=frozen["source"]["sha256"])
        decoding = []
        for _ in range(cfg["evaluation"]["decode_repetitions"]):
            restored, measurement = codecs.invoke(root, cfg, binary, 2, encoded, model=model, source_sha256=frozen["source"]["sha256"])
            if restored != data:
                raise ValueError("Sealed exact reconstruction failed: " + label)
            decoding.append(measurement)
        blocks.append((label, encoded))
        rows.append({"label": label, "input_bytes": len(data), "input_sha256": digest(data),
                     "block_bytes": len(encoded), "payload_bytes": len(encoded) - 40, "framing_bytes": 40,
                     "raw_escape": encoded[4] == 1, "encode": encoding, "decode": evaluator.resources(decoding, len(data))})
        print(f"[sealed] {label}: exact reconstruction", flush=True)
    stored_model = model if any(payload[4] == 2 for _, payload in blocks) else b""
    result, packed = evaluator.counted(frozen["candidate_id"], blocks, stored_model, rows)
    _, counted_model, counted_blocks = archive.unpack(packed)
    for (label, original), (stored_label, payload) in zip(samples, counted_blocks):
        restored, _ = codecs.invoke(root, cfg, binary, 2, payload, model=counted_model, source_sha256=frozen["source"]["sha256"])
        if label != stored_label or restored != original:
            raise ValueError("Counted sealed collection reconstruction failed")
    result.update(correctness=checks, source_sha256=frozen["source"]["sha256"],
                  trained_model_sha256=frozen["model"]["sha256"], frozen_model_used=True, candidate_training_calls=0,
                  decoder_package_bytes=frozen["compiler"]["binary_bytes"])
    return result, packed


def final_result(root):
    receipt_path = root / CONSUMED
    if not receipt_path.exists():
        raise ValueError("No sealed run has been consumed")
    terminal = root / OUTPUT / "terminal.json"
    result_path = root / OUTPUT / "result.json"
    if not terminal.is_file() or not result_path.is_file():
        raise ValueError("Sealed run was already claimed but has no final result; it cannot be rerun")
    state = json.loads(terminal.read_text())
    if state["result_sha256"] != sha256(result_path) or state["receipt_sha256"] != sha256(receipt_path):
        raise ValueError("Final sealed outcome/receipt checksum mismatch")
    result = json.loads(result_path.read_text())
    if "candidate" in result:
        artifact = root / OUTPUT / "archive.alc"
        if not artifact.is_file() or artifact.stat().st_size != result["candidate"]["archive_bytes"] or sha256(artifact) != result["candidate"]["archive_sha256"]:
            raise ValueError("Final sealed archive checksum mismatch")
    return result


def run(root, cfg):
    output = root / OUTPUT
    output.mkdir(parents=True, exist_ok=True)
    with (output / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        protocol, protocol_id = verify_protocol(root, cfg)
        if (root / CONSUMED).exists():
            result = final_result(root)
            if result["protocol_id"] != protocol_id:
                raise ValueError("Stored sealed result binds another protocol")
            return result
        # Only public/development artifacts are read before the durable claim.
        frozen = json.loads((root / selection.RECORD).read_text())
        binary, build = codecs.build(root, cfg, root / frozen["source"]["path"])
        model = (root / frozen["model"]["path"]).read_bytes()
        dictionary, training = baselines.train_dictionary(root, cfg)
        if (training["sha256"] != protocol["baseline_dictionary_sha256"]
                or training["training_sample_sha256"] != protocol["baseline_dictionary_training_sha256"]):
            raise ValueError("Preflight baseline dictionary changed")
        sample = bounded_sample(development_files(root, cfg, "train")[0], 2048)
        checks = codecs.correctness(root, cfg, binary, sample, model)
        from .environment import command_output
        receipt = {"schema_version": 1, "claimed_at": datetime.now(timezone.utc).isoformat(),
                   "protocol_id": protocol_id, "candidate_freeze_id": EXPECTED_CANDIDATE,
                   "evaluator_freeze_id": protocol["evaluator_freeze_id"],
                   "verifier_git_commit": command_output(["git", "-C", str(root), "rev-parse", "HEAD"]),
                   "sealed_manifest_commitment": protocol["sealed_manifest_commitment"],
                   "binary_sha256": build["binary_sha256"], "frozen_model_sha256": digest(model),
                   "baseline_dictionary_sha256": sha256(dictionary), "state": "consumed-before-private-manifest-open"}
        claim(root, receipt)
        try:
            samples, inputs = read_samples(root, cfg, protocol["sealed_manifest_commitment"])
            candidate, packed = candidate_corpus(root, cfg, frozen, binary, model, samples, checks)
            baseline = evaluator.baseline_corpus(root, cfg, samples)
            if baseline["dictionary"]["sha256"] != protocol["baseline_dictionary_sha256"]:
                raise ValueError("Baseline used another dictionary")
            reference = evaluator.reference_corpus(cfg, samples)
            scored = evaluator.score(cfg, baseline, candidate)
            verify_protocol(root, cfg)
            (output / "archive.alc").write_bytes(packed)
            result = {"schema_version": 1, "protocol_id": protocol_id, "candidate_freeze_id": EXPECTED_CANDIDATE,
                      "evaluator_freeze_id": protocol["evaluator_freeze_id"], "status": "passed" if all(scored["gates"].values()) else "failed-gates",
                      "split": "test", "inputs": inputs, "input_bytes": sum(len(data) for _, data in samples),
                      "candidate": candidate, "baselines": baseline, "reference": reference, "score": scored,
                      "sealed_manifest_commitment": protocol["sealed_manifest_commitment"],
                      "scope": "All committed later-day chunks in one house; no claim about unseen houses or scientific novelty",
                      "model_inference_calls": 0, "candidate_training_calls": 0, "docker_used": False,
                      "completed_at": datetime.now(timezone.utc).isoformat()}
        except Exception as error:
            # A verifier/tool/input failure also consumes the one allowed run.
            result = {"schema_version": 1, "protocol_id": protocol_id, "candidate_freeze_id": EXPECTED_CANDIDATE,
                      "evaluator_freeze_id": protocol["evaluator_freeze_id"], "status": "failed-verification", "split": "test",
                      "failure": f"{type(error).__name__}: {error}"[:4000], "installation_allowed": False,
                      "model_inference_calls": 0, "candidate_training_calls": 0, "docker_used": False,
                      "completed_at": datetime.now(timezone.utc).isoformat()}
        write_json(output / "result.json", result)
        write_json(output / "terminal.json", {"result_sha256": sha256(output / "result.json"),
                                              "receipt_sha256": sha256(root / CONSUMED), "status": result["status"]})
        return result


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="run.toml")
    parser.add_argument("--pin", action="store_true", help="Pin code/protocol before any sealed access")
    args = parser.parse_args()
    root, cfg = load_config(args.config)
    result = pin(root, cfg) if args.pin else run(root, cfg)
    print(json.dumps({key: result[key] for key in ("ok", "protocol_id", "status", "score") if key in result}, indent=2))
    return 0 if result.get("status", "passed") == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
