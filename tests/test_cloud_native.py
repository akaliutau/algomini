import json
import io
from pathlib import Path
import random
import shutil
import zipfile

import pytest

from algomini import archive
import legacy_worker as codecs
from algomini.common import digest, load_config, sha256, write_json
from algomini.cloud import contracts, coordinator, native, releasing, research


ROOT = Path(__file__).parents[1]


@pytest.fixture(scope="module")
def release_client(tmp_path_factory):
    workspace = tmp_path_factory.mktemp("cloud-native")
    registry = json.loads((ROOT / "release/registry.json").read_text())
    binary, record = native.build(ROOT, workspace, registry)
    return workspace, registry, binary, record


def test_rust_and_original_python_collections_interoperate(release_client):
    _, registry, binary, _ = release_client
    _, cfg = load_config(ROOT / "run.toml")
    item = registry["codecs"][0]
    old, _ = codecs.build(ROOT, cfg, ROOT / item["source"])
    data = b"<record>the quick brown fox jumps over the lazy dog</record>\r\n" * 60
    original, _ = codecs.invoke(ROOT, cfg, old, 6, data, source_sha256=item["source_sha256"])
    restored, _ = native.invoke(binary, item["id"], cfg, 2, original, source_sha256=item["source_sha256"])
    assert restored == data
    current, _ = native.invoke(binary, item["id"], cfg, 6, data, source_sha256=item["source_sha256"])
    assert codecs.invoke(ROOT, cfg, old, 2, current, source_sha256=item["source_sha256"])[0] == data
    packed = archive.pack(item["id"], [("sample.bin", current)])
    assert native.console_roundtrip(binary, cfg, data, item["id"], packed)["original_deleted"]


def test_modified_source_and_build_cache_are_rejected(release_client, tmp_path):
    _, registry, _, _ = release_client
    broken = json.loads(json.dumps(registry))
    broken["codecs"][0]["source_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="checksum"):
        native.build(ROOT, tmp_path, broken)


def test_release_binary_rebuild_is_reproducible(release_client):
    workspace, registry, binary, original = release_client
    binary.unlink()
    (binary.parent / "build.json").unlink()
    shutil.rmtree(workspace / "target")
    _, rebuilt = native.build(ROOT, workspace, registry)
    assert rebuilt["binary_sha256"] == original["binary_sha256"]


def test_generated_codec_and_nonempty_model_are_bundled_and_counted(tmp_path):
    _, cfg = load_config(ROOT / "run.toml")
    base = json.loads((ROOT / "release/registry.json").read_text())
    for name, data in native.assets(ROOT, base).items():
        path = tmp_path / name;path.parent.mkdir(parents=True, exist_ok=True);path.write_bytes(data)
    source = tmp_path / "generated.rs"
    source.write_text('''use crate::{Codec, CodecError};
pub struct Candidate;
impl Codec for Candidate {
 fn train(_: &[&[u8]]) -> Result<Vec<u8>, CodecError> { Ok(vec![71]) }
 fn encode(input: &[u8], model: &[u8]) -> Result<Vec<u8>, CodecError> {
  if model != [71] { return Err("wrong model".into()); }
  crate::toolkit::zstd_compress(input, 19)
 }
 fn decode(input: &[u8], model: &[u8], _: usize) -> Result<Vec<u8>, CodecError> {
  if model != [71] { return Err("wrong model".into()); }
  crate::toolkit::zstd_decompress(input)
 }
}''')
    item = research.persist_asset(tmp_path, source, b"G", base)
    registry = research.registry_for(base, item, "learned-model")
    binary, _ = native.build(ROOT, tmp_path, registry, asset_root=tmp_path)
    model, _ = native.invoke(binary, item["id"], cfg, 3, b"\3\0\0\0abc")
    assert model == b"G"
    data = b"new generated codec with counted model\n" * 100
    checks = native.correctness(binary, item, cfg, data, model)
    assert checks["passed"]
    encoded, _ = native.invoke(binary, item["id"], cfg, 6, data, model=model, source_sha256=item["source_sha256"])
    assert encoded[4] == 2
    packed = archive.pack(item["id"], [("sample.bin", encoded)], model)
    assert archive.unpack(packed)[1] == model
    assert native.console_roundtrip(binary, cfg, data, item["id"], packed)["exact"]
    large = (data * (archive.CHUNK_BYTES // len(data) + 1)) + b"last bytes"
    packed, _ = native.file_cli(binary, cfg, large, identifier=item["id"])
    info = archive.unpack_chunked(packed)
    assert info["model"] == b"G" and all(frame[4] == 2 for frame in info["frames"])
    assert archive.pack_chunked(item["id"], "sample.bin", info["frames"], large, b"G") == packed
    assert native.file_cli(binary, cfg, packed)[0] == large


@pytest.mark.parametrize("zip_input", [False, True, "text"], ids=["manifest", "zip", "large-text-zip"])
def test_real_reuse_campaign_keeps_test_closed_until_claim_and_never_repeats(monkeypatch, tmp_path, zip_input):
    from test_cloud_contracts import snapshot
    request = snapshot(monkeypatch)
    request["mode"] = "reuse"
    text_case = zip_input == "text"
    request["dataset"]["family"] = "text" if text_case else "json"
    train = (ROOT / "datasets/sample" / ("Shakespeare-1.txt" if text_case else "train-1mb.json")).read_bytes()
    test = (ROOT / "datasets/sample" / ("Dumas-1.txt" if text_case else "test-512kb.json")).read_bytes()
    request["dataset"]["train"].update(bytes=len(train), sha256=digest(train))
    request["dataset"]["test"].update(bytes=len(test), sha256=digest(test))
    if zip_input:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            extension = "txt" if text_case else "json"
            archive.writestr("development." + extension, train)
            archive.writestr("final." + extension, test)
        monkeypatch.setattr(contracts, "download", lambda *_: buffer.getvalue())
        url = request["dataset_manifest_url"].replace("manifest.json", "dataset.zip")
        issue = {"number": request["issue"], "body": f"### Plugin name\n\njson-demo\n\n### Dataset archive URL\n\n{url}"}
        request = contracts.snapshot(ROOT, issue, request["repository"], request["base_commit"], request["image"], staging=tmp_path / "claim")
        request["mode"] = "reuse"
        (tmp_path / "dataset").mkdir()
        shutil.copyfile(tmp_path / "claim/dataset/train.bin", tmp_path / "dataset/train.bin")
        assert not (tmp_path / "dataset/test.bin").exists()
    reads = []
    allow_final = False
    original_fetch = contracts.DatasetFile.fetch
    def fetch(self, rules, **kwargs):
        reads.append(self.url)
        if self.sha256 == digest(test):
            assert allow_final, "Final bytes opened before frozen claim"
            if zip_input:
                return original_fetch(self, rules, **kwargs)
            return test
        if zip_input:
            return original_fetch(self, rules, **kwargs)
        return train
    monkeypatch.setattr(contracts.DatasetFile, "fetch", fetch)
    monkeypatch.setattr(research.coder, "generate", lambda *_a, **_k: pytest.fail("Reuse mode attempted generation"))
    result = research.search(ROOT, tmp_path, request)
    assert result["charged_usd"] == 0 and len(reads) == 1
    assert result["origin"].startswith("reused")
    if text_case:
        assert result["sampling"]["research_bytes"] == 2 * 1024**2
        assert result["sampling"]["development_bytes"] == len(train)
        assert (tmp_path / "campaign/sample.bin").read_bytes() == train[:2*1024**2]
        assert all(trial["result"]["input_bytes"] <= 2*1024**2 for trial in result["trials"])
        assert result["development"]["input_bytes"] == len(train)
        assert result["development"]["candidate"]["chunk_count"] == 3
    else:
        assert result["sampling"]["shared_record_groups"] == 0
    assert result["development_gate"]["passed"]
    assert not (tmp_path / "final.json").exists()
    with pytest.raises(ValueError, match="claim"):
        research.verify(ROOT, tmp_path, request, {"request_id": request["id"], "freeze_id": "wrong"})
    assert len(reads) == 1
    _, freeze_id = research.check_freeze(ROOT, tmp_path, request)
    allow_final = True
    if zip_input:
        # The verify job alone downloads this artifact after the durable claim.
        shutil.copyfile(tmp_path / "claim/dataset/test.bin", tmp_path / "dataset/test.bin")
    receipt = {"request_id": request["id"], "freeze_id": freeze_id, "test_consumed_at": "now", "frozen_commit": "e" * 40, "test_previously_used": True}
    final = research.verify(ROOT, tmp_path, request, receipt)
    assert final["candidate"]["roundtrip"] and final["console"]["original_deleted"]
    assert final["size_first_win"] == (final["score"]["archive_gain"] > 0 and final["eligible"])
    assert len(reads) == 2
    with pytest.raises(ValueError, match="consumed"):
        research.verify(ROOT, tmp_path, request, receipt)
    assert len(reads) == 2
    assert "| Previously used test hash | True |" in coordinator.report(request, result, final)
    if text_case:
        assert final["input_bytes"] == len(test) and final["candidate"]["chunk_count"] == 2
    else:
        assert not final["record_overlap"]["no_exact_record_overlap"]
    # Rebuild the qualified source snapshot without opening final data again.
    allow_final = False
    merged, output = tmp_path / "merged", tmp_path / "release-output"
    files = coordinator.review_files(ROOT, tmp_path, request, final=True)
    for name, data in native.assets(ROOT, json.loads((ROOT / "release/registry.json").read_text())).items():
        files.setdefault(name, data)
    prefix = f"reports/campaigns/{request['id']}/"
    files[prefix + "receipt.json"] = json.dumps(receipt).encode()
    files[prefix + "report.md"] = coordinator.report(request, result, final).encode()
    registry = json.loads(files["release/registry.json"])
    registry["requests"] = [{"id": request["id"], "issue": request["issue"], "freeze_id": freeze_id,
        "image": request["image"], "selected": result["selected"]["id"]}]
    files["release/registry.json"] = json.dumps(registry).encode()
    # main's later configuration must not replace the qualified image's config.
    files["run.toml"] = b"deliberately incompatible later main configuration"
    for name, data in files.items():
        target = merged / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    monkeypatch.setenv("ALGOMINI_BUILD_WORKSPACE", str(tmp_path / "release-build"))
    releasing.build_release(merged, output)
    manifest = json.loads((output / request["id"] / "manifest.json").read_text())
    assert manifest["binary_sha256"] == sha256(tmp_path / "algomini")
    assert manifest["console"]["exact"] and len(reads) == (2 if zip_input else 3)


def test_general_codec_reconstructs_maximum_binary_input_and_rejects_oversize(release_client):
    _, registry, binary, _ = release_client
    _, cfg = load_config(ROOT / "run.toml")
    item = next(row for row in registry["codecs"] if row["id"] == registry["aliases"]["general"])
    data = random.Random(73).randbytes(2 * 1024**2)
    encoded, _ = native.invoke(binary, item["id"], cfg, 6, data, source_sha256=item["source_sha256"])
    assert native.invoke(binary, item["id"], cfg, 2, encoded, source_sha256=item["source_sha256"])[0] == data
    packed = archive.pack(item["id"], [("sample.bin", encoded)])
    assert native.console_roundtrip(binary, cfg, data, item["id"], packed)["exact"]
    with pytest.raises(ValueError, match="limits"):
        native.invoke(binary, item["id"], cfg, 4, data + b"x")
    prefix = b'{"x":"'
    malformed = prefix + b'\\"' * ((2 * 1024**2 - len(prefix)) // 2)
    encoded, _ = native.invoke(binary, item["id"], cfg, 6, malformed, source_sha256=item["source_sha256"])
    assert native.invoke(binary, item["id"], cfg, 2, encoded, source_sha256=item["source_sha256"])[0] == malformed


def test_empty_development_cannot_spend_or_consume_a_final_file(monkeypatch, tmp_path):
    from test_cloud_contracts import snapshot
    request = snapshot(monkeypatch)
    request["dataset"]["family"] = "binary"
    request["execution"] = {"run_id": "17", "run_attempt": 1}
    request["dataset"]["train"].update(bytes=0, sha256=digest(b""))
    request["dataset"]["test"].update(bytes=1, sha256=digest(b"x"))
    monkeypatch.setenv("ALGOMINI_RUN_ID", "17")
    monkeypatch.setenv("ALGOMINI_RUN_ATTEMPT", "1")
    def fetch(self, _rules):
        assert self.url.endswith("train.txt"), "Final input was opened"
        return b""
    monkeypatch.setattr(contracts.DatasetFile, "fetch", fetch)
    monkeypatch.setattr(research, "check_api", lambda *_: pytest.fail("An impossible framing gain contacted the provider"))
    monkeypatch.setattr(research.coder, "generate", lambda *_a, **_kw: pytest.fail("Empty input caused inference"))
    result = research.search(ROOT, tmp_path, request)
    assert result["generation_skipped_for_framing_floor"] and result["charged_usd"] == 0
    assert not result["development_gate"]["passed"] and not (tmp_path / "final.json").exists()
