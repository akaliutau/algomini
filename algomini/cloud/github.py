"""Small GitHub API client and compare-and-swap Git state storage."""

import base64
from datetime import datetime, timezone
import json
import os
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def now():
    return datetime.now(timezone.utc).isoformat()


class APIError(RuntimeError):
    def __init__(self, status, operation):
        self.status = status
        super().__init__(f"GitHub {operation} failed (HTTP {status})")


class GitHub:
    def __init__(self, repository, token=None):
        if repository != "akaliutau/algomini":
            raise ValueError("Unexpected automation repository")
        self.repository = repository
        self.token = token if token is not None else os.environ["GH_TOKEN"]

    def call(self, method, path, value=None):
        raw = json.dumps(value).encode() if value is not None else None
        request = Request("https://api.github.com/" + path, data=raw, method=method,
            headers={"Authorization": "Bearer " + self.token, "Accept": "application/vnd.github+json",
                     "Content-Type": "application/json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "algomini-cloud/1"})
        try:
            with build_opener(NoRedirect()).open(request, timeout=60) as response:
                body = response.read(16 * 1024**2 + 1)
                if len(body) > 16 * 1024**2:
                    raise ValueError("GitHub response exceeds limit")
                return json.loads(body) if body else None
        except HTTPError as error:
            # Error bodies, URLs with credentials and request headers are never logged.
            raise APIError(error.code, method) from None

    def repo(self, method, suffix, value=None):
        return self.call(method, f"repos/{self.repository}/{suffix}", value)

    def file(self, path, ref):
        item = self.repo("GET", "contents/" + quote(path, safe="/") + "?ref=" + quote(ref, safe=""))
        if item.get("encoding") != "base64" or item.get("size", 0) > 2 * 1024**2:
            raise ValueError("GitHub file exceeds supported bounds")
        return base64.b64decode(item["content"], validate=False)

    def head(self, branch):
        try:
            return self.repo("GET", "git/ref/heads/" + quote(branch, safe="/"))["object"]["sha"]
        except APIError as error:
            if error.status == 404:
                return None
            raise

    def commit(self, branch, parent, files, message, *, tree_base=None):
        if not files or len(files) > 100:
            raise ValueError("Invalid commit dimensions")
        entries = []
        for path, data in sorted(files.items()):
            if path.startswith("/") or any(part in {"", ".", "..", ".git"} for part in path.split("/")) or len(data) > 2 * 1024**2:
                raise ValueError("Invalid exported Git path or file size")
            blob = self.repo("POST", "git/blobs", {"content": base64.b64encode(data).decode(), "encoding": "base64"})
            entries.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})
        tree = {"tree": entries}
        if tree_base:
            tree["base_tree"] = self.repo("GET", "git/commits/" + tree_base)["tree"]["sha"]
        made = self.repo("POST", "git/trees", tree)
        commit = self.repo("POST", "git/commits", {"message": message, "tree": made["sha"], "parents": [parent] if parent else []})
        if self.head(branch) is None:
            self.repo("POST", "git/refs", {"ref": "refs/heads/" + branch, "sha": commit["sha"]})
        else:
            # A concurrent sibling commit is not a fast-forward and is rejected.
            self.repo("PATCH", "git/refs/heads/" + quote(branch, safe="/"), {"sha": commit["sha"], "force": False})
        return commit["sha"]

    def comment(self, issue, body):
        marker = f"<!-- algomini:issue-{issue} -->"
        if len(body) > 60000:
            raise ValueError("Issue report exceeds limit")
        for page in range(1, 11):
            rows = self.repo("GET", f"issues/{issue}/comments?per_page=100&page={page}")
            for row in rows:
                if marker in row.get("body", "") and row.get("user", {}).get("type") == "Bot":
                    return self.repo("PATCH", f"issues/comments/{row['id']}", {"body": marker + "\n" + body})
            if len(rows) < 100:
                return self.repo("POST", f"issues/{issue}/comments", {"body": marker + "\n" + body})
        raise ValueError("Too many comments to locate the automation report")


class State:
    def __init__(self, github, branch, historical, *, known_tests=None):
        self.github, self.branch = github, branch
        self.parent = github.head(branch)
        self.value = json.loads(github.file("state.json", self.parent)) if self.parent else {
            "schema_version": 1, "historical_charged_usd": historical, "requests": {}, "tests": known_tests or {}}
        if self.value.get("schema_version") != 1:
            raise ValueError("Unsupported remote state")

    def save(self, message):
        raw = (json.dumps(self.value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()
        self.parent = self.github.commit(self.branch, self.parent, {"state.json": raw}, message)

    def reserve(self, request, run_url):
        identifier, rules = request["id"], request["policy"]
        if identifier in self.value["requests"]:
            return False
        exposure = self.value["historical_charged_usd"] + sum(row["charged_usd"] for row in self.value["requests"].values())
        if exposure + rules["request_reservation_usd"] > rules["provider_limit_usd"] - rules["account_reserve_usd"]:
            raise ValueError("Shared provider budget minus account reserve would be exceeded")
        self.value["requests"][identifier] = {"snapshot": request, "status": "reserved", "charged_usd": rules["request_reservation_usd"],
            "reserved_at": now(), "run_url": run_url, "terminal": False}
        self.save("Reserve " + identifier + " before paid generation")
        return True

    def consume(self, identifier, freeze_id):
        row = self.value["requests"][identifier]
        if row.get("test_consumed_at"):
            raise ValueError("Final test already consumed; never retry")
        checksum = row["snapshot"]["dataset"]["test"]["sha256"]
        previous = self.value["tests"].get(checksum, [])
        row.update(status="test-consumed", freeze_id=freeze_id, test_consumed_at=now(), test_previously_used=bool(previous))
        self.value["tests"][checksum] = [*previous, identifier]
        self.save("Consume " + identifier + " test before dataset access")
