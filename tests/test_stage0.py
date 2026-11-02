import copy
import json
from pathlib import Path
import sys
import zipfile

import pytest

from algomini.common import digest, load_config, worker_environment
from algomini.dataset import bounded_sample, development_files, prepare
from algomini.environment import check_api
from algomini.lab import measure, profile, restore_tokens, transpose_tokens


@pytest.fixture
def experiment(tmp_path):
    _, original = load_config(Path(__file__).parents[1] / "run.toml")
    cfg = copy.deepcopy(original)
    cfg["split"]["chunk_bytes"] = 220
    lines = [b'"date",a,b\r\n']
    for day in range(1, 13):
        for row in range(3):
            lines.append(f'"2026-01-{day:02d} 00:{row * 10:02d}:00",{row}.00,{row}.00\r\n'.encode())
    source = b"".join(lines)
    archive = tmp_path / cfg["dataset"]["cache"]
    archive.parent.mkdir(parents=True)
    with zipfile.ZipFile(archive, "w") as stream:
        stream.writestr(cfg["dataset"]["member"], source)
    cfg["dataset"]["archive_sha256"] = digest(archive.read_bytes())
    return tmp_path, cfg, source


def test_chronological_split_preserves_bytes_and_sealed_commitment(experiment):
    root, cfg, source = experiment
    manifest = prepare(root, cfg)
    training = manifest["development"]["train"]
    validation = manifest["development"]["validation"]
    train_days = {day for entry in training for day in entry["groups"]}
    validation_days = {day for entry in validation for day in entry["groups"]}
    assert max(train_days) < min(validation_days)
    assert not train_days & validation_days
    gaps = set(manifest["split_policy"]["discarded_gap_groups"])
    assert len(gaps) == 2 and not gaps & (train_days | validation_days)
    header = source.splitlines(keepends=True)[0]
    for entry in training + validation:
        data = (root / entry["path"]).read_bytes()
        assert len(data) <= cfg["split"]["chunk_bytes"]
        expected = header + b"".join(line for line in source.splitlines(keepends=True)[1:]
                                     if line[1:11].decode() in entry["groups"])
        assert data == expected
    public = json.dumps(manifest)
    assert "test-000.csv" not in public
    assert "test" not in manifest["development"]
    assert prepare(root, cfg) == manifest


def test_cached_source_tampering_is_rejected(experiment):
    root, cfg, _ = experiment
    (root / cfg["dataset"]["cache"]).write_bytes(b"modified archive")
    with pytest.raises(ValueError, match="checksum"):
        prepare(root, cfg)


def test_development_loader_rejects_test_and_modified_samples(experiment):
    root, cfg, _ = experiment
    prepare(root, cfg)
    with pytest.raises(ValueError, match="train or validation only"):
        development_files(root, cfg, "test")
    sample = development_files(root, cfg, "train")[0]
    sample.write_bytes(sample.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="checksum"):
        development_files(root, cfg, "train")


def test_manifest_path_cannot_escape_into_sealed_directory(experiment):
    root, cfg, _ = experiment
    manifest = prepare(root, cfg)
    manifest["development"]["train"][0]["path"] = cfg["dataset"]["sealed_dir"] + "/test-000.csv"
    (root / cfg["dataset"]["manifest"]).write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="escapes"):
        development_files(root, cfg, "train")


def test_profile_reports_evidence_without_normalizing_input(experiment):
    root, cfg, _ = experiment
    prepare(root, cfg)
    sample = development_files(root, cfg, "train")[0]
    before = sample.read_bytes()
    report = profile(root, cfg)
    assert report["split"] == "train"
    assert ["a", "b"] in report["identical_lexeme_columns"]
    assert report["line_endings"]["crlf"] > 0
    assert sample.read_bytes() == before


@pytest.mark.parametrize("data", [b'a,b\n1.00,-0\n2e0,"x"\n', b'"left,name",b\r\n"a,b",1\r\n"c,d",2', b'a\n'])
def test_reference_roundtrip_preserves_lexemes_and_line_endings(data):
    assert restore_tokens(transpose_tokens(data)) == data


def test_reference_rejects_unsupported_layout():
    with pytest.raises(ValueError, match="rectangular"):
        transpose_tokens(b"a,b\n1\n")


def test_sample_trims_partial_record(tmp_path):
    path = tmp_path / "sample.csv"
    path.write_bytes(b"a,b\n1,2\n3,4\n")
    assert bounded_sample(path, 10) == b"a,b\n1,2\n"


def test_sample_preserves_complete_final_record_without_newline(tmp_path):
    path = tmp_path / "sample.csv"
    data = b"a,b\n1.00,-0"
    path.write_bytes(data)
    assert bounded_sample(path, len(data)) == data


def test_measure_enforces_input_and_wall_limits(experiment):
    _, cfg, _ = experiment
    cfg["lab"]["max_input_bytes"] = 16
    with pytest.raises(ValueError, match="cap"):
        measure(["gzip", "-c"], b"x" * 17, cfg)
    cfg["limits"]["baseline_timeout_seconds"] = 1
    with pytest.raises(ValueError, match="wall-time"):
        measure([sys.executable, "-c", "import time; time.sleep(10)"], b"", cfg)


def test_worker_environment_omits_credentials(monkeypatch):
    monkeypatch.setenv("META_API_KEY", "test-secret")
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    assert "META_API_KEY" not in worker_environment()
    assert "GITHUB_TOKEN" not in worker_environment()


def test_api_failure_does_not_serialize_secret(experiment, monkeypatch):
    from urllib.error import HTTPError
    import algomini.environment as environment

    root, cfg, _ = experiment
    secret = "deliberately-private-test-key"
    monkeypatch.setenv("META_API_KEY", secret)

    class FailedRequest:
        def open(self, request, timeout):
            raise HTTPError(request.full_url, 401, secret, {"Authorization": secret}, None)

    monkeypatch.setattr(environment, "build_opener", lambda *args: FailedRequest())
    result = check_api(root, cfg)
    assert result["ok"] is False and result["http_status"] == 401
    assert secret not in json.dumps(result)
