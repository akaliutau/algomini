"""Token-bearing GitHub operations; never execute candidate code here."""

import copy
import json
import os
from pathlib import Path

from ..common import digest, sha256, write_json
from . import contracts, native
from .presentation import report, status_comment
from .github import APIError, GitHub, State, now


def historical(root):
    paths = [Path(root) / "reports/budget/ledger.json", Path(root) / "reports/research-budget/ledger.json"]
    return sum(row["charged_usd"] for path in paths if path.exists() for row in json.loads(path.read_text()))


def state(root, gh):
    history = Path(root) / "cloud/history.json"
    known = json.loads(history.read_text())["known_tests"] if history.is_file() else {}
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
    gh.repo("PATCH", f"issues/{issue['number']}", {"title": f"[codec] Develop plugin: {name}"})
    write_json(destination, {"issue": issue, "mode": mode})
    output(start=True, issue=issue["number"], mode=mode, base=gh.head("main"))


def claim(root, intake, image, destination, gh):
    try:
        request = contracts.snapshot(root, intake["issue"], gh.repository, os.environ.get("ALGOMINI_BASE_COMMIT", os.environ["GITHUB_SHA"]), image,
                                     staging=Path(destination).parent)
    except (ValueError, OSError) as error:
        gh.comment(intake["issue"]["number"], "Dataset preparation stopped before inference or a spending reservation:\n\n" + str(error))
        output(claimed=False)
        return
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
    write_json(Path(destination).with_name("prior-observations.json"), store.value.get("development_evidence", [])[-128:])
    gh.comment(request["issue"], status_comment(row))
    output(claimed=True, archive=bool(request["dataset"]["train"].get("archive")))


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
        identifier = "cx-" + selected["source_sha256"][:32] + "-" + selected["model_sha256"][:16]
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
    names += [name for name in ("sampling.json", "development-signatures.json", "attempts.json", "preparation.json", "dataset-manifest.json") if (workspace / name).is_file()]
    if final:
        names += ["final.json", "terminal.json"]
    files = {prefix + name: (workspace / name).read_bytes() for name in names}
    audit = workspace / "attempts.json"
    if audit.is_file():
        for name, checksum in json.loads(audit.read_text())["files"].items():
            relative = Path(name)
            path = workspace / relative
            if (len(relative.parts) != 3 or relative.parts[0] != "attempts" or not contracts.HASH.fullmatch(relative.parts[1])
                    or relative.name not in {"codec.rs", "generation.json", "model.bin"}):
                continue  # Verbose redacted CLI tails stay in Actions artifacts.
            if path.is_symlink() or not path.resolve().is_relative_to(workspace.resolve()) or path.stat().st_size > 65536 or sha256(path) != checksum:
                raise ValueError("Attempt evidence checksum/path mismatch")
            files[prefix + name] = path.read_bytes()
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
    research = json.loads((Path(workspace) / "research.json").read_text())
    if not development_ready(research, request):
        write_json(receipt_path, {"request_id": request["id"], "verify": False, "reason": "No qualifying development gain; final file remains unopened"})
        output(verify=False)
        return
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


def development_ready(research, request):
    return all(result and result.get("eligible") and result["candidate"]["archive_bytes"] < result["baselines"]["best_bytes"] * (1 - request["policy"]["release_minimum_archive_gain"])
               for result in (research.get("validation"), research.get("development")))


def development_evidence(request, research):
    rows = []
    for index, trial in enumerate(research.get("trials", [])):
        candidate = trial["result"]["candidate"]
        rows.append({"id": f"{request['id']}/trial-{index}", "tags": [request["dataset"]["family"]], "split": "validation",
            "mechanism": trial["origin"], "outcome": "win" if trial["result"]["size_first_win"] else "no-win",
            "measurements": {key: candidate.get(key) for key in ("archive_bytes", "model_bytes", "payload_bytes", "encoding_mode")},
            "interpretation": "Development evidence only; applicability must be remeasured", "evidence": f"reports/campaigns/{request['id']}/research.json"})
    for index, failure in enumerate(research.get("failures", [])):
        rows.append({"id": f"{request['id']}/failure-{index}", "tags": [request["dataset"]["family"]], "split": "validation",
                     "outcome": "failed", "failure": failure["failure"][:1000]})
    return rows


def remember(store, request, research):
    records = {entry["id"]: entry for entry in store.value.get("development_evidence", [])}
    records.update({entry["id"]: entry for entry in development_evidence(request, research)})
    store.value["development_evidence"] = list(records.values())[-128:]


def publish(root, workspace, issue_number, gh):
    store = state(root, gh)
    identifier = f"issue-{issue_number}"
    row = store.value["requests"].get(identifier)
    if not row:
        gh.comment(issue_number, "Cloud setup/intake failed before a durable spending reservation. Check the Actions run; no research was authorized.")
        return
    if row.get("pr_url") or row.get("release_url") or row.get("status") == "no-development-gain":
        gh.comment(issue_number, status_comment(row))
        return
    request = row["snapshot"]
    workspace = Path(workspace)
    if (workspace / "freeze.json").is_file() and (workspace / "research.json").is_file():
        research = json.loads((workspace / "research.json").read_text())
        if not row.get("test_consumed_at") and not development_ready(research, request):
            checked_checkpoint(root, workspace, request)
            amount = research["charged_usd"]
            if not 0 <= amount <= request["policy"]["request_reservation_usd"]:
                raise ValueError("Invalid development spending receipt")
            row.update(status="no-development-gain", terminal=True, charged_usd=amount, report=report(request, research))
            files = review_files(root, workspace, request)
            files.pop("release/registry.json")
            prefix = f"reports/campaigns/{identifier}/"
            files[prefix + "report.md"] = row["report"].encode()
            files[prefix + "observations.jsonl"] = "".join(json.dumps(entry, sort_keys=True) + "\n" for entry in development_evidence(request, research)).encode()
            branch = "codec/" + identifier
            row["report_commit"] = gh.commit(branch, gh.head(branch) or request["base_commit"], files, "Record development stop for " + identifier, tree_base=gh.head(branch) or request["base_commit"])
            remember(store, request, research)
            store.save("Stop before final access for " + identifier)
            gh.comment(issue_number, status_comment(row))
            return
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
    expected_receipt = {"request_id": identifier, "freeze_id": row["freeze_id"], "test_consumed_at": row["test_consumed_at"],
                        "frozen_commit": row["frozen_commit"], "test_previously_used": row["test_previously_used"], "execution": row["test_execution"]}
    if terminal["receipt"] != expected_receipt:
        raise ValueError("Final execution receipt differs from the durable test claim")
    amount = research["charged_usd"]
    if not isinstance(amount, (int, float)) or not 0 <= amount <= request["policy"]["request_reservation_usd"]:
        raise ValueError("Research usage exceeds its reservation")
    row.update(status="reported", terminal=True, charged_usd=amount, report=report(request, research, final))
    remember(store, request, research)
    branch = "codec/" + identifier
    files = review_files(root, workspace, request, final=True)
    prefix = f"reports/campaigns/{identifier}/"
    files[prefix + "report.md"] = row["report"].encode()
    files[prefix + "receipt.json"] = (json.dumps(terminal["receipt"], indent=2, sort_keys=True) + "\n").encode()
    files[prefix + "observations.jsonl"] = "".join(json.dumps(entry, sort_keys=True) + "\n" for entry in development_evidence(request, research)).encode()
    if final.get("size_first_win"):
        if final["candidate"]["archive_bytes"] >= final["baselines"]["best_bytes"] or not final["eligible"] or final["input_sha256"] != request["dataset"]["test"]["sha256"]:
            raise ValueError("Invalid release qualification")
        registry = json.loads(files["release/registry.json"])
        entry = {"id": identifier, "issue": issue_number, "freeze_id": freeze_id, "qualification": prefix + "final.json",
                 "freeze": prefix + "freeze.json", "request": prefix + "request.json", "receipt": prefix + "receipt.json", "selected": research["selected"]["id"], "image": request["image"]}
        registry["requests"] = [*registry.get("requests", []), entry]
        files["release/registry.json"] = (json.dumps(registry, indent=2, sort_keys=True) + "\n").encode()
    else:
        # Failed final measurements preserve reports but never propose a release registry update.
        files.pop("release/registry.json")
    head = gh.head(branch)
    saved = gh.commit(branch, head, files, "Record terminal evidence for " + identifier, tree_base=head)
    row["report_commit"] = saved
    if final.get("size_first_win"):
        owner = request["repository"].split("/")[0]
        existing = gh.repo("GET", f"pulls?state=all&head={owner}:{branch}")
        reused = research["origin"].startswith("reused")
        title = f"[codec] {'Qualify' if reused else 'Develop'} plugin: {request['plugin']} (#{issue_number})"
        pr = existing[0] if existing else gh.repo("POST", "pulls", {"title": title, "head": branch, "base": "main",
            "body": f"{row['report']}\n\nRelated to #{issue_number}.\n\nSource/model and the release client were frozen before the single final measurement. No model calls occur during PR checks or release."})
        row.update(status="pr-open", pr_url=pr["html_url"], pr_number=pr["number"])
    else:
        row["status"] = "failed-benchmark"
    store.save("Publish terminal outcome for " + identifier)
    gh.comment(issue_number, status_comment(row))
