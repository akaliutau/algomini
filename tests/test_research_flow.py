import json
from pathlib import Path

import pytest

from algomini.cloud import coordinator, research
from algomini.cloud.github import State
from algomini.common import write_json
from test_cloud_contracts import MemoryGit, ROOT, snapshot


def losing_research():
    candidate = {"id": "candidate", "archive_bytes": 100, "payload_bytes": 40, "block_bytes": 80, "model_bytes": 0,
                 "decoder_package_bytes": 1000, "files": [{"encode": {"execution_seconds": .01},
                 "decode": {"decode_mib_per_second": 12., "peak_rss_kib": 1024}}]}
    measured = {"eligible": True, "size_first_win": False, "candidate": candidate, "baselines": {"best_bytes": 90, "results": []},
                "score": {"archive_gain": 1 - 100 / 90}}
    return {"development": measured, "validation": measured, "charged_usd": 0., "origin": "reused seed",
            "selected": {"source_sha256": "a" * 64, "model_sha256": "b" * 64}, "failures": [],
            "trials": [{"item": {"id": "candidate"}, "origin": "reused seed", "result": measured}]}


def test_development_loss_never_consumes_final_and_negative_evidence_reaches_next_claim(tmp_path, monkeypatch):
    request = snapshot(monkeypatch)
    gh = MemoryGit()
    store = State(gh, "automation-state", 0)
    store.reserve(request, "run")
    write_json(tmp_path / "research.json", losing_research())
    write_json(tmp_path / "freeze.json", {"fixture": True})
    monkeypatch.setattr(coordinator, "checked_checkpoint", lambda *_: ({}, "f" * 64))
    monkeypatch.setattr(coordinator, "review_files", lambda *_a, **_kw: {"release/registry.json": b"{}"})
    receipt = tmp_path / "receipt.json"
    coordinator.freeze(ROOT, tmp_path, request, receipt, gh)
    assert json.loads(receipt.read_text())["verify"] is False
    assert not State(gh, "automation-state", 0).value["tests"]
    coordinator.publish(ROOT, tmp_path, request["issue"], gh)
    current = State(gh, "automation-state", 0).value
    assert current["requests"][request["id"]]["status"] == "no-development-gain"
    assert current["requests"][request["id"]]["charged_usd"] == 0
    assert "not opened" in current["requests"][request["id"]]["report"].lower()
    assert not current["tests"]
    assert all(row["split"] == "validation" for row in current["development_evidence"])
    commits = gh.commits
    coordinator.publish(ROOT, tmp_path, request["issue"], gh)
    assert gh.commits == commits
    monkeypatch.setenv("GITHUB_SHA", "a" * 40)
    monkeypatch.setenv("GITHUB_RUN_ID", "123")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    issue = {"number": 8, "body": "### Plugin name\n\nnext\n\n### Dataset manifest URL\n\n" + request["dataset_manifest_url"]}
    coordinator.claim(ROOT, {"issue": issue, "mode": "reuse"}, request["image"], tmp_path / "next/request.json", gh)
    prior = json.loads((tmp_path / "next/prior-observations.json").read_text())
    assert prior and all(row["split"] != "test" for row in prior)


def test_a_valid_claim_cannot_bypass_the_development_gate(tmp_path, monkeypatch):
    request = snapshot(monkeypatch)
    write_json(tmp_path / "research.json", {"development_gate": {"passed": False}})
    monkeypatch.setattr(research, "check_freeze", lambda *_: ({}, "f" * 64))
    monkeypatch.setattr(research.DatasetFile, "fetch", lambda *_: pytest.fail("Final input was opened"))
    with pytest.raises(ValueError, match="Development did not qualify"):
        research.verify(ROOT, tmp_path, request, {"request_id": request["id"], "freeze_id": "f" * 64, "test_consumed_at": "now"})


def test_prior_context_rejects_final_measurements(tmp_path):
    write_json(tmp_path / "prior-observations.json", [{"id": "forbidden", "split": "test"}])
    with pytest.raises(ValueError, match="prior development evidence"):
        research.knowledge_library(ROOT, tmp_path)
