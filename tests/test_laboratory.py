import json
from pathlib import Path
import subprocess
import sys

import pytest

from algomini import labtools, memory
from algomini.campaigns import Campaign
from algomini.common import write_json


def skill(root, name, body, families=("*",)):
    path = root / f".agents/skills/{name}/SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text(f"---\nname: {name}\ndescription: Research procedure.\n---\n\n" + body)
    write_json(path.with_name("metadata.json"), {"families": list(families), "tags": [name]})
    return path


def test_incremental_index_updates_one_source_and_removes_deleted_skill(tmp_path):
    first = skill(tmp_path, "alpha", "Investigate temporal prefixes.")
    second = skill(tmp_path, "beta", "Investigate column layouts.")
    assert memory.rebuild(tmp_path)["updated_sources"] == 2
    assert memory.rebuild(tmp_path)["updated_sources"] == 0
    first.write_text(first.read_text() + "\n\nCounterexample: variable exponents.")
    assert memory.rebuild(tmp_path)["updated_sources"] == 1
    second.unlink()
    assert memory.rebuild(tmp_path)["removed_sources"] == 1
    assert memory.search(tmp_path, "layouts") == []


def test_retrieval_finds_late_sections_filters_families_and_bounds_context(tmp_path):
    skill(tmp_path, "csv", "padding " * 900 + "\n\nCritical byteplanes counterexample.", ("csv",))
    skill(tmp_path, "json", "Key templates preserve duplicate keys.", ("json",))
    matches = memory.search(tmp_path, "byteplanes", family="csv", max_chars=200)
    assert matches and "byteplanes" in matches[0]["excerpt"]
    assert sum(len(row["excerpt"]) for row in matches) <= 200
    assert memory.search(tmp_path, "byteplanes", family="json") == []
    before = memory.rebuild(tmp_path)["source_sha256"]
    write_json(tmp_path / ".agents/skills/json/metadata.json", {"families": ["csv"]})
    assert memory.rebuild(tmp_path)["source_sha256"] != before


class Embeddings:
    id = "synthetic-model-v1"

    def __init__(self):
        self.calls = []

    def encode(self, texts):
        self.calls.extend(texts)
        return [[1., 0.] if "planet" in text or "world" in text else [0., 1.] for text in texts]


def test_semantic_matching_without_keyword_overlap_reuses_vectors(tmp_path):
    skill(tmp_path, "astronomy", "planet orbits", ("text",))
    backend = Embeddings()
    matches = memory.search(tmp_path, "world", embedder=backend, family="text")
    assert matches[0]["id"] == "astronomy" and matches[0]["retrieval"] == "hybrid"
    count = len(backend.calls)
    memory.search(tmp_path, "world", embedder=backend, family="text")
    assert len(backend.calls) == count
    memory.export(tmp_path, embedder=backend)
    assert (tmp_path / "memory/vectors.jsonl").is_file()
    before = len(backend.calls)
    memory.rebuild(tmp_path, embedder=backend, cache_dir=tmp_path / "fresh-cache")
    assert len(backend.calls) == before  # Portable vectors avoid recomputing the library.


def test_active_test_observations_are_not_retrieved(tmp_path):
    memory.append(tmp_path, {"id": "private", "split": "test", "hypothesis": "secretword"})
    assert memory.search(tmp_path, "secretword") == []


def test_campaigns_have_independent_inputs_caches_and_replay(tmp_path, monkeypatch):
    library = tmp_path / "library"
    skill(library, "json", "Investigate repeated key templates.", ("json",))
    source = tmp_path / "sample.jsonl"
    source.write_bytes(b'{"x":1,"x":-0.0}\n{"x":1e0}\n')
    first = Campaign.create(tmp_path / "one", library, source, campaign_id="one", family="json")
    second = Campaign.create(tmp_path / "two", library, source, campaign_id="two", family="json")
    prepared = first.prepare()
    assert prepared["retrieved_characters"] <= 6000
    assert (first.workspace / ".cache/memory/index.sqlite").exists()
    assert not (library / ".cache").exists()
    assert not (second.workspace / "reports").exists()
    monkeypatch.setattr(labtools, "run", lambda *args, **kwargs: pytest.fail("replay repeated probes"))
    assert first.prepare() == prepared
    first.export(library)
    assert (library / "reports/campaigns/one/observations.jsonl").exists()
    source.write_bytes(b"changed")
    with pytest.raises(ValueError, match="different input"):
        Campaign.create(first.workspace, library, source, campaign_id="one", family="json")
    (second.workspace / "sample.bin").write_bytes(b"changed")
    with pytest.raises(ValueError, match="sample changed"):
        second.prepare()


def test_probes_preserve_lexical_distinctions_and_support_quoted_csv():
    result = labtools.run("json-lexemes", b'{"x":-0.0,"x":1e0,"n":1.00}', family="json")["facts"]
    assert result["duplicate_keys"] == 1 and result["negative_zero_spellings"] == 1
    assert result["exponent_numbers"] == 1 and result["distinct_number_spellings"] == 3
    csv = labtools.run("csv-structure", b'a,b\r\n"a,b","two\nlines"\r\n', family="csv")["facts"]
    assert csv["supported"] and csv["rows"] == 2
    assert not labtools.run("json-lexemes", b'{"fragment":', family="json")["facts"]["supported"]
    with pytest.raises(ValueError, match="bound"):
        labtools.run("byte-structure", b"x" * (labtools.MAX_INPUT + 1), family="text")
    with pytest.raises(ValueError, match="applicability"):
        labtools.run("json-lexemes", b"text", family="text")


def test_probe_registry_accepts_explicit_trusted_extension():
    labtools.register(labtools.Probe("test-length", "Tiny extension", ("*",), lambda data: {"length": len(data)}))
    try:
        assert labtools.run("test-length", b"abc", family="binary")["facts"] == {"length": 3}
        with pytest.raises(ValueError, match="duplicate"):
            labtools.register(labtools.PROBES["test-length"])
    finally:
        del labtools.PROBES["test-length"]


def test_core_cli_runs_without_site_packages():
    result = subprocess.run([sys.executable, "-S", "-m", "algomini", "lab", "list"],
                            cwd=Path(__file__).parents[1], capture_output=True, text=True, check=True)
    assert len(json.loads(result.stdout)["probes"]) == 4


@pytest.mark.parametrize("failure", ["preflight", "versions"])
def test_historical_stage0_failures_return_structured_cli_results(tmp_path, monkeypatch, failure):
    from algomini import foundation
    expected = {"ok": False, "reason": failure}
    monkeypatch.setattr(foundation.environment, "doctor", lambda *args: expected if failure == "preflight" else {"ok": True})
    if failure == "versions":
        (tmp_path / "repro").mkdir()
        (tmp_path / "repro/environment.json").write_text("{}")
        monkeypatch.setattr(foundation.environment, "verify_versions", lambda *args: expected)
    monkeypatch.setattr(foundation.dataset, "prepare", lambda *args: pytest.fail("failed preflight continued into data preparation"))
    assert foundation.run(tmp_path, {}) == expected
