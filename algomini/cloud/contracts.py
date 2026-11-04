"""Bounded datasets and content-hashed campaign identities."""

from dataclasses import dataclass
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from ..common import digest, sha256, write_json


SLUG = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
HASH = re.compile(r"[a-f0-9]{64}\Z")
COMMIT = re.compile(r"[a-f0-9]{40}\Z")
IMAGE = re.compile(r"ghcr\.io/akaliutau/algomini-lab@sha256:[a-f0-9]{64}\Z")


def reject_constant(value):
    raise ValueError("Non-finite JSON numbers are unsupported: " + value)


def policy(root):
    return json.loads((Path(root) / "cloud/policy.json").read_text(), parse_constant=reject_constant)


def public_url(url, rules):
    # Accept the link as supplied; there is no domain, commit, query or redirect policy.
    if not isinstance(url, str) or not url.strip():
        raise ValueError("Dataset URL must be nonempty text")
    return url


def download(url, rules, maximum, expected=None):
    from .downloads import fetch
    public_url(url, rules)
    data = fetch(url, maximum)
    if expected and digest(data) != expected:
        raise ValueError("Dataset download failed SHA-256 verification")
    return data


@dataclass(frozen=True)
class DatasetFile:
    url: str
    bytes: int
    sha256: str
    archive: dict | None = None

    @classmethod
    def parse(cls, value, rules):
        if not isinstance(value, dict) or set(value) not in ({"url", "bytes", "sha256"}, {"url", "bytes", "sha256", "archive"}):
            raise ValueError("Dataset file needs url, bytes, sha256 and optional ZIP source details")
        if type(value["bytes"]) is not int or not 0 <= value["bytes"] <= rules["maximum_input_bytes"]:
            raise ValueError(f"Dataset input must contain 0 bytes to {rules['maximum_input_bytes']//1024**2} MiB")
        if not isinstance(value["sha256"], str) or not HASH.fullmatch(value["sha256"]):
            raise ValueError("Dataset SHA-256 must contain 64 lowercase hexadecimal characters")
        archive = value.get("archive")
        if "archive" in value:
            from .datasets import archive_spec
            archive_spec(archive, value["bytes"], rules)
        return cls(public_url(value["url"], rules), value["bytes"], value["sha256"], archive)

    def fetch(self, rules, *, staged=None):
        if self.archive:
            if staged is None or not Path(staged).is_file() or Path(staged).is_symlink():
                raise ValueError("ZIP dataset needs its separately staged partition; research cannot fetch the original ZIP")
            with Path(staged).open("rb") as stream:
                data = stream.read(self.bytes + 1)
            if digest(data) != self.sha256:
                raise ValueError("Staged ZIP partition failed SHA-256 verification")
        else:
            data = download(self.url, rules, self.bytes, self.sha256)
        if len(data) != self.bytes:
            raise ValueError("Dataset byte count mismatch")
        return data


def manifest(raw, rules):
    value = json.loads(raw, parse_constant=reject_constant)
    if (not isinstance(value, dict) or value.get("schema_version") != 1
            or value.get("family") not in {"csv", "json", "xml", "text", "binary"}
            or set(value) != {"schema_version", "family", "provenance", "train", "test"}
            or not isinstance(value["provenance"], dict)
            or any(not isinstance(value["provenance"].get(k), str) or not value["provenance"][k].strip() for k in ("source", "license"))):
        raise ValueError("Unsupported dataset manifest; include family and source/license provenance")
    train, test = (DatasetFile.parse(value[key], rules) for key in ("train", "test"))
    if train.sha256 == test.sha256 or train.url == test.url and not (train.archive and test.archive):
        raise ValueError("Development and final-test files must differ")
    if train.archive or test.archive:
        if not (train.archive and test.archive and train.url == test.url and train.archive["sha256"] == test.archive["sha256"]):
            raise ValueError("ZIP partitions must share one immutable source archive")
        if {r["name"] for r in train.archive["members"]} & {r["name"] for r in test.archive["members"]}:
            raise ValueError("Development and final ZIP members must be disjoint")
    return value


def fetch_dataset(request, part, workspace):
    item = DatasetFile.parse(request["dataset"][part], request["policy"])
    if item.archive:
        return item.fetch(request["policy"], staged=Path(workspace) / "dataset" / (part + ".bin"))
    return item.fetch(request["policy"])


def issue_fields(body):
    if not isinstance(body, str) or len(body) > 8192:
        raise ValueError("Invalid issue form body")
    matches = re.findall(r"^### ([^\n]+)\n+(.*?)(?=^### |\Z)", body, flags=re.M | re.S)
    fields = {key.strip(): value.strip() for key, value in matches}
    label = "Dataset archive URL" if "Dataset archive URL" in fields else "Dataset manifest URL"
    if len(matches) != 2 or set(fields) != {"Plugin name", label}:
        raise ValueError("Use the codec issue form with its two required fields")
    if not SLUG.fullmatch(fields["Plugin name"]):
        raise ValueError("Plugin name must be a lowercase slug, at most 64 characters")
    return fields["Plugin name"], fields[label]


def snapshot(root, issue, repository, base_commit, image, *, staging=None):
    rules = policy(root)
    if repository != rules["repository"] or not COMMIT.fullmatch(base_commit) or not IMAGE.fullmatch(image):
        raise ValueError("Untrusted repository, base commit or cloud image")
    name, url = issue_fields(issue["body"])
    archive_request = re.search(r"^### Dataset archive URL\s*$", issue["body"], flags=re.M)
    if archive_request or urlsplit(url).path.lower().endswith(".zip"):
        from .datasets import prepare
        archive = download(url, rules, rules["maximum_archive_bytes"])
        dataset, payloads = prepare(archive, url, rules)
        raw = (json.dumps(dataset, indent=2, sort_keys=True) + "\n").encode()
        if len(raw) > rules["maximum_manifest_bytes"]:
            raise ValueError("Generated dataset manifest exceeds 32 KiB")
        manifest(raw, rules)
        if staging is not None:
            for part, data in payloads.items():
                path = Path(staging) / "dataset" / (part + ".bin")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
    else:
        raw = download(url, rules, rules["maximum_manifest_bytes"])
        dataset = manifest(raw, rules)
    if staging is not None:
        write_json(Path(staging) / "dataset-manifest.json", dataset)
    return {"schema_version": 1, "id": f"issue-{issue['number']}", "issue": issue["number"],
            "repository": repository, "plugin": name, "dataset_manifest_url": url,
            "dataset_manifest_sha256": digest(raw), "dataset": dataset, "base_commit": base_commit,
            "image": image, "policy": rules, "policy_sha256": sha256(Path(root) / "cloud/policy.json")}


def validate_snapshot(value, root):
    rules = policy(root)
    if (value["policy"] != rules or value.get("mode", "generate") not in {"generate", "reuse"}
            or value["policy_sha256"] != sha256(Path(root) / "cloud/policy.json")
            or value["repository"] != rules["repository"] or not COMMIT.fullmatch(value["base_commit"])
            or not IMAGE.fullmatch(value["image"]) or not SLUG.fullmatch(value["plugin"])
            or type(value["issue"]) is not int or value["issue"] < 1 or value["id"] != f"issue-{value['issue']}"):
        raise ValueError("Request snapshot/policy changed")
    manifest(json.dumps(value["dataset"]), rules)
    return value


def execution_guard(execution):
    """GitHub retries use a new attempt; an old claim never authorizes that retry."""
    if execution:
        import os
        if (str(execution["run_id"]) != os.environ.get("ALGOMINI_RUN_ID")
                or str(execution["run_attempt"]) != os.environ.get("ALGOMINI_RUN_ATTEMPT")):
            raise ValueError("Execution attempt differs from its durable claim; paid work/test access cannot retry")
