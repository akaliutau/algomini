"""Rebuild qualified snapshots; publish only after their entries reach main."""

import json
import os
from pathlib import Path
import shutil
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, build_opener

from .. import archive
from ..common import digest, load_config, sha256, write_json
from . import coordinator, native
from .contracts import DatasetFile
from .github import APIError, NoRedirect


def build_release(library, destination):
    library, destination = Path(library), Path(destination)
    registry = json.loads((library / "release/registry.json").read_text())
    destination.mkdir(parents=True, exist_ok=True)
    wanted = os.environ.get("ALGOMINI_RELEASE_REQUEST")
    entries = [row for row in registry.get("requests", []) if not wanted or row["id"] == wanted]
    for entry in entries:
        prefix = library / "reports/campaigns" / entry["id"]
        request = json.loads((prefix / "request.json").read_text())
        final = json.loads((prefix / "final.json").read_text())
        frozen = json.loads((prefix / "freeze.json").read_text())
        receipt = json.loads((prefix / "receipt.json").read_text())
        build = json.loads((prefix / "build.json").read_text())
        if (not final.get("size_first_win") or not final.get("eligible") or digest((prefix / "freeze.json").read_bytes()) != entry["freeze_id"]
                or final["freeze_id"] != entry["freeze_id"] or receipt["freeze_id"] != entry["freeze_id"]
                or entry["image"] != request["image"] or final["input_sha256"] != request["dataset"]["test"]["sha256"]
                or final["candidate"]["archive_bytes"] >= final["baselines"]["best_bytes"]):
            raise ValueError("Release entry does not carry a passing consumed final outcome")
        # Rebuild the exact qualified catalog, including its aliases. A later merged
        # request must not silently alter an older request's source snapshot.
        qualified = build["definition"]["registry"]
        # This module executes inside the request's immutable image. Its trusted
        # code remains the qualified version even if main has since evolved.
        qualified_library = Path(__file__).parents[2]
        if build["definition"]["runtime"] != native.runtime(qualified_library, entry["image"]):
            raise ValueError("Release driver/toolchain differs from qualification")
        workspace = Path(os.environ.get("ALGOMINI_BUILD_WORKSPACE", "/work"))
        workspace.mkdir(parents=True, exist_ok=True)
        binary, rebuilt = native.build(qualified_library, workspace, qualified, asset_root=library, image=entry["image"])
        if rebuilt["binary_sha256"] != frozen["files"]["algomini"]:
            raise ValueError("Clean release rebuild differs from the qualified executable")
        _, cfg = load_config(qualified_library / "run.toml")
        item = next(row for row in qualified["codecs"] if row["id"] == entry["selected"])
        model = (library / item["model"]).read_bytes()
        development = DatasetFile.parse(request["dataset"]["train"], request["policy"]).fetch(request["policy"])
        encoded, _ = native.invoke(binary, item["id"], cfg, 6, development, model=model, source_sha256=item["source_sha256"])
        packed = archive.pack(item["id"], [("sample.bin", encoded)], model if encoded[4] == 2 else b"")
        measured = json.loads((prefix / "development.json").read_text())
        if digest(packed) != measured["candidate"]["archive_sha256"]:
            raise ValueError("Release development archive differs from frozen qualification")
        smoke = native.console_roundtrip(binary, cfg, development, item["id"], packed)
        directory = destination / entry["id"]
        directory.mkdir(exist_ok=True)
        shutil.copyfile(binary, directory / "algomini-linux-x86_64")
        shutil.copyfile(prefix / "report.md", directory / "report.md")
        write_json(directory / "registry.json", qualified)
        record = {"schema_version": 1, "request_id": entry["id"], "issue": entry["issue"], "tag": "codec-" + entry["id"],
                  "source_commit": receipt["frozen_commit"], "freeze_id": entry["freeze_id"], "image": entry["image"],
                  "binary_sha256": rebuilt["binary_sha256"], "selected": item, "console": smoke,
                  "final_archive_bytes": final["candidate"]["archive_bytes"], "final_baseline_bytes": final["baselines"]["best_bytes"],
                  "registry_sha256": sha256(directory / "registry.json")}
        write_json(directory / "manifest.json", record)
        names = ["algomini-linux-x86_64", "report.md", "registry.json", "manifest.json"]
        (directory / "SHA256SUMS").write_text("".join(f"{sha256(directory / name)}  {name}\n" for name in names))
        for path in directory.iterdir():
            path.chmod(0o644)  # The host artifact uploader runs as a different UID.
    write_json(destination / "build-summary.json", {"requests": [row["id"] for row in entries]})
    (destination / "build-summary.json").chmod(0o644)


def upload(gh, release, path):
    path = Path(path)
    url = f"https://uploads.github.com/repos/{gh.repository}/releases/{release['id']}/assets?name=" + quote(path.name)
    request = Request(url, data=path.read_bytes(), method="POST", headers={"Authorization": "Bearer " + gh.token,
        "Content-Type": "application/octet-stream", "Accept": "application/vnd.github+json", "User-Agent": "algomini-cloud/1"})
    try:
        with build_opener(NoRedirect()).open(request, timeout=120) as response:
            return json.load(response)
    except HTTPError as error:
        raise APIError(error.code, "asset upload") from None


def publish_release(library, destination, gh):
    destination = Path(destination)
    approved = {row["id"]: row for row in json.loads((Path(library) / "release/registry.json").read_text()).get("requests", [])}
    for directory in sorted(destination.glob("issue-*")):
        record = json.loads((directory / "manifest.json").read_text())
        entry = approved.get(record["request_id"])
        if not entry or entry["freeze_id"] != record["freeze_id"] or record["tag"] != "codec-" + record["request_id"]:
            raise ValueError("Release build is not approved by the checked-out main registry")
        store = coordinator.state(library, gh)
        remote = store.value["requests"].get(record["request_id"])
        if (not remote or remote.get("freeze_id") != record["freeze_id"] or remote.get("frozen_commit") != record["source_commit"]
                or not remote.get("pr_number") or not remote.get("test_consumed_at") or not remote.get("terminal")
                or remote.get("status") not in {"pr-open", "released"}):
            raise ValueError("Release has no durable, consumed request/PR record")
        merged = gh.repo("GET", f"pulls/{remote['pr_number']}")
        if not merged.get("merged") or merged["base"]["ref"] != "main" or merged["base"]["repo"]["full_name"] != gh.repository:
            raise ValueError("Release PR has not been merged into this repository's main branch")
        expected_names = {"algomini-linux-x86_64", "report.md", "registry.json", "manifest.json"}
        checked_names = set()
        for line in (directory / "SHA256SUMS").read_text().splitlines():
            checksum, name = line.split("  ", 1)
            if name not in expected_names or name in checked_names or sha256(directory / name) != checksum:
                raise ValueError("Release artifact checksum mismatch")
            checked_names.add(name)
        if checked_names != expected_names or sha256(directory / "algomini-linux-x86_64") != record["binary_sha256"] or sha256(directory / "registry.json") != record["registry_sha256"]:
            raise ValueError("Incomplete release checksums or manifest mismatch")
        # The tag identifies the exact qualified source already included in the
        # merged PR, so Cargo installation builds that snapshot, not a newer catalog.
        tagged = gh.file("release/registry.json", record["source_commit"])
        expected = json.loads((directory / "registry.json").read_text())
        original = json.loads(tagged)
        comparable = {"schema_version": 1, "aliases": original["aliases"], "codecs": [
            {key: row[key] for key in ("id", "source", "source_sha256", "codec_id", "model", "model_sha256")}
            for row in original["codecs"]]}
        if comparable != expected:
            raise ValueError("Release tag source is not the qualified catalog")
        try:
            ref = gh.repo("GET", "git/ref/tags/" + record["tag"])
            if ref["object"]["type"] != "commit" or ref["object"]["sha"] != record["source_commit"]:
                raise ValueError("Existing tag points to different source; never overwrite")
        except APIError as error:
            if error.status != 404:
                raise
            gh.repo("POST", "git/refs", {"ref": "refs/tags/" + record["tag"], "sha": record["source_commit"]})
        try:
            release = gh.repo("GET", "releases/tags/" + record["tag"])
        except APIError as error:
            if error.status != 404:
                raise
            # Drafts are not consistently returned by the tag endpoint; look them
            # up before creating another release after a partially completed upload.
            drafts = gh.repo("GET", "releases?per_page=100")
            release = next((item for item in drafts if item["tag_name"] == record["tag"]), None)
            if release is None:
                release = gh.repo("POST", "releases", {"tag_name": record["tag"], "target_commitish": record["source_commit"],
                    "name": "Algomini " + record["request_id"], "draft": True, "body": (directory / "report.md").read_text()})
        assets = {row["name"]: row for row in gh.repo("GET", f"releases/{release['id']}/assets?per_page=100")}
        for name in ("algomini-linux-x86_64", "report.md", "registry.json", "manifest.json", "SHA256SUMS"):
            path = directory / name
            if name in assets:
                if assets[name].get("digest") != "sha256:" + sha256(path):
                    raise ValueError("Existing release asset differs or lacks a verifiable digest; never overwrite")
            else:
                uploaded = upload(gh, release, path)
                if uploaded.get("digest") != "sha256:" + sha256(path):
                    raise ValueError("Uploaded release asset digest mismatch")
        if release["draft"]:
            release = gh.repo("PATCH", f"releases/{release['id']}", {"draft": False})
        store = coordinator.state(library, gh)
        row = store.value["requests"].get(record["request_id"])
        if row:
            row.update(status="released", release_url=release["html_url"])
            store.save("Publish release for " + record["request_id"])
            command = f"CARGO_NET_GIT_FETCH_WITH_CLI=true cargo install --git https://github.com/{gh.repository} --tag {record['tag']} algomini-cli --locked --force"
            gh.comment(record["issue"], coordinator.status_comment(row) + "\n\n```bash\n" + command + "\n```\nThis repository is private; installation needs Git access. No model credentials are needed by the compressor.")
