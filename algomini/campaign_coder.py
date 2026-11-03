"""Bounded generation in a new workspace; the completed telemetry campaign stays closed."""

import copy
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from . import coder
from .common import digest, sha256, worker_environment, write_json


def account(library, identifier, campaign, reservation, *, settlement=None):
    directory = library / "reports/research-budget"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "ledger.json"
    with (directory / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        entries = json.loads(path.read_text()) if path.exists() else []
        previous = next((row for row in entries if row["id"] == identifier), None)
        if settlement is not None:
            if previous is None:
                raise ValueError("No campaign reservation exists")
            previous.update(settlement)
        else:
            if previous is not None:
                raise ValueError("Request already reserved; inspect its completed or interrupted outcome")
            historical = json.loads((library / "reports/budget/ledger.json").read_text())
            if sum(row["charged_usd"] for row in historical + entries) + reservation > 130:
                raise ValueError("Shared $150 account limit minus $20 reserve would be exceeded")
            entries.append({"id": identifier, "campaign": campaign, "state": "reserved", "charged_usd": reservation})
        write_json(path, entries)


def generate(library, cfg, workspace, prompt, sample, policy, *, kind="initial"):
    library, workspace = Path(library), Path(workspace)
    campaign = json.loads((workspace / "campaign.json").read_text())["id"]
    local = copy.deepcopy(cfg)
    local["coder"]["executable"] = str((library / cfg["coder"]["executable"]).resolve())
    budget = local["budget"]
    budget.update(total_usd=policy["maximum_spending_usd"], discovery_usd=policy["maximum_spending_usd"],
                  framework_usd=0, refinement_usd=0, reserve_usd=0,
                  initial_candidates=policy["maximum_initial_candidates"], evolved_candidates=policy["maximum_evolved_candidates"],
                  repair_sessions=policy["maximum_repairs"])
    template = workspace / "rust/codec-worker"
    if not template.exists():
        shutil.copytree(library / "rust/codec-worker", template, ignore=shutil.ignore_patterns("target"))
    policy_path = workspace / "generation-policy.json"
    definition = {"policy": policy, "coder": local["coder"], "pricing": local["pricing"]}
    if policy_path.exists() and json.loads(policy_path.read_text()) != definition:
        raise ValueError("Campaign generation policy/model changed; use a new campaign")
    if not policy_path.exists():
        write_json(policy_path, definition)
    attempt, _, _ = coder.generation_request(workspace, local, prompt, sample, phase="discovery", kind=kind)
    record_path = workspace / ".cache/coder" / attempt / "generation.json"
    if record_path.exists():
        record = json.loads(record_path.read_text())
        source = record_path.parent / "workspace/codec.rs"
        if not record["ok"] or not source.is_file() or sha256(source) != record["source_sha256"]:
            raise ValueError("Cached campaign generation failed or changed")
        return source, record
    local_ledger = workspace / "reports/budget/ledger.json"
    if local_ledger.exists() and len(json.loads(local_ledger.read_text())) >= policy["maximum_coder_sessions"]:
        raise ValueError("Campaign coder-session cap reached")
    from dotenv import dotenv_values
    key = os.environ.get(local["coder"]["api_key_env"]) or dotenv_values(library / ".env", interpolate=False).get(local["coder"]["api_key_env"])
    if not key:
        raise ValueError("Model credential is missing")
    identifier = digest((campaign + attempt).encode())
    account(library, identifier, campaign, budget["attempt_reservation_usd"])
    request = workspace / ".cache/requests" / (attempt + ".json")
    write_json(request, {"workspace": str(workspace), "config": local, "prompt": prompt, "sample_hex": sample.hex(), "kind": kind})
    env = worker_environment()
    env[local["coder"]["api_key_env"]] = key
    completed = subprocess.run([sys.executable, "-m", "algomini.campaign_coder", str(request)], cwd=library,
                               env=env, capture_output=True, timeout=local["coder"]["timeout_seconds"] + 60)
    if record_path.exists():
        record = json.loads(record_path.read_text())
        usage = record["usage"]
        account(library, identifier, campaign, 0, settlement={"state": "completed" if record["ok"] else "failed",
            "charged_usd": usage["estimated_usd"] if usage["available"] else budget["attempt_reservation_usd"],
            "cost_is_estimate": usage["available"], "usage": usage})
    if completed.returncode:
        raise ValueError("Campaign generation failed: " + completed.stderr.decode(errors="replace").replace(key, "[REDACTED]")[-2000:])
    result = json.loads(completed.stdout)
    source = Path(result["source"])
    if sha256(source) != result["record"]["source_sha256"]:
        raise ValueError("Generated source checksum mismatch")
    return source, result["record"]


if __name__ == "__main__":
    request = json.loads(Path(sys.argv[1]).read_text())
    source, record = coder.generate(Path(request["workspace"]), request["config"], request["prompt"],
                                   bytes.fromhex(request["sample_hex"]), phase="discovery", kind=request["kind"])
    print(json.dumps({"source": str(source), "record": record}))
