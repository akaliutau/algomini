import json
from pathlib import Path
import random
import shutil

import pytest

from algomini import archive, codecs
from algomini.common import digest, load_config, write_json
from algomini.cloud import contracts, coordinator, native, research


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


def test_real_reuse_campaign_keeps_test_closed_until_claim_and_never_repeats(monkeypatch, tmp_path):
    from test_cloud_contracts import snapshot
    request = snapshot(monkeypatch)
    request["mode"] = "reuse"
    rng = random.Random(71)
    train = (" ".join(rng.choice(["alpha", "delta", "record", "value", "time", "sample"]) for _ in range(2800)) + "\n").encode()
    test = (" ".join(rng.choice(["alpha", "delta", "record", "value", "time", "sample"]) for _ in range(2700)) + "\n").encode()
    request["dataset"]["train"].update(bytes=len(train), sha256=digest(train))
    request["dataset"]["test"].update(bytes=len(test), sha256=digest(test))
    reads = []
    allow_final = False
    def fetch(self, rules):
        reads.append(self.url)
        if self.url.endswith("test.txt"):
            assert allow_final, "Final bytes opened before frozen claim"
            return test
        return train
    monkeypatch.setattr(contracts.DatasetFile, "fetch", fetch)
    monkeypatch.setattr(research.coder, "generate", lambda *_a, **_k: pytest.fail("Reuse mode attempted generation"))
    result = research.search(ROOT, tmp_path, request)
    assert result["charged_usd"] == 0 and len(reads) == 1
    assert result["origin"].startswith("reused")
    assert not (tmp_path / "final.json").exists()
    with pytest.raises(ValueError, match="claim"):
        research.verify(ROOT, tmp_path, request, {"request_id": request["id"], "freeze_id": "wrong"})
    assert len(reads) == 1
    _, freeze_id = research.check_freeze(ROOT, tmp_path, request)
    allow_final = True
    receipt = {"request_id": request["id"], "freeze_id": freeze_id, "test_consumed_at": "now", "frozen_commit": "e" * 40, "test_previously_used": False}
    final = research.verify(ROOT, tmp_path, request, receipt)
    assert final["candidate"]["roundtrip"] and final["console"]["original_deleted"]
    assert final["size_first_win"] == (final["score"]["archive_gain"] > 0 and final["eligible"])
    assert len(reads) == 2
    with pytest.raises(ValueError, match="consumed"):
        research.verify(ROOT, tmp_path, request, receipt)
    assert len(reads) == 2
    assert "Previously used test hash: **False**" in coordinator.report(request, result, final)
