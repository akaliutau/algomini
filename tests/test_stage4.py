import copy
from pathlib import Path

import pytest

from algomini import codecs, refinement, selection
from algomini.common import digest, load_config, sha256, write_json


ROOT = Path(__file__).parents[1]


@pytest.fixture
def cfg():
    return copy.deepcopy(load_config(ROOT / "run.toml")[1])


def test_controlled_variants_preserve_decoder_and_have_equal_name_cost():
    parent = (ROOT / "plugins/stage3-byteplanes-r1/codec.rs").read_bytes()
    decoder = parent.decode().split("    fn decode(", 1)[1]
    checksums = []
    for mode in ("raw", "column", "plane", "adaptive"):
        variant = refinement.variant_source(parent, mode, 19)
        assert variant.decode().split("    fn decode(", 1)[1] == decoder
        checksums.append(digest(variant))
        assert len("s4-" + digest(variant)[:17]) == len("stage3-byteplanes-r1")
    assert len(set(checksums)) == 4
    level22 = refinement.variant_source(parent, "adaptive", 22).decode()
    assert "zstd_compress(input, 19)" not in level22 and "zstd_compress(&t1, 22)" in level22
    with pytest.raises(ValueError, match="reviewed parent"):
        refinement.variant_source(parent + b"changed", "adaptive", 19)


def test_failure_minimizer_preserves_predicate_and_bounded_work():
    predicate = lambda data: b"BAD" in data
    minimal, checks = refinement.minimize_failure(b"prefix-prefix-BAD-suffix-suffix", predicate)
    assert minimal == b"BAD" and checks["predicate_checks"] <= 32
    bounded, checks = refinement.minimize_failure(b"x" * 1024 + b"BAD" + b"y" * 1024, predicate, maximum_checks=5)
    assert predicate(bounded) and checks["predicate_checks"] <= 5
    with pytest.raises(ValueError, match="must fail"):
        refinement.minimize_failure(b"good", predicate)


def test_real_broken_codec_reduces_to_one_byte(tmp_path, cfg):
    binary, _ = codecs.build(ROOT, cfg, ROOT / "rust/fixtures/broken.rs")
    result = refinement.counterexample(ROOT, cfg, binary, tmp_path, [b"a,b\r\n1.00,-0\r\n"], control=True)
    assert result["control_fixture"] and result["remaining_bytes"] == 1
    minimal = (tmp_path / "counterexample.bin").read_bytes()
    assert refinement.direct_failure(ROOT, cfg, binary, minimal)
    assert not refinement.direct_failure(ROOT, cfg, binary, b"")


def measured(mode, sizes, *, level=19):
    return {"id": mode, "source_sha256": mode, "outcome": "measured",
            "definition": {"mode": mode, "kind": "deterministic", "zstd_level": level},
            "measurements": {"archive_bytes": sum(sizes) + 100,
                             "files": [{"label": str(index), "block_bytes": value} for index, value in enumerate(sizes)]}}


def test_ablation_checks_real_per_block_choice_not_aggregate_oracle():
    records = [measured("raw", [100, 100]), measured("column", [40, 60]),
               measured("plane", [50, 50]), measured("adaptive", [40, 50])]
    result = refinement.ablations(records, 19)
    assert result["plane_contribution_bytes"] == 10
    assert result["representation_saving_vs_raw_bytes"] == 110
    records[-1]["measurements"]["files"][1]["block_bytes"] = 51
    with pytest.raises(ValueError, match="controlled per-block minimum"):
        refinement.ablations(records, 19)


def test_equal_archive_ties_prefer_smaller_decoder_without_changing_primary_score():
    records = [{"id": "large", "source_sha256": "a", "outcome": "measured",
                "measurements": {"archive_bytes": 100, "deployment_saving_bytes": -1000, "gates": {"correct": True}}},
               {"id": "small", "source_sha256": "z", "outcome": "measured",
                "measurements": {"archive_bytes": 100, "deployment_saving_bytes": -500, "gates": {"correct": True}}}]
    assert refinement.eligible(records)[0]["id"] == "small"
    records[0]["measurements"]["archive_bytes"] = 99
    assert refinement.eligible(records)[0]["id"] == "large"


def test_frozen_selection_never_generates_when_request_cache_is_missing(tmp_path, cfg, monkeypatch):
    marker = tmp_path / "repro/candidate-freeze.json"
    marker.parent.mkdir()
    marker.write_text("frozen")
    monkeypatch.setattr(refinement.coder, "generate", lambda *args, **kwargs: pytest.fail("frozen selection launched generation"))
    with pytest.raises(ValueError, match="candidate is frozen"):
        refinement.evolve(tmp_path, cfg, refinement.load_policy(ROOT), {}, "column-local-choice", "missing", {})


@pytest.fixture
def selected_fixture(tmp_path, cfg, monkeypatch):
    directory = tmp_path / selection.ARTIFACTS
    directory.mkdir(parents=True)
    source, model = directory / "codec.rs", directory / "model.bin"
    source.write_bytes(b"frozen source")
    model.write_bytes(b"frozen model")
    record = {"evaluator_freeze_id": "evaluator", "verifier_sha256": sha256(selection.__file__), "candidate_id": "candidate",
              "files": {path.relative_to(tmp_path).as_posix(): sha256(path) for path in (source, model)},
              "source": {"path": source.relative_to(tmp_path).as_posix(), "sha256": sha256(source)},
              "model": {"path": model.relative_to(tmp_path).as_posix(), "sha256": sha256(model), "bytes": model.stat().st_size},
              "compiler": {"build_fingerprint": "build", "binary_sha256": "binary"}}
    write_json(tmp_path / selection.RECORD, record)
    (tmp_path / selection.ANCHOR).write_text(sha256(tmp_path / selection.RECORD) + "\n")
    monkeypatch.setattr(selection.freeze, "verify", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(selection.registry, "resolve", lambda *args: {"source_sha256": record["source"]["sha256"]})
    return tmp_path, cfg, record


def test_selected_source_model_parameters_and_anchor_cannot_change(selected_fixture):
    root, cfg, record = selected_fixture
    assert selection.verify(root, cfg)["ok"]
    with pytest.raises(ValueError, match="already frozen"):
        selection.create(root, cfg, {}, {})
    with pytest.raises(ValueError, match="checksum mismatch"):
        selection.verify(root, cfg, expected_id="wrong")
    (root / record["model"]["path"]).write_bytes(b"different model")
    with pytest.raises(ValueError, match="artifact checksum"):
        selection.verify(root, cfg)


def test_selected_rebuild_must_match_measured_binary(selected_fixture, monkeypatch):
    root, cfg, _ = selected_fixture
    monkeypatch.setattr(selection.codecs, "build", lambda *args: (None, {"fingerprint": "build", "binary_sha256": "changed"}))
    with pytest.raises(ValueError, match="differs from its freeze"):
        selection.verify(root, cfg, rebuild=True)


def test_selected_record_and_path_escape_are_rejected(selected_fixture):
    root, cfg, record = selected_fixture
    record["files"] = {"../outside.rs": "fake"}
    write_json(root / selection.RECORD, record)
    (root / selection.ANCHOR).write_text(sha256(root / selection.RECORD) + "\n")
    with pytest.raises(ValueError, match="escapes"):
        selection.verify(root, cfg)
