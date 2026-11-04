import io
import json
import stat
import zipfile

import pytest
import yaml

from algomini.cloud import contracts, coordinator, datasets
from algomini.cloud.__main__ import export
from algomini.common import digest, write_json
from test_cloud_contracts import IMAGE, ROOT, SHA, MemoryGit


URL = f"https://raw.githubusercontent.com/example/data/{SHA}/samples.zip"
RULES = contracts.policy(ROOT)


def zipped(files):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files:
            archive.writestr(name, data)
    return stream.getvalue()


def test_zip_intake_stages_bigger_file_for_training_and_builds_manifest(monkeypatch, tmp_path):
    raw = zipped([("nested/small.json", b'{"a":1}'), ("nested/large.json", b'[{"a":2},{"a":3}]'), ("README.md", b"Dataset notes")])
    monkeypatch.setattr(contracts, "download", lambda *_: raw)
    issue = {"number": 7, "body": f"### Plugin name\n\njson-demo\n\n### Dataset archive URL\n\n{URL}"}
    request = contracts.snapshot(ROOT, issue, "akaliutau/algomini", SHA, IMAGE, staging=tmp_path)
    assert contracts.validate_snapshot(request, ROOT) == request
    assert request["dataset"]["family"] == "json"
    assert request["dataset"]["train"]["archive"]["members"][0]["name"] == "nested/large.json"
    assert (tmp_path / "dataset/train.bin").read_bytes() == b'[{"a":2},{"a":3}]'
    assert (tmp_path / "dataset/test.bin").read_bytes() == b'{"a":1}'
    assert digest((tmp_path / "dataset-manifest.json").read_bytes()) == request["dataset_manifest_sha256"]
    assert json.loads((tmp_path / "dataset-manifest.json").read_text()) == request["dataset"]
    # Artifact export never carries input bytes; only the claim job stages them.
    write_json(tmp_path / "request.json", request)
    export(tmp_path, tmp_path / "export")
    assert not (tmp_path / "export/dataset").exists()


def test_many_files_have_a_stable_seventy_thirty_split_and_exact_assembly():
    files = [(f"part-{n}.txt", f"record {n}\r\n".encode()) for n in range(10)]
    value, payloads = datasets.prepare(zipped(files), URL, RULES)
    reversed_value, _ = datasets.prepare(zipped(list(reversed(files))), URL, RULES)
    assert value["family"] == "text"
    assert len(value["train"]["archive"]["members"]) == 7
    assert len(value["test"]["archive"]["members"]) == 3
    for part in ("train", "test"):
        rows = value[part]["archive"]["members"]
        assert rows == reversed_value[part]["archive"]["members"]
        assert payloads[part] == b"".join(dict(files)[row["name"]] for row in rows)
        assert digest(payloads[part]) == value[part]["sha256"]
    assert contracts.manifest(json.dumps(value), RULES) == value


def test_identical_members_stay_together_and_mixed_extensions_use_binary():
    value, _ = datasets.prepare(zipped([("a.json", b"same"), ("b.json", b"same"), ("c.bin", b"different")]), URL, RULES)
    assert value["family"] == "binary"
    hashes = [{r["sha256"] for r in value[part]["archive"]["members"]} for part in ("train", "test")]
    assert not hashes[0] & hashes[1]
    with pytest.raises(ValueError, match="distinct"):
        datasets.prepare(zipped([("a", b"same"), ("b", b"same")]), URL, RULES)


@pytest.mark.parametrize("name", ["../escape", "/absolute", "folder/../escape", "a\\b", "a//b", "C:/x", "a\x00b", "a\nb", ".git/config"])
def test_zip_rejects_unsafe_paths_before_staging(name):
    raw = zipped([(name.replace("\x00", "X"), b"bad"), ("okay", b"okay")])
    if "\x00" in name:
        raw = raw.replace(b"aXb", b"a\x00b")
    with pytest.raises(ValueError):
        datasets.prepare(raw, URL, RULES)


def test_zip_rejects_links_and_duplicate_paths():
    for kind in (stat.S_IFLNK, stat.S_IFDIR):
        item = zipfile.ZipInfo("link")
        item.create_system = 3
        item.external_attr = (kind | 0o777) << 16
        with pytest.raises(ValueError, match="links"):
            datasets.prepare(zipped([(item, b"../../outside"), ("data", b"okay")]), URL, RULES)
    with pytest.warns(UserWarning, match="Duplicate name"):
        raw = zipped([("same", b"a"), ("same", b"b")])
    with pytest.raises(ValueError, match="duplicate"):
        datasets.prepare(raw, URL, RULES)


def test_corrupt_deflate_has_a_readable_intake_error():
    raw = bytearray(zipped([("first.txt", b"compress this data" * 20), ("second.txt", b"second file")]))
    raw[30 + len("first.txt")] = 0xff
    with pytest.raises(ValueError, match="Cannot read the ZIP"):
        datasets.prepare(bytes(raw), URL, RULES)


def test_archive_descriptors_reject_overlapping_members_and_wrong_member_sizes():
    value, _ = datasets.prepare(zipped([("a", b"longer"), ("b", b"short")]), URL, RULES)
    original = value["test"]["archive"]["members"][0]["name"]
    value["test"]["archive"]["members"][0]["name"] = value["train"]["archive"]["members"][0]["name"]
    with pytest.raises(ValueError, match="disjoint"):
        contracts.manifest(json.dumps(value), RULES)
    value["test"]["archive"]["members"][0]["name"] = original
    value["test"]["archive"]["members"][0]["bytes"] += 1
    with pytest.raises(ValueError, match="sizes differ"):
        contracts.manifest(json.dumps(value), RULES)


@pytest.mark.parametrize("files,reason", [
    ([("only", b"one")], "2–64"),
    ([(str(n), bytes([n])) for n in range(65)], "2–64"),
    ([("large", b"x" * (2097152 + 1)), ("small", b"y")], "2 MiB per file"),
    ([(str(n), bytes([n]) * 330000) for n in range(10)], "partition exceeds"),
])
def test_zip_bounds_expansion_file_count_and_each_partition(files, reason):
    with pytest.raises(ValueError, match=reason):
        datasets.prepare(zipped(files), URL, RULES)


def test_staged_partitions_are_required_and_hash_checked_without_network(monkeypatch, tmp_path):
    value, payloads = datasets.prepare(zipped([("train.txt", b"large training file"), ("test.txt", b"small")]), URL, RULES)
    request = {"dataset": value, "policy": RULES}
    monkeypatch.setattr(contracts, "download", lambda *_: pytest.fail("Research tried fetching the ZIP"))
    with pytest.raises(ValueError, match="separately staged"):
        contracts.fetch_dataset(request, "test", tmp_path)
    path = tmp_path / "dataset/train.bin"
    path.parent.mkdir()
    path.write_bytes(payloads["train"])
    assert contracts.fetch_dataset(request, "train", tmp_path) == payloads["train"]
    path.write_bytes(b"changed")
    with pytest.raises(ValueError, match="SHA-256"):
        contracts.fetch_dataset(request, "train", tmp_path)


def test_release_reconstruction_never_opens_final_members(monkeypatch):
    raw = zipped([("train.txt", b"larger training file"), ("test.txt", b"test")])
    value, payloads = datasets.prepare(raw, URL, RULES)
    monkeypatch.setattr(contracts, "download", lambda *_: raw)
    original = zipfile.ZipFile.open
    opened = []
    def tracked(self, name, *args, **kwargs):
        opened.append(name)
        return original(self, name, *args, **kwargs)
    monkeypatch.setattr(zipfile.ZipFile, "open", tracked)
    assert datasets.refetch_development(contracts.DatasetFile.parse(value["train"], RULES), RULES) == payloads["train"]
    assert opened == ["train.txt"]


def test_workflow_keeps_raw_final_dataset_out_of_research_artifacts():
    jobs = yaml.safe_load((ROOT / ".github/workflows/codec-request.yml").read_text())["jobs"]
    uploads = {row.get("with", {}).get("name"): row for row in jobs["claim"]["steps"]}
    paths = uploads["claimed-request"]["with"]["path"]
    assert "dataset/train.bin" in paths and "test.bin" not in paths
    assert uploads["final-dataset"]["with"]["path"].endswith("dataset/test.bin")
    assert all(row.get("with", {}).get("name") != "final-dataset" for row in jobs["research"]["steps"])
    assert "needs.freeze.outputs.verify == 'true'" in jobs["verify"]["if"]
    assert any(row.get("with", {}).get("name") == "final-dataset" for row in jobs["verify"]["steps"])


def test_invalid_zip_reports_an_actionable_error_without_reserving_money(monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_SHA", SHA)
    monkeypatch.setattr(contracts, "download", lambda *_: zipped([("only.txt", b"one file")]))
    issue = {"number": 7, "body": f"### Plugin name\n\njson-demo\n\n### Dataset archive URL\n\n{URL}"}
    gh = MemoryGit()
    coordinator.claim(ROOT, {"issue": issue, "mode": "reuse"}, IMAGE, tmp_path / "request.json", gh)
    assert not gh.branches and not (tmp_path / "request.json").exists()
    assert "before inference or a spending reservation" in gh.comments[0][1]
    assert "2–64 data files" in gh.comments[0][1]
