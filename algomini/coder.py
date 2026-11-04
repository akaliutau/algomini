"""Configurable CLI coder: consume a brief, validate one file, record usage."""

from datetime import datetime, timezone
import copy
import fcntl
import json
import math
import os
from pathlib import Path
import subprocess

from .common import digest, sha256, worker_environment, write_json
from .execution import run_isolated


class BudgetExhausted(ValueError):
    """A new paid session would exceed its existing reservation boundary."""


def token_prices(cfg, model=None):
    model = model or cfg["coder"]["model"]
    rates = cfg["pricing"].get("models", {}).get(model)
    if rates is None:
        if model != cfg["pricing"].get("model", cfg["coder"]["model"]):
            raise ValueError("No explicit token pricing configured for model: " + model)
        rates = cfg["pricing"]
    result = {}
    for key in ("input_usd_per_million", "cached_input_usd_per_million", "output_usd_per_million"):
        value = rates[key]
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError("Token prices must be finite and nonnegative")
        result[key] = value
    return result


def session_configuration(cfg, kind):
    """Choose a coding profile with its own rates, without mutating the run policy."""
    profile = cfg["coder"].get("profiles", {}).get(kind, {})
    if not isinstance(profile, dict) or set(profile) - {"model", "reasoning_effort"}:
        raise ValueError("Coding profiles support only model and reasoning_effort")
    selected = copy.deepcopy(cfg)
    selected["coder"].update(profile)
    model = selected["coder"]["model"]
    selected["pricing"].update(token_prices(cfg, model))
    selected["pricing"]["model"] = model
    return selected


def budget_entry(root, cfg, attempt_id, settlement=None, *, phase="framework", kind="initial"):
    if phase not in ("framework", "discovery", "refinement") or kind not in ("initial", "evolved", "repair"):
        raise ValueError("Invalid generation budget phase/kind")
    directory = root / "reports/budget"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "ledger.json"
    with (directory / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        entries = json.loads(path.read_text()) if path.exists() else []
        existing = next((entry for entry in entries if entry["attempt_id"] == attempt_id), None)
        if settlement is not None:
            if existing is None:
                raise ValueError("No budget reservation exists for this attempt")
            existing.update(settlement)
        else:
            budget = cfg["budget"]
            if not budget["generation_enabled"] or not budget["provider_spending_cap_verified"]:
                raise ValueError("Paid generation requires the enabled gate and verified provider cap")
            if existing is not None:
                raise ValueError("This attempt has already been reserved; inspect its cached outcome")
            phase_entries = [entry for entry in entries if entry["phase"] == phase]
            if phase == "framework" and len(phase_entries) >= budget["stage1_max_attempts"]:
                raise ValueError("Stage 1 generation-attempt limit reached")
            if kind == "repair" and sum(entry.get("kind") == "repair" for entry in entries) >= budget["repair_sessions"]:
                raise ValueError("Research repair-session limit reached")
            cap = budget["evolved_candidates"] if kind == "evolved" else budget["initial_candidates"]
            if phase != "framework" and kind != "repair" and sum(entry.get("kind", "initial") == kind for entry in phase_entries) >= cap:
                raise ValueError("Research candidate-attempt limit reached")
            reserved = budget["attempt_reservation_usd"]
            charged = sum(entry["charged_usd"] for entry in entries)
            phase_charged = sum(entry["charged_usd"] for entry in phase_entries)
            if phase_charged + reserved > budget[f"{phase}_usd"] or charged + reserved > budget["total_usd"] - budget["reserve_usd"]:
                raise BudgetExhausted(f"{phase.title()} budget/reserve would be exceeded")
            entries.append({"attempt_id": attempt_id, "state": "reserved", "charged_usd": reserved,
                            "reservation_usd": reserved, "phase": phase, "kind": kind,
                            "model": cfg["coder"]["model"], "reasoning_effort": cfg["coder"].get("reasoning_effort", "high"),
                            "recorded_at": datetime.now(timezone.utc).isoformat()})
        write_json(path, entries)


def usage_from_events(events, cfg):
    summaries = [item["payload"] for item in events if item.get("payload_type") == "run.usage"]
    if not summaries:
        return {"available": False, "estimated_usd": None}
    summary = summaries[-1]
    if summary.get("cancelled_calls", 0) or summary.get("failed_calls", 0):
        return {"available": False, "estimated_usd": None, "calls": summary.get("calls"),
                "reason": "Incomplete provider calls; retain the full reservation"}
    models = summary.get("models", [])
    if not models or any(item.get("model") != cfg["coder"]["model"] for item in models):
        return {"available": False, "estimated_usd": None, "calls": summary.get("calls")}
    if any(any(type(item.get(key, 0)) is not int or item.get(key, 0) < 0 for key in ("input_tokens", "cached_tokens", "output_tokens"))
           or item.get("cached_tokens", 0) > item.get("input_tokens", 0) for item in models):
        return {"available": False, "estimated_usd": None, "reason": "Invalid token totals; retain the reservation"}
    input_tokens = sum(item.get("input_tokens", 0) for item in models)
    cached_tokens = sum(item.get("cached_tokens", 0) for item in models)
    output_tokens = sum(item.get("output_tokens", 0) for item in models)
    try:
        prices = token_prices(cfg)
    except ValueError:
        return {"available": False, "estimated_usd": None, "reason": "No valid prices for this model; retain the reservation"}
    estimated = ((input_tokens - cached_tokens) * prices["input_usd_per_million"]
                 + cached_tokens * prices["cached_input_usd_per_million"]
                 + output_tokens * prices["output_usd_per_million"]) / 1_000_000
    if not math.isfinite(estimated) or estimated < 0:
        return {"available": False, "estimated_usd": None, "reason": "Invalid price/usage estimate; retain the reservation"}
    return {"available": True, "input_tokens": input_tokens, "cached_input_tokens": cached_tokens,
            "output_tokens": output_tokens, "calls": summary.get("calls"),
            "estimated_usd": round(estimated, 8), "cost_source": "CLI token totals and declared standard-mode prices"}




def generation_request(root, cfg, prompt, sample, *, attempt=0, phase="framework", kind="initial"):
    coder = cfg["coder"]
    excerpt = sample[:min(16384, coder.get("excerpt_bytes", 12288))]
    if len(sample) > len(excerpt) and b"\n" in excerpt and not excerpt.endswith(b"\n"):
        excerpt = excerpt.rsplit(b"\n", 1)[0] + b"\n"
    definition = {"prompt": prompt, "sample_sha256": digest(excerpt), "training_sha256": digest(sample), "model": coder["model"],
                  "adapter_sha256": sha256(__file__), "api_sha256": sha256(coder["api_source"]),
                  "command": coder["command"], "executable_sha256": sha256(root / coder["executable"]),
                  "max_model_steps": coder["max_model_steps"], "timeout_seconds": coder["timeout_seconds"],
                  "reasoning_effort": coder.get("reasoning_effort", "high"), "pricing": cfg["pricing"],
                  "attempt": attempt}
    definition.update(budget_phase=phase, candidate_kind=kind)
    attempt_id = digest(json.dumps(definition, sort_keys=True).encode())
    return attempt_id, excerpt, definition


def generate(root, cfg, prompt, sample, *, attempt=0, phase="framework", kind="initial"):
    from dotenv import dotenv_values

    coder = cfg["coder"]
    attempt_id, excerpt, definition = generation_request(root, cfg, prompt, sample, attempt=attempt, phase=phase, kind=kind)
    directory = root / ".cache/coder" / attempt_id
    work = directory / "workspace"
    source = work / "codec.rs"
    record_path = directory / "generation.json"
    if record_path.exists():
        record = json.loads(record_path.read_text())
        if record["ok"] and source.is_file() and not source.is_symlink() and sha256(source) == record["source_sha256"]:
            return source, record
        raise ValueError("Cached generation failed: " + record.get("failure", "artifact missing or modified"))
    if (root / "reports/stage5/consumed.json").exists():
        raise ValueError("Sealed evaluation is consumed; new candidate generation is disabled for this experiment")
    if not cfg["budget"]["generation_enabled"] or not cfg["budget"]["provider_spending_cap_verified"]:
        raise ValueError("Paid generation is disabled or the provider cap is unverified")
    key = os.environ.get(coder["api_key_env"]) or dotenv_values(root / ".env", interpolate=False).get(coder["api_key_env"])
    if not key:
        raise ValueError("Configured model credential is missing")
    work.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o700)
    subprocess.run(["git", "init", "--quiet", str(work)], check=True, capture_output=True)
    (work / "prompt.txt").write_text(prompt)
    (work / "sample.bin").write_bytes(excerpt)
    (work / "api.rs").write_bytes(Path(coder["api_source"]).read_bytes())
    protected = {name: sha256(work / name) for name in ("prompt.txt", "sample.bin", "api.rs")}
    env = worker_environment()
    # Preserve the actual user-home identity required by native file mediation.
    # Landlock still denies its file contents; XDG state stays in the job tree.
    env["HOME"] = os.environ.get("HOME", str(Path.home()))
    env[coder["api_key_env"]] = key
    if coder.get("trace_transport"):
        env["MUSE_TRANSPORT_TRACE"] = "1"
    temporary = work / ".tmp"
    temporary.mkdir(exist_ok=True)
    env["TMPDIR"] = str(temporary)
    for name in ("CONFIG", "CACHE", "DATA", "STATE"):
        value = work / (".xdg-" + name.lower())
        value.mkdir(exist_ok=True)
        env["XDG_" + name + "_HOME"] = str(value)
    substitutions = {"executable": str(root / coder["executable"]), "model": coder["model"],
                     "reasoning_effort": coder.get("reasoning_effort", "high"),
                     "max_model_steps": str(coder["max_model_steps"]), "prompt_file": str(work / "prompt.txt"),
                     "workspace": str(work)}
    argv = [part.format_map(substitutions) for part in coder["command"]]
    budget_entry(root, cfg, attempt_id, phase=phase, kind=kind)
    result = run_isolated(argv, cwd=work,
                          ro=[root / coder["executable"], "/etc/resolv.conf", "/etc/hosts", "/etc/nsswitch.conf", "/etc/ssl/certs"],
                          rw=[work], env=env, timeout=coder["timeout_seconds"], memory_mib=2048,
                          output_bytes=8 * 1024**2, mode="coder", network=True)
    output = result["stdout"].decode(errors="replace").replace(key, "[REDACTED]")
    error = result["stderr"].decode(errors="replace").replace(key, "[REDACTED]")
    (directory / "events.jsonl").write_text(output)
    (directory / "stderr.txt").write_text(error)
    events = []
    for line in output.splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    usage = usage_from_events(events, cfg)
    artifact_ok = source.is_file() and not source.is_symlink() and 0 < source.stat().st_size <= 65536
    protected_ok = all((work / name).is_file() and sha256(work / name) == checksum for name, checksum in protected.items())
    record = {"attempt_id": attempt_id, "attempt": attempt, "model": coder["model"], "phase": phase, "kind": kind,
              "reasoning_effort": coder.get("reasoning_effort", "high"),
              "prompt_sha256": digest(prompt.encode()), "sample_sha256": digest(excerpt),
              "benchmark_sample_sha256": digest(sample), "excerpt_bytes": len(excerpt),
              "executable_sha256": definition["executable_sha256"],
              "ok": artifact_ok and protected_ok and not result["timed_out"],
              "cli_turn_completed": result["returncode"] == 0,
              "exit_code": result["returncode"], "timed_out": result["timed_out"],
              "artifact_valid": artifact_ok, "protected_inputs_unchanged": protected_ok,
              "wall_seconds": result["wall_seconds"], "isolation": result["isolation"], "usage": usage}
    if artifact_ok:
        record["source_sha256"] = sha256(source)
    if not record["ok"]:
        status = "\n".join(line for line in error.splitlines() if line.startswith(("muse:", "run ended", "Error:", "error:")))
        denied = [item.get("payload", {}).get("text", "") for item in events
                  if item.get("payload_type") == "tool.result" and item.get("payload", {}).get("text", "").startswith("tool denied:")]
        record["failure"] = ("; ".join(denied[:2]) + "\n" + status).strip()[-1500:] or "CLI did not complete with a valid, independent source artifact"
    write_json(record_path, record)
    budget_entry(root, cfg, attempt_id, {"state": "completed" if record["ok"] else "failed",
                 "charged_usd": usage["estimated_usd"] if usage["available"] else cfg["budget"]["attempt_reservation_usd"],
                 "cost_is_estimate": usage["available"], "usage": usage}, phase=phase, kind=kind)
    if not record["ok"]:
        raise ValueError("Coder generation failed: " + record["failure"])
    return source, record
