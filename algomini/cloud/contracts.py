"""Bounded public request inputs and immutable campaign identities."""

from dataclasses import dataclass
import json
from pathlib import Path
import re
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from ..common import digest, sha256


SLUG = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
HASH = re.compile(r"[a-f0-9]{64}\Z")
COMMIT = re.compile(r"[a-f0-9]{40}\Z")
IMAGE = re.compile(r"ghcr\.io/akaliutau/algomini-lab@sha256:[a-f0-9]{64}\Z")


def reject_constant(value):
    raise ValueError("Non-finite JSON numbers are unsupported: " + value)


def policy(root):
    return json.loads((Path(root) / "cloud/policy.json").read_text(), parse_constant=reject_constant)


def public_url(url, rules):
    if not isinstance(url, str) or len(url) > 2048:
        raise ValueError("Invalid dataset URL")
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname not in rules["allowed_dataset_hosts"]
            or parsed.username or parsed.password or parsed.port not in (None, 443) or parsed.query or parsed.fragment):
        raise ValueError("Dataset URLs require an approved public HTTPS host without credentials/query/fragment")
    if parsed.hostname == "raw.githubusercontent.com":
        pieces = parsed.path.split("/")
        if len(pieces) < 5 or not COMMIT.fullmatch(pieces[3]):
            raise ValueError("GitHub dataset URLs must pin a full 40-character commit")
    return url


def download(url, rules, maximum, expected=None):
    public_url(url, rules)
    class Redirect(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            public_url(newurl, rules)
            return super().redirect_request(req, fp, code, msg, headers, newurl)
    with build_opener(Redirect()).open(Request(url, headers={"User-Agent": "algomini-cloud/1"}), timeout=60) as response:
        data = response.read(maximum + 1)
    if len(data) > maximum or expected and digest(data) != expected:
        raise ValueError("Dataset download exceeded its limit or failed SHA-256 verification")
    return data


@dataclass(frozen=True)
class DatasetFile:
    url: str
    bytes: int
    sha256: str

    @classmethod
    def parse(cls, value, rules):
        if not isinstance(value, dict) or set(value) != {"url", "bytes", "sha256"}:
            raise ValueError("Dataset file needs exactly url, bytes and sha256")
        if type(value["bytes"]) is not int or not 0 <= value["bytes"] <= rules["maximum_input_bytes"]:
            raise ValueError("Dataset input must contain 0 bytes to 2 MiB")
        if not isinstance(value["sha256"], str) or not HASH.fullmatch(value["sha256"]):
            raise ValueError("Dataset SHA-256 must contain 64 lowercase hexadecimal characters")
        return cls(public_url(value["url"], rules), value["bytes"], value["sha256"])

    def fetch(self, rules):
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
    if train.sha256 == test.sha256 or train.url == test.url:
        raise ValueError("Development and final-test files must differ")
    return value


def issue_fields(body):
    if not isinstance(body, str) or len(body) > 8192:
        raise ValueError("Invalid issue form body")
    matches = re.findall(r"^### ([^\n]+)\n+([^#]*?)(?=^### |\Z)", body, flags=re.M | re.S)
    fields = {key.strip(): value.strip() for key, value in matches}
    if len(matches) != 2 or set(fields) != {"Plugin name", "Dataset manifest URL"}:
        raise ValueError("Use the codec issue form with its two required fields")
    if not SLUG.fullmatch(fields["Plugin name"]):
        raise ValueError("Plugin name must be a lowercase slug, at most 64 characters")
    return fields["Plugin name"], fields["Dataset manifest URL"]


def snapshot(root, issue, repository, base_commit, image):
    rules = policy(root)
    if repository != rules["repository"] or not COMMIT.fullmatch(base_commit) or not IMAGE.fullmatch(image):
        raise ValueError("Untrusted repository, base commit or cloud image")
    name, url = issue_fields(issue["body"])
    raw = download(url, rules, rules["maximum_manifest_bytes"])
    dataset = manifest(raw, rules)
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
