import json
from pathlib import Path

import pytest

from algomini.cloud import coordinator, releasing
from algomini.cloud.github import APIError, State
from algomini.common import sha256, write_json
from test_cloud_contracts import MemoryGit, ROOT, snapshot


class ReleaseGit(MemoryGit):
    def __init__(self, assets, existing_release="draft"):
        super().__init__()
        self.assets = assets
        self.merged = False
        self.created_releases = 0
        self.release_exists = existing_release != "new"
        self.release_updates = []
        self.tag = None
        self.draft = {"id": 99, "tag_name": "codec-issue-7", "name": "Algomini issue-7", "draft": existing_release != "published",
                      "html_url": "https://github.com/akaliutau/algomini/releases/tag/codec-issue-7"}

    def repo(self, method, path, value=None):
        if path == "pulls/9":
            return {"merged": self.merged, "base": {"ref": "main", "repo": {"full_name": self.repository}}}
        if path == "git/ref/tags/codec-issue-7":
            if self.tag: return {"object": {"type": "commit", "sha": self.tag}}
            raise APIError(404, method)
        if path == "git/refs":
            self.tag = value["sha"];return {"object": {"sha": self.tag}}
        if path == "releases/tags/codec-issue-7":
            if self.release_exists and not self.draft["draft"]:
                return self.draft
            raise APIError(404, method)  # Draft lookup must recover through list releases.
        if path == "releases?per_page=100": return [self.draft] if self.release_exists else []
        if path == "releases":
            assert not self.release_exists, "Duplicate draft release created"
            assert value["name"] == "Algomini v0.3.3"
            self.created_releases += 1
            self.release_exists = True
            self.draft.update(value)
            return self.draft
        if path == "releases/99/assets?per_page=100": return self.assets
        if path == "releases/99" and method == "PATCH":
            self.release_updates.append(value)
            self.draft.update(value);return self.draft
        return super().repo(method, path, value)


@pytest.mark.parametrize("existing_release", ["new", "draft", "published"])
def test_release_requires_merge_and_publishes_or_recovers_idempotently(monkeypatch, tmp_path, existing_release):
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
    assets = [{"name": name, "digest": "sha256:" + sha256(directory / name)} for name in [*names, "SHA256SUMS"]]
    gh = ReleaseGit([] if existing_release == "new" else assets, existing_release)
    store = State(gh, "automation-state", 0)
    store.reserve(request, "run")
    store.consume("issue-7", "f" * 64)
    store.value["requests"]["issue-7"].update(freeze_id="f" * 64, frozen_commit="a" * 40, pr_number=9, report="Measured report", status="pr-open", terminal=True)
    store.save("Fixture PR")
    # The title must use the frozen package version, not this checkout's version.
    gh.files["a" * 40] = {"release/registry.json": json.dumps(qualified).encode(),
                          "crates/algomini-cli/Cargo.toml": b'[package]\nversion = "0.3.3"\n'}
    uploads = []
    def upload(_gh, _release, path):
        assert existing_release == "new", "Matching release asset was uploaded again"
        asset = {"name": path.name, "digest": "sha256:" + sha256(path)}
        gh.assets.append(asset)
        uploads.append(path.name)
        return asset
    monkeypatch.setattr(releasing, "upload", upload)
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
    assert gh.created_releases == (1 if existing_release == "new" else 0) and gh.tag == "a" * 40
    assert gh.draft["name"] == "Algomini v0.3.3"
    assert gh.release_updates == [{"draft": False, "name": "Algomini v0.3.3"}]
    assert uploads == (["algomini-linux-x86_64", "report.md", "registry.json", "manifest.json", "SHA256SUMS"] if existing_release == "new" else [])
    assert not gh.draft["draft"] and len(gh.comments) == 1
    assert State(gh, "automation-state", 0).value["requests"]["issue-7"]["status"] == "released"


def test_report_survives_status_replay():
    row = {"status": "pr-open", "snapshot": {"id": "issue-7", "plugin": "wiki"}, "run_url": "run", "charged_usd": .1,
           "report": "| Method | Bytes |\n|---|---:|\n| codec | 42 |"}
    assert row["report"] in coordinator.status_comment(row)
