import json
from pathlib import Path

import pytest

from algomini.cloud import coordinator, releasing
from algomini.cloud.github import APIError, State
from algomini.common import sha256, write_json
from test_cloud_contracts import MemoryGit, ROOT, snapshot


class ReleaseGit(MemoryGit):
    def __init__(self, assets):
        super().__init__()
        self.assets = assets
        self.merged = False
        self.created_releases = 0
        self.tag = None
        self.draft = {"id": 99, "tag_name": "codec-issue-7", "draft": True, "html_url": "https://github.com/akaliutau/algomini/releases/tag/codec-issue-7"}

    def repo(self, method, path, value=None):
        if path == "pulls/9":
            return {"merged": self.merged, "base": {"ref": "main", "repo": {"full_name": self.repository}}}
        if path == "git/ref/tags/codec-issue-7":
            if self.tag: return {"object": {"type": "commit", "sha": self.tag}}
            raise APIError(404, method)
        if path == "git/refs":
            self.tag = value["sha"];return {"object": {"sha": self.tag}}
        if path == "releases/tags/codec-issue-7":
            raise APIError(404, method)  # Draft lookup must recover through list releases.
        if path == "releases?per_page=100": return [self.draft]
        if path == "releases":
            self.created_releases += 1;raise AssertionError("Duplicate draft release created")
        if path == "releases/99/assets?per_page=100": return self.assets
        if path == "releases/99" and method == "PATCH":
            self.draft.update(value);return self.draft
        return super().repo(method, path, value)


def test_release_requires_merge_and_recovers_an_existing_draft_without_uploads(monkeypatch, tmp_path):
    request = snapshot(monkeypatch)
    root, output = tmp_path / "repo", tmp_path / "output"
    root.mkdir();(root / "cloud").mkdir()
    (root / "cloud/policy.json").write_bytes((ROOT / "cloud/policy.json").read_bytes())
    directory = output / "issue-7";directory.mkdir(parents=True)
    record = {"request_id": "issue-7", "issue": 7, "freeze_id": "f" * 64, "tag": "codec-issue-7", "source_commit": "a" * 40}
    qualified = {"schema_version": 1, "aliases": {}, "codecs": []}
    write_json(directory / "registry.json", qualified)
    (directory / "report.md").write_text("Measured report")
    (directory / "algomini-linux-x86_64").write_bytes(b"qualified binary fixture; not executed")
    record.update(binary_sha256=sha256(directory / "algomini-linux-x86_64"), registry_sha256=sha256(directory / "registry.json"))
    write_json(directory / "manifest.json", record)
    names = ["manifest.json", "registry.json", "report.md", "algomini-linux-x86_64"]
    (directory / "SHA256SUMS").write_text("".join(f"{sha256(directory / name)}  {name}\n" for name in names))
    write_json(root / "release/registry.json", {**qualified, "requests": [{"id": "issue-7", "freeze_id": "f" * 64}]})
    gh = ReleaseGit([{"name": name, "digest": "sha256:" + sha256(directory / name)} for name in [*names, "SHA256SUMS"]])
    store = State(gh, "automation-state", 0)
    store.reserve(request, "run")
    store.consume("issue-7", "f" * 64)
    store.value["requests"]["issue-7"].update(freeze_id="f" * 64, frozen_commit="a" * 40, pr_number=9, report="Measured report", status="pr-open", terminal=True)
    store.save("Fixture PR")
    gh.files["a" * 40] = {"release/registry.json": json.dumps(qualified).encode()}
    monkeypatch.setattr(releasing, "upload", lambda *_: pytest.fail("Matching draft asset was uploaded again"))
    with pytest.raises(ValueError, match="not been merged"):
        releasing.publish_release(root, output, gh)
    assert gh.tag is None
    gh.merged = True
    sums = (directory / "SHA256SUMS").read_text()
    (directory / "SHA256SUMS").write_text("")
    with pytest.raises(ValueError, match="Incomplete release checksums"):
        releasing.publish_release(root, output, gh)
    assert gh.tag is None
    (directory / "SHA256SUMS").write_text(sums)
    durable = State(gh, "automation-state", 0)
    consumed = durable.value["requests"]["issue-7"].pop("test_consumed_at")
    durable.save("Fixture missing consumption")
    with pytest.raises(ValueError, match="consumed request"):
        releasing.publish_release(root, output, gh)
    assert gh.tag is None
    durable.value["requests"]["issue-7"]["test_consumed_at"] = consumed
    durable.save("Restore fixture consumption")
    releasing.publish_release(root, output, gh)
    releasing.publish_release(root, output, gh)
    assert gh.created_releases == 0 and gh.tag == "a" * 40
    assert not gh.draft["draft"] and len(gh.comments) == 1
    assert State(gh, "automation-state", 0).value["requests"]["issue-7"]["status"] == "released"


def test_report_survives_status_replay():
    row = {"status": "pr-open", "snapshot": {"id": "issue-7", "plugin": "wiki"}, "run_url": "run", "charged_usd": .1,
           "report": "| Method | Bytes |\n|---|---:|\n| codec | 42 |"}
    assert row["report"] in coordinator.status_comment(row)
