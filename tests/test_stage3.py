import copy
import json
from pathlib import Path

import pytest

from algomini import coder, memory, research
from algomini.common import digest, load_config, sha256, write_json
import algomini.search as search_module
from algomini.search import diverse_elites, load_policy


ROOT = Path(__file__).parents[1]


@pytest.fixture
def cfg():
    return copy.deepcopy(load_config(ROOT / "run.toml")[1])


def test_discovery_budget_is_separate_and_preserves_global_reserve(tmp_path, cfg):
    cfg["budget"]["stage1_max_attempts"] = 1
    coder.budget_entry(tmp_path, cfg, "framework")
    with pytest.raises(ValueError, match="attempt limit"):
        coder.budget_entry(tmp_path, cfg, "more-framework")
    coder.budget_entry(tmp_path, cfg, "research", phase="discovery")
    entries = json.loads((tmp_path / "reports/budget/ledger.json").read_text())
    assert [entry["phase"] for entry in entries] == ["framework", "discovery"]
    cfg["budget"]["total_usd"] = 30
    cfg["budget"]["reserve_usd"] = 20
    with pytest.raises(ValueError, match="reserve"):
        coder.budget_entry(tmp_path, cfg, "excess", phase="discovery")


def test_candidate_and_repair_caps_are_independent_and_global(tmp_path, cfg):
    cfg["budget"]["initial_candidates"] = 1
    cfg["budget"]["repair_sessions"] = 1
    coder.budget_entry(tmp_path, cfg, "initial", phase="discovery")
    with pytest.raises(ValueError, match="candidate-attempt"):
        coder.budget_entry(tmp_path, cfg, "excess", phase="discovery")
    coder.budget_entry(tmp_path, cfg, "repair", phase="discovery", kind="repair")
    with pytest.raises(ValueError, match="repair-session"):
        coder.budget_entry(tmp_path, cfg, "another-repair", phase="refinement", kind="repair")


def test_memory_retrieves_observations_and_skills_and_invalidates_on_edit(tmp_path):
    record = {"id": "trial", "tags": ["decimal-deltas"], "hypothesis": "Exact mantissas preserve trailing zeros", "outcome": "failed"}
    assert memory.append(tmp_path, record)
    assert not memory.append(tmp_path, record)
    skill = tmp_path / ".agents/skills/csv-prefix/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: csv-prefix\ndescription: Investigate exact temporal prefixes.\n---\nPreserve timestamp suffixes and line endings.")
    first = memory.rebuild(tmp_path)
    assert first["documents"] == 2 and first["rebuilt"]
    matches = memory.search(tmp_path, "mantissas", max_chars=400)
    assert matches[0]["id"] == "trial"
    assert sum(len(value["excerpt"]) for value in matches) <= 400
    skill.write_text(skill.read_text() + "\nCounterexample: variable-width byteplanes.")
    second = memory.rebuild(tmp_path)
    assert first["source_sha256"] != second["source_sha256"]
    assert memory.search(tmp_path, "byteplanes")[0]["kind"] == "skill"
    assert memory.search(tmp_path, '" OR *; DROP TABLE knowledge --') == []


def test_exact_layout_offsets_and_decimal_spelling_stats():
    data = b'a,b\r\n1.00,-0\r\n+1.0,1e0'
    rows, offsets = research.layout(data)
    assert rows == [[b"a", b"b"], [b"1.00", b"-0"], [b"+1.0", b"1e0"]]
    assert offsets[1]["ending_bytes"] == 2 and offsets[-1]["ending_bytes"] == 0
    assert all(data[a:b] == token for row, record in zip(rows, offsets) for token, (a, b) in zip(row, record["tokens"]))
    stats = research.column_stats("x", [b"1.00", b"+1.0", b"-0", b"1e0"])
    assert stats["fraction_digits"] == [(0, 2), (1, 1), (2, 1)]
    assert stats["lexical_exceptions"]["negative_zero"] == 1 and stats["lexical_exceptions"]["leading_plus"] == 1
    assert stats["lexical_exceptions"]["exponent_tokens"] == 1
    quoted = research.column_stats("quoted", [b'"  60"', b'"19.890000000000001"', b'" 6.5999999999999996e+00"'])
    assert quoted["decimal_tokens"] == 3
    assert quoted["lexical_exceptions"]["quoted_tokens"] == 3 and quoted["lexical_exceptions"]["leading_padding_tokens"] == 2
    exact = research.column_stats("wide", [b"123456789012345678.123456789012345678"] * 2)
    assert exact["scaled_integer"]["max_magnitude_bits"] == 117
    with pytest.raises(ValueError, match="rectangular"):
        research.layout(b"a,b\n1\n")


@pytest.mark.parametrize("mode", [0, 1])
def test_training_representation_preserves_headers_duplicates_and_spelling(mode):
    data = b'left,right,other\r\n" 1.00"," 1.00",-0\r\n"2e0","2e0",+1.0'
    transformed = research.representation(data, mode)
    assert research.restore_representation(transformed) == data
    with pytest.raises(ValueError):
        research.restore_representation(transformed[:-1])


def item(identifier, family, size, vector, *, speed=True, source=None):
    return {"id": identifier, "mechanism": family, "source_sha256": source or identifier, "outcome": "measured",
            "measurements": {"archive_bytes": size, "archive_gain": .2,
                             "gates": {"exact_reconstruction": True, "decode_speed": speed, "decode_rss": True},
                             "files": [{"block_bytes": value, "input_bytes": 1000} for value in vector]}}


def test_diverse_elites_keep_families_and_exploration_and_reject_resource_failure():
    records = [item("best", "numeric", 100, [40, 60]), item("variant", "numeric", 101, [41, 60]),
               item("cross", "cross-field", 110, [60, 50]), item("layout", "column-layout", 120, [60, 60]),
               item("close", "prefix", 121, [61, 61]), item("explorer", "byteplanes", 130, [20, 110]),
               item("too-slow", "fake", 50, [20, 30], speed=False)]
    selected = diverse_elites(records)
    assert [record["id"] for record in selected] == ["best", "cross", "layout", "explorer"]
    assert len({record["mechanism"] for record in selected}) == 4
    assert selected[-1]["selection"] == "exploration profile"


def test_search_policy_starts_four_distinct_mechanisms_without_changing_frozen_config(tmp_path):
    policy = load_policy(ROOT)
    assert len(set(policy["initial_mechanisms"])) == 4
    path = tmp_path / "policy.toml"
    path.write_text((ROOT / "search.toml").read_text().replace('"cross-field"', '"column-layout"'))
    with pytest.raises(ValueError, match="distinct"):
        load_policy(tmp_path, path)


def test_explicit_budget_extension_cannot_change_model_data_or_scoring():
    old = {"policy": {"maximum_repairs": 2, "maximum_initial_candidates": 6, "maximum_run_seconds": 3600},
           "freeze_id": "same", "profile_sha256": "same", "coder": {"model": "same"}}
    extended = copy.deepcopy(old)
    extended["policy"]["maximum_repairs"] = 4
    assert search_module.budget_extension(old, extended)
    for field, value in (("freeze_id", "new"), ("profile_sha256", "new"), ("coder", {"model": "new"})):
        changed = copy.deepcopy(extended)
        changed[field] = value
        assert not search_module.budget_extension(old, changed)
    extended["policy"]["maximum_run_seconds"] = 7200
    assert not search_module.budget_extension(old, extended)


def test_completed_candidate_replay_never_starts_generation(tmp_path, cfg, monkeypatch):
    policy = load_policy(ROOT)
    directory = tmp_path / "reports/stage3/candidates/stage3-column-layout"
    directory.mkdir(parents=True)
    code = b"exact generated source"
    (directory / "codec.rs").write_bytes(code)
    evaluated = {"candidate": {"source_sha256": digest(code), "archive_sha256": "fixed", "archive_bytes": 10, "files": []},
                 "score": {"archive_gain": .2, "gates": {}, "minimum_decode_mib_per_second": 20,
                           "peak_decode_rss_kib": 100, "deployment_saving_bytes": -100}}
    record = {"id": "stage3-column-layout", "tags": ["column-layout"], "outcome": "measured",
              "source_sha256": digest(code), "measurements": search_module.measured_summary(evaluated)}
    write_json(directory / "outcome.json", record)
    monkeypatch.setattr(coder, "generate", lambda *args, **kwargs: pytest.fail("replay launched paid generation"))
    monkeypatch.setattr(search_module, "cached_evaluation", lambda *args: (evaluated, True))
    assert search_module.observation(tmp_path, cfg, policy, {}, record["id"], "column-layout") == record
    assert not (tmp_path / "reports/budget/ledger.json").exists()
    (directory / "codec.rs").write_bytes(b"changed")
    with pytest.raises(ValueError, match="source changed"):
        search_module.observation(tmp_path, cfg, policy, {}, record["id"], "column-layout")
    (directory / "codec.rs").write_bytes(code)
    record["measurements"]["archive_bytes"] = 999
    write_json(directory / "outcome.json", record)
    with pytest.raises(ValueError, match="measurement provenance"):
        search_module.observation(tmp_path, cfg, policy, {}, record["id"], "column-layout")


def test_evaluation_cache_mismatch_never_reruns_or_hides_corruption(tmp_path, cfg, monkeypatch):
    directory = tmp_path / "candidate"
    directory.mkdir()
    write_json(directory / "evaluation.json", {"altered": True})
    write_json(directory / "evaluation-cache.json", {"key": "wrong", "result_sha256": "wrong"})
    monkeypatch.setattr(search_module.registry, "resolve", lambda *args: {"source_sha256": "source"})
    monkeypatch.setattr(search_module.freeze, "verify", lambda *args: {"freeze_id": "freeze"})
    monkeypatch.setattr(search_module.evaluator, "probes", lambda *args: [("a", b"data")])
    monkeypatch.setattr(search_module.evaluator, "training_samples", lambda *args: [b"training"])
    monkeypatch.setattr(search_module.subprocess, "run", lambda *args, **kwargs: pytest.fail("corrupt cache launched another evaluation"))
    with pytest.raises(ValueError, match="checksum mismatch"):
        search_module.cached_evaluation(tmp_path, cfg, "candidate", directory)


def test_cached_model_and_counted_archive_are_bound_to_measurement(tmp_path, cfg, monkeypatch):
    directory = tmp_path / "candidate"
    directory.mkdir()
    artifact = tmp_path / "reports/evaluations/candidate/validation/archive.alc"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"counted archive")
    expected = {"freeze_id": "freeze", "source_sha256": "source", "inputs": [{"label": "a", "bytes": 4, "sha256": digest(b"data")}],
                "training_sha256": [digest(b"training")]}
    measured = {"candidate": {"archive_bytes": artifact.stat().st_size, "archive_sha256": sha256(artifact),
                              "trained_model_sha256": digest(b"model"), "build": {"fingerprint": "build"}}}
    result_path = directory / "evaluation.json"
    stamp_path = directory / "evaluation-cache.json"
    write_json(result_path, measured)
    write_json(stamp_path, {"key": search_module.fingerprint(expected), "result_sha256": sha256(result_path)})
    monkeypatch.setattr(search_module.registry, "resolve", lambda *args: {"source_sha256": "source"})
    monkeypatch.setattr(search_module.freeze, "verify", lambda *args: {"freeze_id": "freeze"})
    monkeypatch.setattr(search_module.evaluator, "probes", lambda *args: [("a", b"data")])
    monkeypatch.setattr(search_module.evaluator, "training_samples", lambda *args: [b"training"])
    monkeypatch.setattr(search_module.subprocess, "run", lambda *args, **kwargs: pytest.fail("cache validation launched evaluation"))
    assert search_module.cached_evaluation(tmp_path, cfg, "candidate", directory)[1]
    stamp = json.loads(stamp_path.read_text())
    assert stamp["schema_version"] == 2 and stamp["definition"]["model_sha256"] == digest(b"model")
    artifact.write_bytes(b"changed archive")
    with pytest.raises(ValueError, match="counted archive checksum"):
        search_module.cached_evaluation(tmp_path, cfg, "candidate", directory)
    artifact.write_bytes(b"counted archive")
    measured["candidate"]["trained_model_sha256"] = digest(b"different model")
    write_json(result_path, measured)
    stamp["result_sha256"] = sha256(result_path)
    write_json(stamp_path, stamp)
    with pytest.raises(ValueError, match="model/build cache checksum"):
        search_module.cached_evaluation(tmp_path, cfg, "candidate", directory)
