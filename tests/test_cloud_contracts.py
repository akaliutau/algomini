import copy
import json
from pathlib import Path

import pytest

from algomini.cloud import contracts, coordinator, research
from algomini.cloud.github import APIError, State


ROOT = Path(__file__).parents[1]
SHA = "a" * 40
IMAGE = "ghcr.io/akaliutau/algomini-lab@sha256:" + "b" * 64


def descriptor():
    return {"schema_version": 1, "family": "xml", "provenance": {"source": "Synthetic fixture", "license": "CC0"},
        "train": {"url": f"https://raw.githubusercontent.com/example/data/{SHA}/train.txt", "bytes": 10, "sha256": "c" * 64},
        "test": {"url": f"https://raw.githubusercontent.com/example/data/{SHA}/test.txt", "bytes": 11, "sha256": "d" * 64}}


def snapshot(monkeypatch):
    monkeypatch.setattr(contracts, "download", lambda *_: json.dumps(descriptor()).encode())
    issue = {"number": 7, "body": f"### Plugin name\n\nwiki-demo\n\n### Dataset manifest URL\n\nhttps://raw.githubusercontent.com/example/data/{SHA}/manifest.json"}
    return contracts.snapshot(ROOT, issue, "akaliutau/algomini", SHA, IMAGE)


@pytest.mark.parametrize("url", ["http://raw.githubusercontent.com/a/b/" + SHA + "/x", "https://127.0.0.1/x",
    "https://raw.githubusercontent.com/a/b/main/x", "https://key@raw.githubusercontent.com/a/b/" + SHA + "/x",
    "https://raw.githubusercontent.com/a/b/" + SHA + "/x?token=secret", "https://raw.githubusercontent.com:444/a/b/" + SHA + "/x"])
def test_dataset_urls_are_public_immutable_and_credential_free(url):
    with pytest.raises(ValueError):
        contracts.public_url(url, contracts.policy(ROOT))


@pytest.mark.parametrize("change", ["same", "oversize", "bad-hash", "missing-license", "extra-key", "bool-size"])
def test_manifest_rejects_ambiguous_or_invalid_inputs(change):
    value = descriptor()
    if change == "same": value["test"] = value["train"]
    elif change == "oversize": value["train"]["bytes"] = 2097153
    elif change == "bad-hash": value["train"]["sha256"] = "wrong"
    elif change == "missing-license": del value["provenance"]["license"]
    elif change == "extra-key": value["command"] = "execute me"
    elif change == "bool-size": value["train"]["bytes"] = True
    with pytest.raises(ValueError):
        contracts.manifest(json.dumps(value), contracts.policy(ROOT))


def test_form_has_two_fields_and_snapshot_refuses_changes(monkeypatch):
    value = snapshot(monkeypatch)
    assert contracts.validate_snapshot(value, ROOT) == value
    value["plugin"] = "$(execute)"
    with pytest.raises(ValueError): contracts.validate_snapshot(value, ROOT)
    with pytest.raises(ValueError): contracts.issue_fields("### Plugin name\nwiki\n### Extra\nhello")


def test_retry_attempt_cannot_reuse_a_spending_or_test_claim(monkeypatch):
    monkeypatch.setenv("ALGOMINI_RUN_ID", "123")
    monkeypatch.setenv("ALGOMINI_RUN_ATTEMPT", "1")
    contracts.execution_guard({"run_id": "123", "run_attempt": 1})
    monkeypatch.setenv("ALGOMINI_RUN_ATTEMPT", "2")
    with pytest.raises(ValueError, match="cannot retry"):
        contracts.execution_guard({"run_id": "123", "run_attempt": 1})


def test_unclaimed_cloud_research_cannot_contact_provider(monkeypatch, tmp_path):
    request = snapshot(monkeypatch)
    monkeypatch.setattr(research, "check_api", lambda *_: pytest.fail("Unclaimed research contacted provider"))
    with pytest.raises(ValueError, match="durable Actions execution claim"):
        research.search(ROOT, tmp_path, request)


def test_coder_uses_image_tools_when_checkout_is_a_readonly_mount(monkeypatch, tmp_path):
    request = snapshot(monkeypatch)
    (tmp_path / "run.toml").write_bytes((ROOT / "run.toml").read_bytes())
    config = research.configuration(tmp_path, request)
    assert Path(config["coder"]["executable"]).parent == ROOT / ".tools/bin"
    assert not str(config["coder"]["executable"]).startswith(str(tmp_path))


class MemoryGit:
    def __init__(self):
        self.repository = "akaliutau/algomini"
        self.branches = {}
        self.files = {}
        self.commits = 0
        self.comments = []
        self.permission = "write"

    def head(self, branch):
        return self.branches.get(branch)

    def file(self, path, ref):
        ref = self.branches.get(ref, ref)
        return self.files[ref][path]

    def commit(self, branch, parent, files, message, *, tree_base=None):
        if branch in self.branches and parent != self.branches[branch]:
            raise APIError(422, "non-fast-forward")
        self.commits += 1
        head = f"{self.commits:040x}"
        previous = self.files.get(tree_base, {})
        self.files[head] = {**previous, **files}
        self.branches[branch] = head
        return head

    def comment(self, issue, body):
        self.comments[:] = [(issue, body)]

    def repo(self, method, path, value=None):
        if "/permission" in path:
            return {"permission": self.permission}
        raise AssertionError((method, path))


def test_duplicate_reservation_and_stale_git_writer_cannot_spend(monkeypatch):
    request = snapshot(monkeypatch)
    gh = MemoryGit()
    first, stale = State(gh, "automation-state", 11.99), State(gh, "automation-state", 11.99)
    assert first.reserve(request, "https://github.com/run/1")
    other = copy.deepcopy(request);other["id"]="issue-8";other["issue"]=8
    with pytest.raises(APIError): stale.reserve(other, "https://github.com/run/2")
    fresh = State(gh, "automation-state", 0)
    assert not fresh.reserve(request, "duplicate")
    assert len(fresh.value["requests"]) == 1
    assert fresh.value["historical_charged_usd"] == 11.99


def test_provider_reserve_and_one_final_claim_are_durable(monkeypatch):
    request = snapshot(monkeypatch)
    gh = MemoryGit()
    store = State(gh, "automation-state", 111.)
    with pytest.raises(ValueError, match="budget"):
        store.reserve(request, "run")
    assert not gh.branches
    store = State(gh, "automation-state", 11.99)
    store.reserve(request, "run")
    store.consume(request["id"], "frozen")
    replay = State(gh, "automation-state", 0)
    with pytest.raises(ValueError, match="consumed"):
        replay.consume(request["id"], "frozen")
    assert replay.value["tests"][request["dataset"]["test"]["sha256"]] == [request["id"]]


def test_external_request_needs_actual_write_permission_before_model_work(monkeypatch, tmp_path):
    request = snapshot(monkeypatch)
    issue = {"number": 7, "body": "### Plugin name\n\nwiki-demo\n\n### Dataset manifest URL\n\n" + request["dataset_manifest_url"], "labels": [{"name": "codec-request"}]}
    event = {"repository": {"full_name": "akaliutau/algomini"}, "issue": issue, "action": "opened", "sender": {"login": "visitor"}}
    gh = MemoryGit();gh.permission = "triage"
    output = tmp_path / "output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    coordinator.gate(ROOT, event, "issues", tmp_path / "intake", gh)
    assert "start=false" in output.read_text()
    assert not gh.branches and "Waiting for approval" in gh.comments[0][1]
