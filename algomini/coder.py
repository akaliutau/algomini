"""Configurable CLI coder: consume a brief, validate one file, record usage."""

from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import subprocess

from .common import digest, sha256, worker_environment, write_json
from .execution import run_isolated


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
                raise ValueError(f"{phase.title()} budget/reserve would be exceeded")
            entries.append({"attempt_id": attempt_id, "state": "reserved", "charged_usd": reserved,
                            "reservation_usd": reserved, "phase": phase, "kind": kind,
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
    input_tokens = sum(item.get("input_tokens", 0) for item in models)
    cached_tokens = sum(item.get("cached_tokens", 0) for item in models)
    output_tokens = sum(item.get("output_tokens", 0) for item in models)
    prices = cfg["pricing"]
    estimated = ((input_tokens - cached_tokens) * prices["input_usd_per_million"]
                 + cached_tokens * prices["cached_input_usd_per_million"]
                 + output_tokens * prices["output_usd_per_million"]) / 1_000_000
    return {"available": True, "input_tokens": input_tokens, "cached_input_tokens": cached_tokens,
            "output_tokens": output_tokens, "calls": summary.get("calls"),
            "estimated_usd": round(estimated, 8), "cost_source": "CLI token totals and declared standard-mode prices"}


def brief(root, cfg, profile, baseline, feedback=None):
    api = (root / "rust/codec-worker/src/api.rs").read_text()
    evidence = {"input": profile["input"], "identical_columns": profile["identical_lexeme_columns"],
                "timestamp": profile["timestamp"], "strongest_pairs": profile["strongest_numeric_pairs"][:4],
                "hypotheses": profile["hypotheses"], "baseline": baseline}
    return f"""Write exactly one Rust source file, codec.rs, in this workspace.
You are developing a lossless compressor for numeric CSV telemetry. Produce a compact
first working mechanism, preferably below 150 lines; later research will improve it.
Choose and explain
a mechanism in source comments, implement it, and ensure its inverse is exact. Aim to
improve the measured baseline; performance is determined by an independent evaluator.

Fixed interface (already provided by the parent crate; do not redefine it):
{api}
Your file must use `use crate::{{Codec, CodecError}};`, export `pub struct Candidate;`,
and implement Codec for Candidate. Stateless codecs use the default train method.
Safe shared helpers are available as crate::toolkit::zstd_compress(input, level)
and crate::toolkit::zstd_decompress(encoded). The latter reads the frame content size
and rejects outputs above 4 MiB. Both return Result<Vec<u8>, CodecError>.
No external crates, unsafe code, files, processes, environment, networking, or extra
modules. Your module is compiled under forbid(unsafe_code). CodecError is String.
Keep the implementation compact and check lengths before allocation/indexing.

Training evidence only:
{json.dumps(evidence, indent=2)}

sample.csv is a small training excerpt. The evidence above is sufficient: use at
most one optional read_file call, then write_file to create codec.rs immediately.
Do not spend the session rereading data or looking for other project files.
You may inspect sample.csv, but do not embed its
records in source or assume observed relationships always hold. For structured data,
consider representations, field/temporal prediction, exact duplicated columns, and
exceptions. Preserve raw numeric spellings, whitespace, quoting, line endings, and
final-newline presence. All information needed to reverse a transform belongs in
its payload or counted model. Handle empty, arbitrary binary, malformed, and
unsupported inputs with a reversible fallback. A stored original-length prefix or
other transform metadata must be included in your output. Do not use floating-point
reserialization to reconstruct the original bytes.

The runtime adds a 40-byte frame, checksum, codec ID, and raw escape. The evaluator
also tests encode/decode directly to prevent a broken algorithm hiding behind raw
escape. Tests and the Rust compiler are run by the harness after you write the file;
shell execution is disabled. Use native file-writing tools to create codec.rs now.
Write no other project files and leave prompt.txt, api.rs, and sample.csv unchanged.
{('Previous attempt feedback (repair the mechanism/code): ' + feedback) if feedback else ''}
"""


def generation_request(root, cfg, prompt, sample, *, attempt=0, phase="framework", kind="initial"):
    coder = cfg["coder"]
    excerpt = sample[:4096]
    if len(sample) > len(excerpt) and b"\n" in excerpt and not excerpt.endswith(b"\n"):
        excerpt = excerpt.rsplit(b"\n", 1)[0] + b"\n"
    definition = {"prompt": prompt, "sample_sha256": digest(excerpt), "model": coder["model"],
                  "command": coder["command"], "executable_sha256": sha256(root / coder["executable"]),
                  "max_model_steps": coder["max_model_steps"], "timeout_seconds": coder["timeout_seconds"],
                  "attempt": attempt}
    # Preserve Stage 1 cache keys while separating later phase reservations.
    if phase != "framework" or kind != "initial":
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
    (work / "sample.csv").write_bytes(excerpt)
    (work / "api.rs").write_bytes((root / "rust/codec-worker/src/api.rs").read_bytes())
    protected = {name: sha256(work / name) for name in ("prompt.txt", "sample.csv", "api.rs")}
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
