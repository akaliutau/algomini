import hashlib
from pathlib import Path
from zipfile import ZipFile

import pytest

from algomini import text_e2e
from algomini.common import digest, load_config


ROOT = Path(__file__).parents[1]


def test_streaming_prefix_checks_the_complete_member_before_persisting(tmp_path):
    data = b'<text title="sample">Repeated words &amp; exact bytes.</text>\n' * 100
    source = tmp_path / "source.zip"
    with ZipFile(source, "w") as archive:
        archive.writestr("fixture", data)
    spec = {"member": {"name": "fixture", "bytes": len(data), "md5": hashlib.md5(data).hexdigest(),
                       "sha1": hashlib.sha1(data).hexdigest()},
            "prefix": {"offset": 0, "bytes": 1000, "sha256": digest(data[:1000])}}
    output = tmp_path / "prefix"
    assert text_e2e.extract_prefix(source, spec, output).read_bytes() == data[:1000]
    output.unlink()
    spec["member"]["sha1"] = "0" * 40
    with pytest.raises(ValueError, match="checksum"):
        text_e2e.extract_prefix(source, spec, output)
    assert not output.exists()
    spec["prefix"]["bytes"] = 2 * 1024**2 + 1
    with pytest.raises(ValueError, match="bound"):
        text_e2e.extract_prefix(source, spec, output)


def test_tiny_native_integration_and_replay_without_new_work(tmp_path, monkeypatch):
    root, cfg = load_config(ROOT / "run.toml")
    sample = tmp_path / "synthetic.xml"
    data = '<page><text>quoted "value", repeated words, café &amp; entities</text></page>\r\n'.encode() * 20
    sample.write_bytes(data)
    fixture = {"test_fixture": True, "prefix": {"bytes": len(data), "sha256": digest(data)}}
    monkeypatch.setattr(text_e2e, "prepare_fixture", lambda root: (sample, fixture))
    output = tmp_path / "reports/integration"
    first = text_e2e.run(root, cfg, output=output)
    assert first["ok"] and first["input_bytes"] == len(data)
    assert first["installation"]["original_removed_before_decode"]
    assert len(first["baselines"]) == 4 and first["candidate_training_calls"] == 0
    assert len(first["campaigns"]) == 2
    for name in ("prepare_fixture", "clean_roundtrip", "compare_baselines"):
        monkeypatch.setattr(text_e2e, name, lambda *args: pytest.fail("cached replay repeated work"))
    assert text_e2e.run(root, cfg, output=output) == first
    (output / "campaigns/wikipedia.json").write_text("changed")
    with pytest.raises(ValueError, match="artifact checksum"):
        text_e2e.run(root, cfg, output=output)
