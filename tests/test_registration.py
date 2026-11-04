import json

import pytest

from tools.register_codec import register


def test_source_and_model_versions_are_immutable_and_alias_changes_are_explicit(tmp_path):
    registry = tmp_path / "release/registry.json"
    registry.parent.mkdir()
    registry.write_text(json.dumps({"schema_version": 1, "aliases": {}, "codecs": [], "requests": []}))
    source = tmp_path / "codec.rs"
    source.write_text("pub struct Candidate;")
    model = tmp_path / "model.bin"
    model.write_bytes(b"one")
    first = register(tmp_path, "custom", source, model)
    with pytest.raises(ValueError, match="Alias exists"):
        register(tmp_path, "custom", source, model)
    model.write_bytes(b"two")
    second = register(tmp_path, "custom", source, model, replace=True)
    assert first != second
    result = json.loads(registry.read_text())
    assert len(result["codecs"]) == 2 and result["aliases"]["custom"] == second
    assert (tmp_path / "plugins" / first / "model.bin").read_bytes() == b"one"


def test_registration_refuses_source_links_and_invalid_names(tmp_path):
    registry = tmp_path / "release/registry.json"
    registry.parent.mkdir()
    registry.write_text(json.dumps({"schema_version": 1, "aliases": {}, "codecs": [], "requests": []}))
    source = tmp_path / "codec.rs"
    source.write_text("pub struct Candidate;")
    link = tmp_path / "link.rs"
    link.symlink_to(source)
    with pytest.raises(ValueError, match="nonsymlink"):
        register(tmp_path, "custom", link)
    with pytest.raises(ValueError, match="lowercase"):
        register(tmp_path, "../escape", source)
