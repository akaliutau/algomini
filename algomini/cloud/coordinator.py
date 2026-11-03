"""Token-bearing GitHub operations; never execute candidate code here."""

import copy
import json
import os
from pathlib import Path

from ..common import digest, sha256, write_json
from . import contracts, native
from .github import APIError, GitHub, State, now


def historical(root):
    paths = [Path(root) / "reports/budget/ledger.json", Path(root) / "reports/research-budget/ledger.json"]
    return sum(row["charged_usd"] for path in paths if path.exists() for row in json.loads(path.read_text()))


def state(root, gh):
    known = {}
    def collect(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "input_sha256" and isinstance(item, str) and contracts.HASH.fullmatch(item):
                    known[item] = ["historical-local-evidence"]
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)
    for relative in ("reports/stage5/result.json", "reports/wikipedia-search/verification/result.json", "artifacts/wikipedia-selected/development.json"):
        path = Path(root) / relative
        if path.is_file():
            collect(json.loads(path.read_text()))
    return State(gh, contracts.policy(root)["state_branch"], historical(root), known_tests=known)


def output(**values):
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a") as stream:
            for key, value in values.items():
                text = str(value).lower() if isinstance(value, bool) else str(value)
                if "\n" in text or "\r" in text:
                    raise ValueError("Invalid workflow output")
                stream.write(f"{key}={text}\n")


def authorized(gh, actor):
    try:
        permission = gh.repo("GET", "collaborators/" + actor + "/permission")["permission"]
        return permission in {"write", "maintain", "admin"}
    except APIError as error:
        if error.status == 404:
            return False
        raise


def gate(root, event, event_name, destination, gh):
    rules = contracts.policy(root)
    if event["repository"]["full_name"] != rules["repository"]:
        raise ValueError("Unexpected workflow repository")
    if event_name == "workflow_dispatch":
        issue = gh.repo("GET", f"issues/{int(event['inputs']['issue'])}")
        mode = event["inputs"].get("mode", "generate")
    else:
        issue = event["issue"]
        mode = "generate"
        labels = {row["name"] for row in issue["labels"]}
        if (event.get("action") not in {"opened", "labeled"} or "codec-request" not in labels
                or event["action"] == "labeled" and event.get("label", {}).get("name") != "codec-run"):
            output(start=False)
            return
    if "pull_request" in issue or mode not in {"generate", "reuse"}:
        raise ValueError("Expected a codec issue and supported execution mode")
    actor = event["sender"]["login"]
    if not authorized(gh, actor):
        gh.comment(issue["number"], "Waiting for approval: a maintainer with repository write access can add `codec-run`. No model calls or spending reservations were made.")
        output(start=False, issue=issue["number"])
        return
    try:
        name, url = contracts.issue_fields(issue["body"])
        contracts.public_url(url, rules)
    except ValueError as error:
        gh.comment(issue["number"], "Request rejected before inference: " + str(error))
        output(start=False, issue=issue["number"])
        return
    current = state(root, gh)
    existing = current.value["requests"].get(f"issue-{issue['number']}")
    if existing:
        previous = existing["snapshot"]
        if previous["plugin"] != name or previous["dataset_manifest_url"] != url:
            gh.comment(issue["number"], "This issue is already claimed with different fields. Its inputs are immutable; create a new issue for changed work.")
        else:
            if not existing["terminal"]:
                run = existing.get("run_id")
                read_token = os.environ.get("GH_ACTIONS_TOKEN")
                if run and read_token:
                    details = GitHub(gh.repository, read_token).repo("GET", f"actions/runs/{run}")
                    interrupted = details["status"] == "completed" or int(os.environ.get("GITHUB_RUN_ATTEMPT", "1")) > existing.get("run_attempt", 1)
                    if interrupted:
                        existing.update(status="interrupted", terminal=True, interrupted_at=now())
                        current.save("Retain interrupted reservation for " + previous["id"])
            gh.comment(issue["number"], status_comment(existing))
        output(start=False, issue=issue["number"])
        return
    write_json(destination, {"issue": issue, "mode": mode})
    output(start=True, issue=issue["number"], mode=mode, base=os.environ["GITHUB_SHA"])


def claim(root, intake, image, destination, gh):
    request = contracts.snapshot(root, intake["issue"], gh.repository, os.environ["GITHUB_SHA"], image)
    request["mode"] = intake["mode"]
    request["execution"] = {"run_id": os.environ["GITHUB_RUN_ID"], "run_attempt": int(os.environ.get("GITHUB_RUN_ATTEMPT", "1"))}
    store = state(root, gh)
    run_id = os.environ["GITHUB_RUN_ID"]
    if not store.reserve(request, f"https://github.com/{gh.repository}/actions/runs/{run_id}"):
        output(claimed=False)
        return
    row = store.value["requests"][request["id"]]
    row.update(run_id=run_id, run_attempt=int(os.environ.get("GITHUB_RUN_ATTEMPT", "1")), status="researching")
    store.save("Start claimed " + request["id"])
    write_json(destination, request)
    gh.comment(request["issue"], status_comment(row))
    output(claimed=True)


def checked_checkpoint(root, workspace, request):
    contracts.validate_snapshot(request, root)
    workspace = Path(workspace)
    frozen = json.loads((workspace / "freeze.json").read_text())
    if frozen["request_sha256"] != sha256(workspace / "request.json"):
        raise ValueError("Checkpoint request checksum mismatch")
    runtime = frozen["runtime"]
    trusted = [Path(root) / name for name in ("run.toml", "cloud/policy.json")]
    trusted += sorted((Path(root) / "algomini").glob("*.py")) + sorted((Path(root) / "algomini/cloud").glob("*.py"))
    if (runtime["image"] != request["image"]
            or runtime["trusted_sources"] != {str(path.relative_to(root)): sha256(path) for path in trusted}
            or runtime["driver"] != {str(path.relative_to(root)): sha256(path) for path in native.driver_files(root)}
            or runtime["adapter_sha256"] != sha256(Path(root) / "algomini/cloud/native.py")):
        raise ValueError("Checkpoint release driver/image differs from trusted checkout")
    for name, checksum in frozen["files"].items():
        relative = Path(name)
        path = workspace / relative
        if (relative.is_absolute() or ".." in relative.parts or path.is_symlink() or not path.resolve().is_relative_to(workspace.resolve())
                or not path.is_file() or path.stat().st_size > 6 * 1024**2 or sha256(path) != checksum):
            raise ValueError("Checkpoint file path, size or checksum mismatch")
    registry = json.loads((workspace / "registry.json").read_text())
    selected = frozen["selected"]
    base = json.loads((Path(root) / "release/registry.json").read_text())
    expected = copy.deepcopy(base)
    previous = next((item for item in base["codecs"] if item["id"] == selected["id"]), None)
    if previous:
        if selected != previous:
            raise ValueError("A reused codec version was modified")
    else:
        identifier = "cx-" + selected["source_sha256"][:16] + "-" + selected["model_sha256"][:8]
        if (selected["id"] != identifier or selected["codec_id"] != selected["source_sha256"][:32]
                or selected["source"] != f"plugins/{identifier}/codec.rs" or selected["model"] != f"plugins/{identifier}/model.bin"):
            raise ValueError("Invalid immutable codec version")
        expected["codecs"].append(selected)
    expected["aliases"][request["plugin"]] = selected["id"]
    if registry != expected:
        raise ValueError("Research attempted to change unrelated codecs or release aliases")
    native.assets(workspace, registry)
    return frozen, sha256(workspace / "freeze.json")


def review_files(root, workspace, request, *, final=False):
    workspace = Path(workspace)
    frozen, _ = checked_checkpoint(root, workspace, request)
    selected = frozen["selected"]
    prefix = f"reports/campaigns/{request['id']}/"
    names = ["request.json", "freeze.json", "research.json", "development.json", "build.json", "dictionary.json", "cloud-runtime.json"]
    if final:
        names += ["final.json", "terminal.json"]
    files = {prefix + name: (workspace / name).read_bytes() for name in names}
    for name in (selected["source"], selected["model"]):
        files[name] = (workspace / name).read_bytes()
    files["release/registry.json"] = (workspace / "registry.json").read_bytes()
    return files


def freeze(root, workspace, request, receipt_path, gh):
    _, identifier = checked_checkpoint(root, workspace, request)
    store = state(root, gh)
    row = store.value["requests"][request["id"]]
    if row["snapshot"] != request:
        raise ValueError("Remote request differs from checkpoint")
    branch = "codec/" + request["id"]
    if row.get("test_consumed_at"):
        write_json(receipt_path, {"request_id": request["id"], "freeze_id": row["freeze_id"], "test_consumed_at": row["test_consumed_at"], "frozen_commit": row["frozen_commit"], "test_previously_used": row["test_previously_used"], "execution": row["test_execution"]})
        output(verify=False)
        return
    if not row.get("frozen_commit"):
        if gh.head(branch):
            saved = gh.file(f"reports/campaigns/{request['id']}/freeze.json", branch)
            if digest(saved) != identifier:
                raise ValueError("Existing development branch has a different freeze")
            commit = gh.head(branch)
        else:
            commit = gh.commit(branch, request["base_commit"], review_files(root, workspace, request), "Freeze " + request["id"] + " before final test", tree_base=request["base_commit"])
        row.update(status="frozen", frozen_commit=commit, freeze_id=identifier)
        store.save("Pin selected source/build for " + request["id"])
    row["test_execution"] = {"run_id": os.environ["GITHUB_RUN_ID"], "run_attempt": int(os.environ.get("GITHUB_RUN_ATTEMPT", "1"))}
    store.consume(request["id"], identifier)
    row = store.value["requests"][request["id"]]
    write_json(receipt_path, {"request_id": request["id"], "freeze_id": identifier, "test_consumed_at": row["test_consumed_at"], "frozen_commit": row["frozen_commit"], "test_previously_used": row["test_previously_used"], "execution": row["test_execution"]})
    gh.comment(request["issue"], status_comment(row))
    output(verify=True)


def status_comment(row):
    request = row["snapshot"]
    text = f"**Algomini: {row['status']}** — `{request['id']}`, plugin `{request['plugin']}`.\n\n"
    text += f"[Actions run]({row['run_url']}). Reserved/charged exposure: ${row['charged_usd']:.4f}. Size first; speed secondary."
    if row.get("pr_url"):
        text += f"\n\n[Review PR]({row['pr_url']}). Closing this issue is optional."
    if row.get("release_url"):
        text += f"\n\n[Release]({row['release_url']})."
    if row["status"] == "interrupted":
        text += "\n\nThe previous run stopped without a complete terminal publication. Reservations and test claims are retained; no automatic paid retry is allowed."
    if row.get("report"):
        text += "\n\n" + row["report"]
    return text


def table(result, scope):
    candidate = result.get("candidate")
    if not candidate:
        return f"{scope}: no completed benchmark ({result.get('failure', 'unavailable')})."
    baseline = result["baselines"]
    rows = [candidate, *baseline["results"]]
    lines = ["| Scope | Method | Archive bytes | Saving | Encode s | Decode MiB/s | RSS MiB | Exact |", "|---|---|---:|---:|---:|---:|---:|---|"]
    for row in rows:
        metrics = row["files"][0]
        saving = 1 - row["archive_bytes"] / baseline["best_bytes"]
        lines.append(f"| {scope} | `{row['id']}` | {row['archive_bytes']:,} | {saving:.2%} | {metrics['encode']['execution_seconds']:.3f} | {metrics['decode']['decode_mib_per_second']:.2f} | {metrics['decode']['peak_rss_kib']/1024:.2f} | yes |")
    return "\n".join(lines)


def report(request, research, final):
    text = f"# Codec request {request['id']}\n\nPlugin: `{request['plugin']}`. Mode: `{request.get('mode', 'generate')}`. Selected origin: {research['origin']}.\n\n"
    text += "Size ranks first; throughput is secondary. Hard correctness, memory and time limits remain mandatory.\n\n"
    text += table(research["development"], "Development") + "\n\n" + table(final, "Final")
    text += f"\n\nFinal size-first win: **{bool(final.get('size_first_win'))}**. The 10% target was {'met' if final.get('score',{}).get('archive_gain',0)>=.1 else 'not met'}. "
    text += f"Estimated/reserved model usage: ${research['charged_usd']:.6f}. Executable: {research['development']['candidate']['decoder_package_bytes']:,} bytes, separate from archive sizes. "
    text += f"One declared final file was used after source/model/client freezing; no tuning followed. Previously used test hash: **{final.get('test_previously_used', False)}**.\n"
    text += f"\nPinned cloud image: `{request['image']}`. Source: `{research['selected']['source_sha256']}`. Model: `{research['selected']['model_sha256']}`.\n"
    return text


def publish(root, workspace, issue_number, gh):
    store = state(root, gh)
    identifier = f"issue-{issue_number}"
    row = store.value["requests"].get(identifier)
    if not row:
        gh.comment(issue_number, "Cloud setup/intake failed before a durable spending reservation. Check the Actions run; no research was authorized.")
        return
    if row.get("pr_url") or row.get("release_url"):
        gh.comment(issue_number, status_comment(row))
        return
    request = row["snapshot"]
    workspace = Path(workspace)
    if not (workspace / "final.json").is_file() or not (workspace / "terminal.json").is_file():
        failure = workspace / "failure.json"
        if failure.is_file() and (workspace / "request.json").is_file() and json.loads((workspace / "request.json").read_text()) == request:
            value = json.loads(failure.read_text())
            amount = sum(entry["charged_usd"] for entry in value["usage"])
            if not 0 <= amount <= request["policy"]["request_reservation_usd"] or amount != value["charged_usd"]:
                raise ValueError("Invalid failed-research spending receipt")
            row.update(status="failed-research", terminal=True, charged_usd=amount, failure=value["failure"])
        else:
            row.update(status="interrupted", terminal=True)
        store.save("Retain incomplete request exposure for " + identifier)
        gh.comment(issue_number, status_comment(row))
        return
    _, freeze_id = checked_checkpoint(root, workspace, request)
    terminal = json.loads((workspace / "terminal.json").read_text())
    final = json.loads((workspace / "final.json").read_text())
    research = json.loads((workspace / "research.json").read_text())
    if (freeze_id != row["freeze_id"] or terminal["freeze_id"] != freeze_id or terminal["result_sha256"] != sha256(workspace / "final.json")
            or final["freeze_id"] != freeze_id or final["request_id"] != identifier):
        raise ValueError("Final evidence does not match the remotely consumed freeze")
    amount = research["charged_usd"]
    if not isinstance(amount, (int, float)) or not 0 <= amount <= request["policy"]["request_reservation_usd"]:
        raise ValueError("Research usage exceeds its reservation")
    row.update(status="reported", terminal=True, charged_usd=amount, report=report(request, research, final))
    branch = "codec/" + identifier
    files = review_files(root, workspace, request, final=True)
    prefix = f"reports/campaigns/{identifier}/"
    files[prefix + "report.md"] = row["report"].encode()
    files[prefix + "receipt.json"] = (json.dumps(terminal["receipt"], indent=2, sort_keys=True) + "\n").encode()
    if final.get("size_first_win"):
        if final["candidate"]["archive_bytes"] >= final["baselines"]["best_bytes"] or not final["eligible"] or final["input_sha256"] != request["dataset"]["test"]["sha256"]:
            raise ValueError("Invalid release qualification")
        registry = json.loads(files["release/registry.json"])
        entry = {"id": identifier, "issue": issue_number, "freeze_id": freeze_id, "qualification": prefix + "final.json",
                 "freeze": prefix + "freeze.json", "request": prefix + "request.json", "receipt": prefix + "receipt.json", "selected": research["selected"]["id"], "image": request["image"]}
        registry["requests"] = [*registry.get("requests", []), entry]
        files["release/registry.json"] = (json.dumps(registry, indent=2, sort_keys=True) + "\n").encode()
        observation = {"id": identifier + "/development", "tags": [request["dataset"]["family"]], "split": "validation",
            "hypothesis": "A bounded generated or reused lossless representation selected on development data",
            "measurements": {"archive_bytes": research["development"]["candidate"]["archive_bytes"], "archive_gain": research["development"]["score"]["archive_gain"]},
            "outcome": research["origin"], "evidence": prefix + "development.json"}
        files[prefix + "observations.jsonl"] = (json.dumps(observation, sort_keys=True) + "\n").encode()
    else:
        # Failed final measurements preserve reports but never propose a release registry update.
        files.pop("release/registry.json")
    head = gh.head(branch)
    saved = gh.commit(branch, head, files, "Record terminal evidence for " + identifier, tree_base=head)
    row["report_commit"] = saved
    if final.get("size_first_win"):
        owner = request["repository"].split("/")[0]
        existing = gh.repo("GET", f"pulls?state=all&head={owner}:{branch}")
        pr = existing[0] if existing else gh.repo("POST", "pulls", {"title": f"Add {request['plugin']} codec ({identifier})", "head": branch, "base": "main",
            "body": f"{row['report']}\n\nRelated to #{issue_number}.\n\nSource/model and the release client were frozen before the single final measurement. No model calls occur during PR checks or release."})
        row.update(status="pr-open", pr_url=pr["html_url"], pr_number=pr["number"])
    else:
        row["status"] = "failed-benchmark"
    store.save("Publish terminal outcome for " + identifier)
    gh.comment(issue_number, status_comment(row))
