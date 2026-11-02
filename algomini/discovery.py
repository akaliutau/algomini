"""Stage 1: one profiled, generated, compiled, independently tested codec."""

import copy
import json
from pathlib import Path
import shutil

from . import codecs, coder, environment, lab
from .common import digest, sha256, write_json
from .dataset import bounded_sample, development_files


def baseline_reference(root, cfg, sample):
    cached = root / "reports/stage0/baselines-train.json"
    if cached.exists():
        reference = json.loads(cached.read_text())
        expected = [(entry["id"], entry["encode"], entry["decode"]) for entry in cfg["baselines"]]
        actual = [(entry["id"], entry["encode_command"], entry["decode_command"]) for entry in reference["results"]]
        if reference["input"]["sha256"] == digest(sample) and actual == expected:
            reference["cache"] = {"path": cached.relative_to(root).as_posix(), "sha256": sha256(cached)}
            return reference
    return lab.baselines(root, cfg)


def first_loop(root, cfg):
    cfg = copy.deepcopy(cfg)
    cfg["report_dir"] = "reports/stage1"
    output = root / cfg["report_dir"]
    output.mkdir(parents=True, exist_ok=True)
    # Keep earlier controller outcomes without leaving stale failure flags next
    # to an accepted artifact when a numbered slot is reused on replay.
    for previous in output.glob("attempt-*/failure.json"):
        write_json(output / "controller-history" / (sha256(previous) + ".json"), json.loads(previous.read_text()))
        previous.unlink()
    ledger_path = root / "reports/budget/ledger.json"
    prior_attempts = {entry["attempt_id"] for entry in json.loads(ledger_path.read_text())} if ledger_path.exists() else set()
    preflight = environment.doctor(root, cfg)
    if not preflight["ok"]:
        raise ValueError("Local preflight failed; inspect reports/stage1/preflight.json")
    versions = environment.verify_versions(root, cfg)
    if not versions["ok"]:
        raise ValueError("Installed versions differ from the Stage 0 reference")
    before = codecs.core_hashes(root)
    profile = lab.profile(root, cfg)
    sample = bounded_sample(development_files(root, cfg, "train")[0], cfg["lab"]["baseline_probe_bytes"])
    baselines = baseline_reference(root, cfg, sample)
    baseline = {"id": baselines["best_baseline"], "archive_bytes": min(row["archive_bytes"] for row in baselines["results"]),
                "reference": baselines["reference"]["archive_bytes"]}
    identity = codecs.evaluate(root, cfg, root / "rust/codec-worker/src/codec.rs", sample)
    write_json(output / "identity.json", identity)
    try:
        codecs.evaluate(root, cfg, root / "rust/fixtures/broken.rs", sample)
    except ValueError as error:
        rejection = {"rejected": True, "reason": str(error)}
    else:
        raise ValueError("The deliberately incorrect codec was accepted")
    write_json(output / "negative-control.json", rejection)
    feedback = None
    attempts = []
    maximum = min(cfg["budget"]["stage1_max_attempts"], cfg["budget"]["repair_sessions"] + 1)
    for attempt in range(maximum):
        attempt_dir = output / f"attempt-{attempt:02d}"
        source = None
        try:
            source, generation = coder.generate(root, cfg, coder.brief(root, cfg, profile, baseline, feedback), sample, attempt=attempt)
            write_json(attempt_dir / "generation.json", generation)
            # Preserve the exact generated source even if compilation or checking fails.
            attempt_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, attempt_dir / "codec.rs")
            training_sample = bounded_sample(development_files(root, cfg, "train")[0], 2048)
            measured = codecs.evaluate(root, cfg, source, sample, training_samples=[training_sample])
            write_json(attempt_dir / "evaluation.json", measured)
            attempts.append({"attempt": attempt, "accepted": True, "source_sha256": sha256(source)})
            break
        except ValueError as error:
            failure = str(error)[-6000:]
            if source is None:
                feedback = "The previous CLI attempt did not leave codec.rs. Write it now with native write_file using the relative path codec.rs. Environment setup is handled by the harness; do not debug it or inspect more data."
            else:
                feedback = failure + "\nPrevious generated source to repair:\n" + source.read_text()[:24000]
            attempts.append({"attempt": attempt, "accepted": False, "reason": failure})
            write_json(attempt_dir / "failure.json", attempts[-1])
    else:
        write_json(output / "summary.json", {"ok": False, "attempts": attempts, "scope": "train-only Stage 1"})
        raise ValueError("All bounded Stage 1 attempts failed; reports preserve the failures")
    if codecs.core_hashes(root) != before:
        raise ValueError("Trusted Rust core changed during generation/evaluation")
    reproduction = environment.snapshot(root, cfg, destination="repro/stage1-environment.json", write_locks=False)
    reproduction["rust_source_sha256"] = before
    reproduction["cc_version"] = environment.command_output(["cc", "--version"]).splitlines()[0]
    reproduction["execution_backend"] = "Linux Landlock ABI >=6 and libseccomp; local host; no Docker"
    import ctypes
    reproduction["landlock_abi"] = int(ctypes.CDLL(None).syscall(444, 0, 0, 1))
    write_json(root / "repro/stage1-environment.json", reproduction)
    packages = ("gcc", "libc6-dev", "libseccomp2", "libzstd1")
    package_lines = environment.command_output(["dpkg-query", "-W", "-f", "${Package}\t${Version}\n", *packages]).splitlines()
    (root / "repro/stage1-system-packages.lock").write_text("\n".join(line.replace("\t", "=") for line in package_lines) + "\n")
    plugin = root / "plugins/muse-telemetry-v1"
    plugin.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, plugin / "codec.rs")
    manifest = {"schema_version": 1, "id": "muse-telemetry-v1", "status": "experimental",
                "source": "codec.rs", "source_sha256": sha256(source), "codec_id": sha256(source)[:32],
                "format_version": 1, "worker_protocol_version": 1, "model": generation["model"],
                "generation_attempt_id": generation["attempt_id"], "build_fingerprint": measured["build"]["fingerprint"],
                "binary_sha256": measured["build"]["binary_sha256"], "binary_bytes": measured["build"]["binary_bytes"],
                "runtime_dependency": "libzstd.so.1; version pinned in environment records"}
    write_json(plugin / "manifest.json", manifest)
    gain = 1 - measured["archive_bytes"] / baseline["archive_bytes"]
    result = {"ok": True, "scope": "One training probe; no validation or sealed-test performance claim",
              "input_sha256": digest(sample), "input_bytes": len(sample), "attempts": attempts,
              "identity_archive_bytes": identity["archive_bytes"], "negative_control": rejection,
              "candidate": {key: value for key, value in measured.items() if key != "build"},
              "baseline": baseline, "baseline_cache": baselines.get("cache"),
              "simple_reference_archive_bytes": baselines["reference"]["archive_bytes"],
              "gain_vs_simple_reference": 1 - measured["archive_bytes"] / baselines["reference"]["archive_bytes"],
              "archive_gain": gain, "decoder_package_bytes": manifest["binary_bytes"],
              "net_deployment_saving_bytes": baseline["archive_bytes"] - measured["archive_bytes"] - manifest["binary_bytes"],
              "generation": generation, "core_unchanged_during_run": True,
              "core_sha256": digest(json.dumps(before, sort_keys=True).encode()),
              "isolation": "Local Landlock and libseccomp; no Docker",
              "reproduce": ["python", "-m", "algomini", "stage1"]}
    history = []
    for entry in json.loads((root / "reports/budget/ledger.json").read_text()):
        path = root / ".cache/coder" / entry["attempt_id"] / "generation.json"
        if path.is_file():
            historic = json.loads(path.read_text())
            write_json(output / "generation-history" / f"{entry['attempt_id']}.json", historic)
            history.append({"attempt_id": entry["attempt_id"], "artifact_valid": historic["artifact_valid"],
                            "usage": historic["usage"], "state": entry["state"]})
    result["generation_history"] = history
    budget = json.loads(ledger_path.read_text())
    result["new_generation_attempts_this_run"] = [entry["attempt_id"] for entry in budget if entry["attempt_id"] not in prior_attempts]
    result["reported_usage_estimate_usd"] = round(sum(entry["charged_usd"] for entry in budget if entry.get("cost_is_estimate")), 8)
    result["unreported_usage_reserved_usd"] = sum(entry["charged_usd"] for entry in budget if not entry.get("cost_is_estimate"))
    write_json(output / "summary.json", result)
    with (output / "observations.jsonl").open("a") as stream:
        stream.write(json.dumps({"kind": "generated-codec-evaluation", "split": "train", "input_sha256": digest(sample),
                                 "source_sha256": sha256(source), "archive_bytes": measured["archive_bytes"],
                                 "baseline_bytes": baseline["archive_bytes"], "archive_gain": gain,
                                 "generation_attempt_id": generation["attempt_id"], "evidence": "summary.json"}, sort_keys=True) + "\n")
    rows = ["# Stage 1: first local codec loop", "",
            f"Muse generated `plugins/muse-telemetry-v1/codec.rs` using **{generation['model']}**.",
            "The harness compiled it offline, checked the algorithm directly on eight tiny inputs, and verified framed reconstruction in a fresh restricted process.", "",
            "| Measurement | Bytes |", "|---|---:|",
            f"| Training input | {len(sample)} |", f"| Strongest declared baseline ({baseline['id']}) | {baseline['archive_bytes']} |",
            f"| Identity codec including frame | {identity['archive_bytes']} |",
            f"| Generated codec including frame/model | {measured['archive_bytes']} |",
            f"| Simple raw-token transpose reference | {baselines['reference']['archive_bytes']} |",
            f"| Decoder executable | {manifest['binary_bytes']} |", "",
            f"Archive reduction on this training probe: **{gain:.2%}**. Net saving after the incremental executable: **{result['net_deployment_saving_bytes']} bytes**.",
            "These are exploratory training results. The module is experimental; unseen-data verification and final throughput gates remain later stages.", "",
            "The generated module currently trails the simpler reference. Its source comment about never losing to raw zstd describes its internal payload choice; the complete archive still includes the mode tag and trusted framing.",
            f"Reported token usage estimates total ${result['reported_usage_estimate_usd']:.4f}; ${result['unreported_usage_reserved_usd']:.2f} remains reserved for calls without complete usage. These are not reconciled provider invoices.",
            f"New generation attempts during this invocation: {len(result['new_generation_attempts_this_run'])}.", "",
            f"The deliberately incorrect codec was rejected: `{rejection['reason']}`.",
            "The trusted core hashes matched before and after the run. Candidate builds and workers had no credentials or network access; decode received only the counted archive.",
            "Landlock restricts file contents and writes but does not hide host path metadata. Directory traversal metadata is visible to support native APIs. This is a host boundary, with final evaluator hardening still required in Stage 2.",
            "Timings include process and isolation startup. Do not interpret them as warmed decoder throughput.", "",
            "Reproduce with `python -m algomini stage1`. Matching generation inputs reuse the exact cached artifact; independent compilation and round-trip checks remain mandatory.",
            "See `summary.json`, `negative-control.json`, the attempt records, and `../budget/ledger.json` for evidence."]
    (output / "report.md").write_text("\n".join(rows) + "\n")
    return result
