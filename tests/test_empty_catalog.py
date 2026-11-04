import copy
import json
import subprocess

import pytest

from algomini.cloud import coordinator, native, releasing, research
from algomini.cloud.github import State
from algomini.common import digest, write_json
from test_cloud_contracts import MemoryGit, ROOT, snapshot


def test_empty_client_builds_lists_no_plugins_and_rejects_unknown_codec(empty_library, tmp_path):
    registry = json.loads((empty_library / "release/registry.json").read_text())
    assert native.assets(empty_library, registry) == {}
    binary, _ = native.build(empty_library, tmp_path, registry)
    listed = subprocess.run([binary, "plugins"], check=True, capture_output=True, text=True)
    assert "No plugins installed" in listed.stdout
    source, output = tmp_path / "sample.bin", tmp_path / "out.alc"
    source.write_bytes(b"source bytes")
    rejected = subprocess.run([binary, "compress", "--plugin", "missing", source, output], capture_output=True)
    assert rejected.returncode == 1 and b"unknown codec" in rejected.stderr and not output.exists()
    with pytest.raises(ValueError, match="unknown codec"):
        native.assets(empty_library, {**registry, "aliases": {"dangling": "missing"}})


def test_cloud_reset_clears_research_once_preserving_spending_requests_and_tests(empty_library, monkeypatch):
    request = snapshot(monkeypatch)
    gh = MemoryGit()
    store = State(gh, "automation-state", 12.5)
    store.reserve(request, "run")
    store.consume(request["id"], "f" * 64)
    store.value["development_evidence"] = [{"id": "old", "split": "validation"}]
    store.save("Old development context")
    before = copy.deepcopy(store.value)
    current = coordinator.state(empty_library, gh)
    assert current.value["development_evidence"] == []
    for key in ("requests", "tests", "historical_charged_usd"):
        assert current.value[key] == before[key]
    current.value["development_evidence"] = [{"id": "new", "split": "validation"}]
    current.save("New campaign evidence")
    commits = gh.commits
    assert coordinator.state(empty_library, gh).value["development_evidence"] == current.value["development_evidence"]
    assert gh.commits == commits


def test_empty_catalog_can_generate_freeze_and_verify_its_first_codec(empty_library, monkeypatch, tmp_path):
    request = snapshot(monkeypatch)
    request["dataset"]["family"] = "json"
    request["execution"] = {"run_id": "17", "run_attempt": 1}
    monkeypatch.setenv("ALGOMINI_RUN_ID", "17")
    monkeypatch.setenv("ALGOMINI_RUN_ATTEMPT", "1")
    train = (ROOT / "datasets/sample/train-1mb.json").read_bytes()
    final_bytes = (ROOT / "datasets/sample/test-512kb.json").read_bytes()
    for part, data in (("train", train), ("test", final_bytes)):
        request["dataset"][part].update(bytes=len(data), sha256=digest(data))
    opened = []
    allow_final = False
    def fetch(self, _rules):
        opened.append(self.sha256)
        if self.sha256 == digest(final_bytes):
            assert allow_final, "Final bytes reached research"
            return final_bytes
        return train
    monkeypatch.setattr(research.DatasetFile, "fetch", fetch)
    monkeypatch.setattr(research, "check_api", lambda *_: {"ok": True})
    fixture = ROOT / "tests/fixtures/plugins/cx-4c16b773d1840d2130089affeddad6c7-e3b0c44298fc1c14/codec.rs"
    prompts = []
    def generate(workspace, _cfg, prompt, _sample, **_kwargs):
        # Simulate coding only; real compilation, baselines, controls and final
        # verification run below. No provider call or production seed is used.
        prompts.append(prompt)
        source = workspace / "generated.rs"
        source.write_text("// Deterministic test candidate.\n" + fixture.read_text())
        return source, {"attempt_id": "a" * 64}
    monkeypatch.setattr(research.coder, "generate", generate)
    result = research.search(empty_library, tmp_path, request)
    assert "No parent source" in prompts[0]
    assert all(not row["origin"].startswith("reused") for row in result["trials"])
    assert result["development_gate"]["passed"] and len(opened) == 1
    assert result["origin"].startswith("Muse-generated")
    assert not (tmp_path / "final.json").exists()
    _, frozen = research.check_freeze(empty_library, tmp_path, request)
    allow_final = True
    receipt = {"request_id": request["id"], "freeze_id": frozen, "test_consumed_at": "now", "execution": request["execution"],
               "frozen_commit": "e" * 40, "test_previously_used": True}
    final = research.verify(empty_library, tmp_path, request, receipt)
    assert final["size_first_win"] and final["console"]["exact"] and len(opened) == 2
    with pytest.raises(ValueError, match="consumed"):
        research.verify(empty_library, tmp_path, request, receipt)
    files = coordinator.review_files(empty_library, tmp_path, request, final=True)
    assert result["selected"]["source"] in files
    registry = json.loads(files["release/registry.json"])
    assert len(registry["codecs"]) == 1 and set(registry["aliases"]) == {request["plugin"]}
    prefix = f"reports/campaigns/{request['id']}/"
    files[prefix + "receipt.json"] = json.dumps(receipt).encode()
    files[prefix + "report.md"] = coordinator.report(request, result, final).encode()
    registry["requests"] = [{"id": request["id"], "issue": request["issue"], "freeze_id": frozen,
                             "image": request["image"], "selected": result["selected"]["id"]}]
    files["release/registry.json"] = json.dumps(registry).encode()
    merged = tmp_path / "merged"
    for name, data in files.items():
        path = merged / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    allow_final = False
    monkeypatch.setenv("ALGOMINI_BUILD_WORKSPACE", str(tmp_path / "release-build"))
    releasing.build_release(merged, tmp_path / "release-output")
    manifest = json.loads((tmp_path / "release-output" / request["id"] / "manifest.json").read_text())
    assert manifest["console"]["exact"] and len(opened) == 3  # Development refetch only.


def test_empty_catalog_reuse_and_tiny_input_cannot_call_the_provider(empty_library, monkeypatch, tmp_path):
    request = snapshot(monkeypatch)
    request["dataset"]["family"] = "binary"
    request["execution"] = {"run_id": "18", "run_attempt": 1}
    request["dataset"]["train"].update(bytes=0, sha256=digest(b""))
    monkeypatch.setenv("ALGOMINI_RUN_ID", "18")
    monkeypatch.setenv("ALGOMINI_RUN_ATTEMPT", "1")
    monkeypatch.setattr(research.DatasetFile, "fetch", lambda self, _: b"")
    monkeypatch.setattr(research, "check_api", lambda *_: pytest.fail("Impossible gain contacted provider"))
    monkeypatch.setattr(research.coder, "generate", lambda *_a, **_k: pytest.fail("Empty catalog reuse contacted provider"))
    request["mode"] = "reuse"
    with pytest.raises(ValueError, match="No plugins are registered"):
        research.search(empty_library, tmp_path / "reuse", request)
    request["mode"] = "generate"
    with pytest.raises(ValueError, match="framing floor"):
        research.search(empty_library, tmp_path / "tiny", request)
