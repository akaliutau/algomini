import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from algomini import coder, installation, installed, sealed, selection
from algomini.common import digest, load_config, sha256, worker_environment, write_json


ROOT = Path(__file__).parents[1]


@pytest.fixture
def cfg():
    return copy.deepcopy(load_config(ROOT / "run.toml")[1])


def private_fixture(root, cfg, entries=None):
    base = root / cfg["dataset"]["sealed_dir"]
    base.mkdir(parents=True)
    data = b'a,b\r\n" 1.00",-0\r\n'
    path = base / "test-000.csv"
    path.write_bytes(data)
    entries = entries or [{"path": path.relative_to(root).as_posix(), "bytes": len(data), "sha256": digest(data), "groups": ["synthetic-day"]}]
    write_json(base / "manifest.json", {"schema_version": 1, "test": entries})
    return base, data, sha256(base / "manifest.json")


def test_sealed_loader_requires_commitment_and_every_exact_file(tmp_path, cfg):
    base, data, commitment = private_fixture(tmp_path, cfg)
    samples, entries = sealed.read_samples(tmp_path, cfg, commitment)
    assert samples == [("test-000.csv", data)] and entries[0]["bytes"] == len(data)
    with pytest.raises(ValueError, match="commitment mismatch"):
        sealed.read_samples(tmp_path, cfg, "0" * 64)
    (base / "test-000.csv").write_bytes(data + b"changed")
    with pytest.raises(ValueError, match="size/checksum"):
        sealed.read_samples(tmp_path, cfg, commitment)


@pytest.mark.parametrize("failure", ["escape", "duplicate", "oversize", "symlink"])
def test_sealed_loader_rejects_invalid_manifest_entries(tmp_path, cfg, failure):
    base, data, _ = private_fixture(tmp_path, cfg)
    manifest_path = base / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    entry = manifest["test"][0]
    if failure == "escape":
        entry["path"] = "outside.csv"
    elif failure == "duplicate":
        manifest["test"].append(dict(entry))
    elif failure == "oversize":
        entry["bytes"] = cfg["lab"]["max_input_bytes"] + 1
    else:
        (base / "test-000.csv").unlink()
        outside = tmp_path / "outside.csv"
        outside.write_bytes(data)
        (base / "test-000.csv").symlink_to(outside)
    write_json(manifest_path, manifest)
    with pytest.raises(ValueError, match="Invalid sealed entry"):
        sealed.read_samples(tmp_path, cfg, sha256(manifest_path))


def test_claim_is_exclusive_and_interruption_cannot_rerun(tmp_path):
    sealed.claim(tmp_path, {"state": "consumed"})
    with pytest.raises(FileExistsError):
        sealed.claim(tmp_path, {"state": "again"})
    with pytest.raises(ValueError, match="cannot be rerun"):
        sealed.final_result(tmp_path)


def test_protocol_failure_does_not_claim_or_open_holdout(tmp_path, cfg, monkeypatch):
    def fail(*args):
        raise ValueError("synthetic protocol mismatch")

    monkeypatch.setattr(sealed, "verify_protocol", fail)
    monkeypatch.setattr(sealed, "read_samples", lambda *args: pytest.fail("holdout opened before protocol verified"))
    with pytest.raises(ValueError, match="protocol mismatch"):
        sealed.run(tmp_path, cfg)
    assert not (tmp_path / sealed.CONSUMED).exists()


@pytest.mark.parametrize("status", ["failed-gates", "failed-verification"])
def test_failed_outcome_cannot_be_packaged(tmp_path, cfg, monkeypatch, status):
    monkeypatch.setattr(sealed, "final_result", lambda *args: {"status": status})
    with pytest.raises(ValueError, match="no package"):
        installation.package(tmp_path, cfg)
    assert not (tmp_path / "packages").exists()


def test_frozen_model_is_used_without_candidate_training(tmp_path, cfg, monkeypatch):
    calls = []
    model = b"counted-model"
    samples = [("a.csv", b"a" * 100), ("b.csv", b"b" * 100)]

    def invoke(root, cfg, binary, operation, data, **kwargs):
        calls.append(operation)
        assert kwargs["model"] == model
        if operation == 6:
            frame = bytearray(b"x" * 40 + data)
            frame[4] = 2
            return bytes(frame), {"execution_seconds": .001, "peak_rss_kib": 100}
        assert operation == 2
        return data[40:], {"execution_seconds": .001, "peak_rss_kib": 100}

    monkeypatch.setattr(sealed.codecs, "invoke", invoke)
    frozen = {"candidate_id": "candidate", "source": {"sha256": "source"}, "model": {"sha256": digest(model)}, "compiler": {"binary_bytes": 1000}}
    result, packed = sealed.candidate_corpus(tmp_path, cfg, frozen, None, model, samples, {"passed": True})
    assert 3 not in calls and result["candidate_training_calls"] == 0
    assert result["model_bytes"] == len(model)
    assert installed.archive.unpack(packed)[1] == model


def test_failed_sealed_attempt_is_final_and_never_opens_again(tmp_path, cfg, monkeypatch):
    source = tmp_path / "codec.rs"
    source.write_text("source")
    model = tmp_path / "model.bin"
    model.write_bytes(b"")
    frozen = {"source": {"path": "codec.rs"}, "model": {"path": "model.bin"}}
    write_json(tmp_path / selection.RECORD, frozen)
    dictionary = tmp_path / "dictionary"
    dictionary.write_bytes(b"dictionary")
    sample = tmp_path / "train.csv"
    sample.write_bytes(b"a,b\n1,2\n")
    protocol = {"baseline_dictionary_sha256": sha256(dictionary), "baseline_dictionary_training_sha256": [],
                "evaluator_freeze_id": "evaluator", "sealed_manifest_commitment": "commitment"}
    monkeypatch.setattr(sealed, "verify_protocol", lambda *args: (protocol, "protocol"))
    monkeypatch.setattr(sealed.codecs, "build", lambda *args: (None, {"binary_sha256": "binary"}))
    monkeypatch.setattr(sealed.codecs, "correctness", lambda *args: {"passed": True})
    monkeypatch.setattr(sealed.baselines, "train_dictionary", lambda *args: (dictionary, {"sha256": sha256(dictionary), "training_sample_sha256": []}))
    monkeypatch.setattr(sealed, "development_files", lambda *args: [sample])
    import algomini.environment as environment
    monkeypatch.setattr(environment, "command_output", lambda *args: "pre-seal-commit")

    def fail(root, cfg, commitment):
        assert (root / sealed.CONSUMED).exists()
        raise ValueError("synthetic committed input failure")

    monkeypatch.setattr(sealed, "read_samples", fail)
    first = sealed.run(tmp_path, cfg)
    assert first["status"] == "failed-verification"
    monkeypatch.setattr(sealed, "read_samples", lambda *args: pytest.fail("failed outcome reopened sealed data"))
    assert sealed.run(tmp_path, cfg) == first
    with pytest.raises(ValueError, match="no package"):
        installation.package(tmp_path, cfg)


def test_new_generation_is_disabled_after_sealed_consumption(tmp_path, cfg):
    sealed.claim(tmp_path, {"state": "consumed"})
    executable = tmp_path / cfg["coder"]["executable"]
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"fake-coder")
    with pytest.raises(ValueError, match="new candidate generation is disabled"):
        coder.generate(tmp_path, cfg, "new source", b"train", phase="refinement", kind="evolved")
    assert not (tmp_path / "reports/budget/ledger.json").exists()


def test_passing_evidence_requires_all_four_actual_gates():
    assert not installed.passing({"status": "passed", "score": {"gates": {}}})
    gates = {key: True for key in installed.GATES}
    assert installed.passing({"status": "passed", "score": {"gates": gates}})
    gates["exact_reconstruction"] = False
    assert not installed.passing({"status": "passed", "score": {"gates": gates}})


def test_native_package_installs_and_decodes_without_repo_or_original(tmp_path, cfg, monkeypatch):
    # The passing evidence is explicitly synthetic; no real holdout is opened.
    for name in installation.RUNTIME:
        destination = tmp_path / f"algomini/{name}.py"
        destination.parent.mkdir(exist_ok=True, parents=True)
        shutil.copyfile(ROOT / f"algomini/{name}.py", destination)
    shutil.copytree(ROOT / "rust/codec-worker", tmp_path / "rust/codec-worker", ignore=shutil.ignore_patterns("target"))
    shutil.copytree(ROOT / selection.ARTIFACTS, tmp_path / selection.ARTIFACTS)
    (tmp_path / "repro").mkdir()
    shutil.copyfile(ROOT / selection.RECORD, tmp_path / selection.RECORD)
    public = tmp_path / cfg["dataset"]["manifest"]
    public.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / cfg["dataset"]["manifest"], public)
    fixture = {"status": "passed", "test_fixture": True, "candidate_freeze_id": sealed.EXPECTED_CANDIDATE,
               "protocol_id": "synthetic", "evaluator_freeze_id": "synthetic",
               "score": {"gates": {key: True for key in installed.GATES}}}
    write_json(tmp_path / "reports/stage5/result.json", fixture)
    monkeypatch.setattr(sealed, "final_result", lambda *args: fixture)
    monkeypatch.setattr(sealed, "verify_protocol", lambda *args: ({"evaluator_freeze_id": "synthetic"}, "synthetic"))
    package, manifest, package_id = installation.package(tmp_path, cfg)
    destination = tmp_path / "fresh-install/plugin"
    installation.install(package, destination, expected_id=package_id)
    data = b'a,b\r\n" 60",1.00\r\n' * 40
    original = tmp_path / "original.csv"
    original.write_bytes(data)
    env = worker_environment()
    env["PATH"] = "/usr/bin:/bin"
    entry = [sys.executable, "-I", "-B", str(destination / "decode.py"), "--plugin", str(destination)]
    subprocess.run([*entry, "encode", str(original), str(tmp_path / "encoded.alc")], cwd=tmp_path,
                   env=env, check=True, capture_output=True, timeout=60)
    original.unlink()
    subprocess.run([*entry, "decode", str(tmp_path / "encoded.alc"), str(tmp_path / "decoded")], cwd=tmp_path,
                   env=env, check=True, capture_output=True, timeout=60)
    assert (tmp_path / "decoded/original.csv").read_bytes() == data
    assert installed.verify(destination, expected_id=package_id)[1] == package_id
    (destination / "model.bin").write_bytes(b"changed")
    with pytest.raises(ValueError, match="checksum mismatch"):
        installed.verify(destination, expected_id=package_id)
