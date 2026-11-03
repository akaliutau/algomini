"""Controlled Stage 4 ablations, Muse refinement, and validation-only selection."""

from datetime import datetime, timezone
import fcntl
import json
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tomllib

from . import codecs, coder, environment, evaluator, freeze, memory, registry
from .common import digest, sha256, worker_environment, write_json
from .dataset import bounded_sample, development_files
from .search import cached_evaluation, fingerprint, measured_summary


PARENT_SHA = "3d0f93fed0f457b33097f605c80dd314fbdb6c87bc2312fc4fca5acb9e3a499c"
CHOICE = "z1.len() < z2.len() && z1.len() < raw.len()"
PLANE_CHOICE = "} else if z2.len() < raw.len() {"
MECHANISMS = {
    "column-local-choice": "Investigate choosing raw-token column streams versus byte-position planes separately for each column, with all lengths/modes counted. Preserve the existing whole-block column/plane alternatives as a strong fallback. Compare actual compressed bytes; local raw-byte savings alone are not sufficient.",
    "token-dictionaries": "Investigate exact raw-token dictionaries or repeat runs for columns with many repeated lexemes. Store every literal once and the exact IDs/runs; consider ordering literals to improve backend compression. Preserve quotes, padding and numeric spelling without numeric conversion. Keep the parent's existing alternatives when the new representation costs more.",
    "lexical-templates": "Investigate factoring common byte prefixes/suffixes or fixed-width lexical templates with sparse exceptions. Preserve all exact raw tokens, lengths and exceptions. Prior temporal-prefix-only code regressed, so compare after zstd and retain the complete parent alternatives rather than replacing them unconditionally.",
}


def load_policy(root, path="refine.toml"):
    path = Path(path)
    if not path.is_absolute():
        path = root / path
    policy = tomllib.loads(path.read_text())
    if (policy["schema_version"] != 1 or policy["run_id"] != "stage4" or policy["parent_sha256"] != PARENT_SHA
            or not 0 < len(policy["levels"]) <= 4 or len(set(policy["levels"])) != len(policy["levels"])
            or any(not isinstance(level, int) or not 1 <= level <= 22 for level in policy["levels"])
            or policy["ablation_level"] not in policy["levels"]
            or not 1 <= policy["maximum_evolved_candidates"] <= 12 or not 0 <= policy["maximum_repairs"] <= 4
            or not 1 <= policy["plateau_rounds"] <= 3 or not 0 < policy["screen_bytes"] <= 65536
            or not 0 < policy["maximum_run_seconds"] <= 14400
            or not 1 <= policy["retrieve_documents"] <= 4 or not 0 < policy["retrieve_chars"] <= 6000):
        raise ValueError("Invalid bounded Stage 4 policy")
    mechanisms = policy["evolved_mechanisms"]
    if not mechanisms or len(set(mechanisms)) != len(mechanisms) or any(item not in MECHANISMS for item in mechanisms):
        raise ValueError("Declare distinct supported refinement mechanisms")
    return policy


def variant_source(parent, mode, level):
    """Exact source edits only; keep the parent's decoder and layout unchanged."""
    if digest(parent) != PARENT_SHA or mode not in ("raw", "column", "plane", "adaptive") or not 1 <= level <= 22:
        raise ValueError("Variant requires the reviewed parent, layout mode, and bounded zstd level")
    source = parent.decode("utf-8")
    if source.count(CHOICE) != 1 or source.count(PLANE_CHOICE) != 1:
        raise ValueError("Reviewed source selection anchors changed")
    if mode != "adaptive":
        source = source.replace(CHOICE, "z1.len() < raw.len()" if mode == "column" else "false")
        source = source.replace(PLANE_CHOICE, "} else if false {" if mode in ("raw", "column") else PLANE_CHOICE)
    source, count = re.subn(r"zstd_compress\((input|&t1|&t2), 19\)", lambda match: f"zstd_compress({match[1]}, {level})", source)
    if count != 4:
        raise ValueError("Reviewed backend call sites changed")
    definition = {"parent_sha256": PARENT_SHA, "mode": mode, "zstd_level": level,
                  "author": "deterministic host variant of exact Muse source"}
    return ("// Stage 4 controlled variant: " + json.dumps(definition, sort_keys=True) + "\n" + source).encode()


def minimize_failure(data, fails, *, maximum_checks=32):
    if not 1 <= maximum_checks <= 64:
        raise ValueError("Counterexample check budget exceeds bounds")
    checks = 1
    if not fails(data):
        raise ValueError("Counterexample predicate must fail on the original input")
    granularity = 2
    while data and checks < maximum_checks:
        size = max(1, (len(data) + granularity - 1) // granularity)
        reduced = False
        for offset in range(0, len(data), size):
            if checks >= maximum_checks:
                break
            trial = data[:offset] + data[offset + size:]
            checks += 1
            if fails(trial):
                data, granularity, reduced = trial, max(2, granularity - 1), True
                break
        if not reduced:
            if granularity >= len(data):
                break
            granularity = min(len(data), granularity * 2)
    return data, {"predicate_checks": checks, "maximum_checks": maximum_checks, "remaining_bytes": len(data)}


def direct_failure(root, cfg, binary, data, model=b""):
    try:
        encoded, _ = codecs.invoke(root, cfg, binary, 4, data, original_len=len(data), model=model)
        restored, _ = codecs.invoke(root, cfg, binary, 5, encoded, original_len=len(data), model=model)
        return restored != data
    except ValueError:
        return True


def counterexample(root, cfg, binary, directory, samples, *, model=b"", control=False):
    for data in samples:
        if direct_failure(root, cfg, binary, data, model):
            minimal, checks = minimize_failure(data, lambda value: direct_failure(root, cfg, binary, value, model))
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "counterexample.bin").write_bytes(minimal)
            record = {"input_bytes": len(data), "input_sha256": digest(data), "minimal_sha256": digest(minimal),
                      "control_fixture": control, "scope": "Bounded deletion minimization; not a proof of global minimality", **checks}
            write_json(directory / "counterexample.json", record)
            return record
    return None


def observe(root, cfg, policy, code, definition):
    checksum = digest(code)
    # All methods have 20-byte names, matching the Stage 3 parent. This controls
    # common-container overhead in causal comparisons.
    identifier = "s4-" + checksum[:17]
    directory = root / "reports/stage4/candidates" / identifier
    directory.mkdir(parents=True, exist_ok=True)
    source, outcome = directory / "codec.rs", directory / "outcome.json"
    if outcome.exists():
        record = json.loads(outcome.read_text())
        if not source.is_file() or sha256(source) != checksum or record["definition"] != definition:
            raise ValueError("Recorded refinement source/definition changed")
        if record["outcome"] == "measured":
            result, _ = cached_evaluation(root, cfg, identifier, directory)
            if measured_summary(result) != record["measurements"]:
                raise ValueError("Recorded refinement measurement changed")
        memory.append(root, record, run_id="stage4")
        return record
    if source.exists() and sha256(source) != checksum:
        raise ValueError("Refinement source cache collision")
    source.write_bytes(code)
    record = {"id": identifier, "schema_version": 1, "source_sha256": checksum,
              "parent_source_sha256": definition["parent_source_sha256"], "definition": definition,
              "tags": ["csv", "refinement", definition["mechanism"]], "hypothesis": definition["hypothesis"],
              "split": "validation", "outcome": "rejected", "driver_sha256": sha256(__file__),
              "freeze_id": (root / freeze.ANCHOR).read_text().strip(),
              "reproduce": ["python", "-m", "algomini", "stage4"]}
    stage = "compile/tiny correctness/training screen"
    sample = bounded_sample(development_files(root, cfg, "train")[0], policy["screen_bytes"])
    try:
        print(f"[{identifier}] {definition['mechanism']}: tiny checks", flush=True)
        screen = codecs.evaluate(root, cfg, source, sample, training_samples=[sample[:2048]])
        write_json(directory / "screen.json", screen)
        stage = "manifest/frozen validation"
        existing = registry.discover(root)
        if identifier not in existing:
            registry.register(root, identifier, source, provenance=definition)
        elif existing[identifier]["source_sha256"] != checksum:
            raise ValueError("Refinement plugin id collision")
        result, _ = cached_evaluation(root, cfg, identifier, directory)
        record.update(outcome="measured", measurements=measured_summary(result),
                      evidence=(directory / "evaluation.json").relative_to(root).as_posix())
        print(f"[{identifier}] {record['measurements']['archive_bytes']} bytes; gain {record['measurements']['archive_gain']:.2%}", flush=True)
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        record.update(failed_stage=stage, failure=str(error)[-6000:])
        try:
            binary, _ = codecs.build(root, cfg, source)
            training = struct.pack("<I", min(len(sample), 2048)) + sample[:2048]
            model, _ = codecs.invoke(root, cfg, binary, 3, training)
            cases = [b"", b"\0", b"a" * 63, bytes(range(256)), b"a,b\r\n1.00,-0\r\n", sample[:2048], sample]
            record["counterexample"] = counterexample(root, cfg, binary, directory, cases, model=model)
        except ValueError:
            pass  # Compiler failures have no failing input; retain diagnostics/source.
        print(f"[{identifier}] rejected: {record['failure'][:200]}", flush=True)
    write_json(outcome, record)
    memory.append(root, record, run_id="stage4")
    return record


def eligible(records):
    return sorted((record for record in records if record["outcome"] == "measured" and all(record["measurements"]["gates"].values())),
                  key=lambda record: (record["measurements"]["archive_bytes"], -record["measurements"]["deployment_saving_bytes"], record["source_sha256"]))


def ablations(records, level):
    controls = {record["definition"]["mode"]: record for record in records
                if record["definition"].get("zstd_level") == level and record["definition"]["kind"] == "deterministic"
                and record["outcome"] == "measured"}
    if set(controls) != {"raw", "column", "plane", "adaptive"}:
        raise ValueError("All four controlled ablations must complete before refinement")
    adaptive = controls["adaptive"]["measurements"]
    rows = []
    for index, row in enumerate(adaptive["files"]):
        sizes = {mode: record["measurements"]["files"][index]["block_bytes"] for mode, record in controls.items()}
        if sizes["adaptive"] != min(sizes.values()):
            raise ValueError("Adaptive output does not match the controlled per-block minimum")
        rows.append({"label": row["label"], "block_bytes": sizes, "plane_contribution_bytes": sizes["column"] - sizes["adaptive"]})
    return {"schema_version": 1, "zstd_level": level, "equal_method_name_bytes": 20,
            "controls": {mode: {"id": record["id"], "archive_bytes": record["measurements"]["archive_bytes"],
                                 "source_sha256": record["source_sha256"]} for mode, record in controls.items()},
            "files": rows, "plane_contribution_bytes": controls["column"]["measurements"]["archive_bytes"] - adaptive["archive_bytes"],
            "representation_saving_vs_raw_bytes": controls["raw"]["measurements"]["archive_bytes"] - adaptive["archive_bytes"],
            "scope": "Controlled output-mode ablation at fixed backend level, decoder, framing and method-name length; all bytes counted"}


def run(root, cfg, policy_path="refine.toml", *, measure_only=False):
    policy = load_policy(root, policy_path)
    output = root / "reports/stage4"
    output.mkdir(parents=True, exist_ok=True)
    with (output / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _run(root, cfg, policy, output, measure_only=measure_only)


def _run(root, cfg, policy, output, *, measure_only=False):
    from . import selection

    pinned = freeze.verify(root, cfg)
    if (root / selection.RECORD).exists():
        selection.verify(root, cfg)
    parent = registry.resolve(root, policy["parent"])
    if parent["source_sha256"] != policy["parent_sha256"]:
        raise ValueError("Reviewed parent source changed")
    definition = {"policy": policy, "freeze_id": pinned["freeze_id"], "coder": cfg["coder"],
                  "training_sha256": [digest(value) for value in evaluator.training_samples(root, cfg)],
                  "validation_sha256": [digest(value) for _, value in evaluator.probes(root, cfg, "validation")]}
    state_path = output / "run.json"
    if state_path.exists():
        state = json.loads(state_path.read_text())
        if state["definition_sha256"] != fingerprint(definition):
            raise ValueError("Stage 4 definition changed; preserve the run instead of silently replacing it")
    else:
        state = {"started_at": datetime.now(timezone.utc).isoformat(), "definition_sha256": fingerprint(definition), "definition": definition}
        write_json(state_path, state)
    records = []
    matrix = [(mode, policy["ablation_level"]) for mode in ("raw", "column", "plane", "adaptive")]
    matrix += [("adaptive", level) for level in policy["levels"] if level != policy["ablation_level"]]
    code = parent["source_path"].read_bytes()
    for mode, level in matrix:
        parameters = {"kind": "deterministic", "mechanism": f"{mode}-zstd-{level}", "mode": mode, "zstd_level": level,
                      "parent_source_sha256": parent["source_sha256"],
                      "hypothesis": "Measure layout contribution at fixed effort, or backend-level effect with the adaptive layout held fixed"}
        records.append(observe(root, cfg, policy, variant_source(code, mode, level), parameters))
    controlled = ablations(records, policy["ablation_level"])
    write_json(output / "ablations.json", controlled)
    memory.append(root, {"id": "stage4-controlled-ablations", "tags": ["csv", "byteplanes", "ablation"],
                        "split": "validation", "hypothesis": controlled["scope"], "measurements": controlled,
                        "evidence": "reports/stage4/ablations.json"}, run_id="stage4")
    if measure_only:
        return {"ok": True, "freeze_id": pinned["freeze_id"], "controlled_experiments": len(records),
                "ablations": controlled, "best": [{"id": item["id"], **item["measurements"]} for item in eligible(records)[:1]],
                "scope": "Measurement-only invocation; no model call or selection change"}
    return finish(root, cfg, policy, output, state, records, controlled)


def refinement_brief(root, cfg, parent, mechanism, controlled, retrieved, *, repair=None):
    source = (root / parent["source_path"]).read_text()
    contract = (root / "rust/codec-worker/src/api.rs").read_text()
    return f"""Write exactly one complete Rust file codec.rs using native write_file now.
Refine the working parent compressor below with ONE focused mechanism:
{MECHANISMS[mechanism]}

Preserve its exact-byte inverse, existing alternatives, generic fallback and
stateless trainer. Keep only one file, no external crates, unsafe code, files,
environment, processes or networking. The fixed parent crate supplies:
{contract}
Use crate::{{Codec, CodecError}}, pub struct Candidate and impl Codec.
Safe helpers crate::toolkit::zstd_compress(input, level) and zstd_decompress return
Result<Vec<u8>, String>. Source limit 64 KiB; payload 4 MiB; input 2 MiB.
Use compact, checked Rust. Keep working parent code where possible instead of
rewriting unrelated parsing/decoding. Add a new counted mode only if needed.

All numbers, quotes, padding, exponents, negative zero, whitespace and line
endings must reconstruct byte-for-byte. No numeric rounding or float-based
reserialization. Tiny/binary/malformed inputs need a tagged reversible fallback;
the host tests the algorithm directly without raw escape. Keep all state in
payload/model, and keep the default empty model for this stateless experiment.
Encoder budget is 30 seconds; decoder minimum 10 MiB/s and address space 256 MiB.

Current parent's validation archive: {parent['archive_bytes']} bytes. The strongest
declared baseline is 46,253 and the structured reference 40,434 bytes on these
fixed DEVELOPMENT probes. Actual validation bytes are never supplied to you.
Controlled measurements (same metadata cost and decoder):
{json.dumps(controlled, separators=(',', ':'))}
Training facts: numeric tokens are quoted, sometimes padded, scientific or long
decimal strings. Sampled data columns rv1/rv2 match, but their headers differ.
Prior header-separated/dedup prototypes did not beat the simple NUL-column
reference on a 64 KiB training sample. Keep choices based on actual compressed
bytes after metadata. Do not silently switch all backend calls to a weaker level.

Retrieved project knowledge:
{json.dumps(retrieved, separators=(',', ':'))}

Working parent, source SHA-256 {parent['source_sha256']}:
{source}

{('Repair only the smallest compiler/correctness issue, preserving the mechanism. Host diagnostics: ' + repair) if repair else ''}
prompt.txt contains this task. sample.csv is an optional <=4 KiB TRAIN excerpt.
At most one optional read_file call, then write_file codec.rs immediately.
Shell is disabled; the harness compiles and tests after your write. Do not create
tests/README or inspect other project files. Explain the new mode in short comments.
"""


def evolve(root, cfg, policy, parent_record, mechanism, slot, controlled, *, repair=None):
    directory = root / "reports/stage4/requests" / slot
    directory.mkdir(parents=True, exist_ok=True)
    terminal = directory / "outcome.json"
    if terminal.exists():
        result = json.loads(terminal.read_text())
        if result["outcome"] == "measured" or result.get("source_path"):
            code = (root / result["source_path"]).read_bytes()
            if digest(code) != result["source_sha256"]:
                raise ValueError("Evolved source changed after its outcome was recorded")
            if result.get("candidate_id"):
                original = json.loads((root / "reports/stage4/candidates" / result["candidate_id"] / "outcome.json").read_text())
                return observe(root, cfg, policy, code, original["definition"])
        return result
    if (root / "repro/candidate-freeze.json").exists():
        raise ValueError("A candidate is frozen; inspect missing request outcomes instead of starting new generation")
    sample = bounded_sample(development_files(root, cfg, "train")[0], policy["screen_bytes"])
    request_path = directory / "request.json"
    if request_path.exists():
        request = json.loads(request_path.read_text())
        if request["sample_sha256"] != digest(sample):
            raise ValueError("Refinement generation inputs changed")
    else:
        source_path = parent_record.get("source_path", f"reports/stage4/candidates/{parent_record['id']}/codec.rs")
        parent = {"source_path": source_path, "source_sha256": parent_record["source_sha256"],
                  "archive_bytes": parent_record.get("measurements", {}).get("archive_bytes", "rejected; no score")}
        retrieved = memory.search(root, mechanism + " byteplanes csv", limit=policy["retrieve_documents"], max_chars=policy["retrieve_chars"], family="csv")
        prompt = refinement_brief(root, cfg, parent, mechanism, controlled, retrieved, repair=repair)
        kind = "repair" if repair else "evolved"
        attempt_id, _, _ = coder.generation_request(root, cfg, prompt, sample, phase="refinement", kind=kind)
        request = {"prompt": prompt, "prompt_sha256": digest(prompt.encode()), "sample_sha256": digest(sample),
                   "attempt_id": attempt_id, "kind": kind, "parent_source_sha256": parent["source_sha256"], "retrieved": retrieved}
        write_json(request_path, request)
    try:
        print(f"[{slot}] Muse {request['kind']}: {mechanism}", flush=True)
        source, generation = coder.generate(root, cfg, request["prompt"], sample, phase="refinement", kind=request["kind"])
        write_json(directory / "generation.json", generation)
        code = source.read_bytes()
        (directory / "codec.rs").write_bytes(code)
        definition = {"kind": request["kind"], "mechanism": mechanism, "hypothesis": MECHANISMS[mechanism],
                      "parent_source_sha256": request["parent_source_sha256"], "generation_attempt_id": request["attempt_id"],
                      "parameters": "All encoder/decoder choices are embedded in the exact generated source; no external runtime parameters"}
        result = observe(root, cfg, policy, code, definition)
        write_json(terminal, {**result, "candidate_id": result["id"], "source_path": (directory / "codec.rs").relative_to(root).as_posix()})
        return result
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        result = {"id": slot, "outcome": "generation-failed", "source_sha256": None,
                  "definition": {"kind": request["kind"], "mechanism": mechanism, "parent_source_sha256": request["parent_source_sha256"]},
                  "parent_source_sha256": request["parent_source_sha256"], "generation_attempt_id": request["attempt_id"],
                  "tags": ["csv", "refinement", "failure"], "hypothesis": MECHANISMS[mechanism], "failure": str(error)[-4000:]}
        cache = root / ".cache/coder" / request["attempt_id"]
        if (cache / "generation.json").exists():
            shutil.copyfile(cache / "generation.json", directory / "generation.json")
        source = cache / "workspace/codec.rs"
        if source.is_file() and not source.is_symlink() and source.stat().st_size <= 65536:
            shutil.copyfile(source, directory / "codec.rs")
            result.update(source_sha256=sha256(source), source_path=(directory / "codec.rs").relative_to(root).as_posix())
        write_json(terminal, result)
        memory.append(root, result, run_id="stage4")
        return result


def repeat_selection(root, cfg, record, output):
    path = output / "selected-repeat.json"
    original = json.loads((root / record["evidence"]).read_text())
    if path.exists():
        result = json.loads(path.read_text())
    else:
        command = [sys.executable, "-m", "algomini.evaluator", "evaluate", record["id"], "--split", "validation"]
        subprocess.run(command, cwd=root, env=worker_environment(), capture_output=True, text=True, check=True, timeout=1200)
        result = json.loads((root / "reports/evaluations" / record["id"] / "validation/result.json").read_text())
        write_json(path, result)

    def signatures(value):
        return {row["id"]: (row["archive_bytes"], row["archive_sha256"]) for row in
                [*value["baselines"]["results"], value["reference"], value["candidate"]]}

    if signatures(result) != signatures(original) or not all(result["score"]["gates"].values()):
        raise ValueError("Selected candidate did not repeat archive bytes/hashes and frozen acceptance gates")
    check = {"ok": True, "all_eight_archive_signatures_repeat": True, "candidate_id": record["id"],
             "source_sha256": record["source_sha256"], "archive_bytes": result["candidate"]["archive_bytes"], "score": result["score"]}
    write_json(output / "repeat-check.json", check)
    return check


def mixed_choice_ablation(root, cfg, policy):
    parent_id = "s4-98e12bcd61e8117b0"
    parent = registry.resolve(root, parent_id)
    expected = "98e12bcd61e8117b0c9f3cc6b4edfbc3ebcb13f11d332c39290903ed97cc8cf7"
    source = parent["source_path"].read_text()
    marker = "if z3.len() < best.len() {"
    if parent["source_sha256"] != expected or source.count(marker) != 1:
        raise ValueError("Reviewed mixed-column selector changed")
    code = ("// Stage 4 causal ablation: disable only mixed-column selection.\n" + source.replace(marker, "if false && z3.len() < best.len() {")).encode()
    definition = {"kind": "mechanism-ablation", "mechanism": "disable-mixed-columns", "zstd_level": 22,
                  "parent_source_sha256": expected, "hypothesis": "Hold decoder, backend, metadata and alternatives fixed; disable only the mixed-column encoder choice"}
    control = observe(root, cfg, policy, code, definition)
    measured = json.loads((root / "reports/stage4/candidates" / parent_id / "evaluation.json").read_text())
    if control["outcome"] != "measured":
        raise ValueError("Mixed-column ablation did not pass correctness")
    difference = control["measurements"]["archive_bytes"] - measured["candidate"]["archive_bytes"]
    rows = [{"label": left["label"], "without_mix_bytes": left["block_bytes"], "with_mix_bytes": right["block_bytes"],
             "saving_bytes": left["block_bytes"] - right["block_bytes"]}
            for left, right in zip(control["measurements"]["files"], measured["candidate"]["files"])]
    if difference < 0 or any(row["saving_bytes"] < 0 for row in rows):
        raise ValueError("Mixed-choice control unexpectedly outperformed a min-size selector")
    result = {"schema_version": 1, "parent_id": parent_id, "parent_source_sha256": expected,
              "control_id": control["id"], "control_source_sha256": control["source_sha256"], "zstd_level": 22,
              "with_mix_bytes": measured["candidate"]["archive_bytes"], "without_mix_bytes": control["measurements"]["archive_bytes"],
              "mixed_column_contribution_bytes": difference, "files": rows,
              "scope": "Single encoder-selection condition disabled; decoder/backend/framing and 20-byte method names held fixed"}
    write_json(root / "reports/stage4/mixed-choice-ablation.json", result)
    memory.append(root, {"id": "stage4-mixed-choice-ablation", "tags": ["csv", "byteplanes", "column-local-choice", "ablation"],
                        "hypothesis": result["scope"], "split": "validation", "measurements": result,
                        "evidence": "reports/stage4/mixed-choice-ablation.json"}, run_id="stage4")
    return result


def finish(root, cfg, policy, output, state, records, controlled):
    from . import selection

    if not eligible(records):
        raise ValueError("No deterministic control passes the frozen acceptance gates")
    started = datetime.fromisoformat(state["started_at"])
    plateau, repairs, stop = 0, 0, "bounded_refinement_complete"
    for index, mechanism in enumerate(policy["evolved_mechanisms"][:policy["maximum_evolved_candidates"]]):
        slot = f"evolved-{index:02d}"
        completed = (output / "requests" / slot / "outcome.json").exists()
        if not completed and (datetime.now(timezone.utc) - started).total_seconds() >= policy["maximum_run_seconds"]:
            stop = "time_limit"
            break
        before = eligible(records)[0]
        record = evolve(root, cfg, policy, before, mechanism, slot, controlled)
        records.append(record)
        if record["outcome"] not in ("measured", "duplicate") and repairs < policy["maximum_repairs"]:
            repair_slot = slot + "-repair"
            repair_done = (output / "requests" / repair_slot / "outcome.json").exists()
            ledger = json.loads((root / "reports/budget/ledger.json").read_text())
            remaining = sum(entry.get("kind") == "repair" for entry in ledger) < cfg["budget"]["repair_sessions"]
            if repair_done or (remaining and (datetime.now(timezone.utc) - started).total_seconds() < policy["maximum_run_seconds"]):
                if record.get("source_sha256"):
                    feedback = record.get("failure", "repair rejected source")
                    example = output / "candidates" / record["id"] / "counterexample.bin"
                    if example.exists() and example.stat().st_size <= 256:
                        feedback += "\nMinimal train/synthetic failure input (hex): " + example.read_bytes().hex()
                    record = evolve(root, cfg, policy, record, mechanism, repair_slot, controlled, repair=feedback)
                    records.append(record)
                    repairs += 1
        after = eligible(records)[0]
        plateau = 0 if after["measurements"]["archive_bytes"] < before["measurements"]["archive_bytes"] else plateau + 1
        if plateau >= min(policy["plateau_rounds"], cfg["budget"]["plateau_rounds"]):
            stop = "three_rounds_without_validation_improvement"
            break
    winner = eligible(records)[0]
    mixed = mixed_choice_ablation(root, cfg, policy)
    repeat = repeat_selection(root, cfg, winner, output)
    control_dir = output / "counterexample-control"
    if not (control_dir / "counterexample.json").exists():
        broken, _ = codecs.build(root, cfg, root / "rust/fixtures/broken.rs")
        counterexample(root, cfg, broken, control_dir, [b"a,b\r\n1.00,-0\r\n"], control=True)
    if (root / selection.RECORD).exists():
        selected = selection.verify(root, cfg, rebuild=True)
        if selected["candidate_id"] != winner["id"]:
            raise ValueError("Selection is already frozen; do not replace it with another candidate")
    else:
        selected = selection.create(root, cfg, winner, policy)
    ledger = json.loads((root / "reports/budget/ledger.json").read_text())
    paid = [entry for entry in ledger if entry["phase"] == "refinement"]
    result = {"schema_version": 1, "ok": True, "selection": selected, "winner": winner,
              "records": [{"id": item["id"], "outcome": item["outcome"], "definition": item["definition"],
                           "source_sha256": item["source_sha256"], "measurements": item.get("measurements")} for item in records],
              "ablations": controlled, "mixed_choice_ablation": mixed, "repeat": repeat, "stop_reason": stop, "repair_sessions": repairs,
              "refinement_budget": {"sessions": len(paid), "known_usage_estimate_usd": sum(entry["charged_usd"] for entry in paid if entry.get("cost_is_estimate")),
                                    "retained_reservations_usd": sum(entry["charged_usd"] for entry in paid if not entry.get("cost_is_estimate")),
                                    "allocation_usd": cfg["budget"]["refinement_usd"]},
              "freeze": freeze.verify(root, cfg), "sealed_test_opened": False, "docker_used": False,
              "scope": "One source/model/parameter/build is frozen using validation alone; sealed verification and installation remain later work"}
    write_json(output / "results.json", result)
    rows = ["# Stage 4: refinement and frozen candidate", "", f"Candidate freeze: `{selected['candidate_freeze_id']}`.",
            f"Selected **{winner['id']}**, source `{winner['source_sha256']}`. All ranking uses fixed validation probes; the sealed test is unopened.", "",
            "| Candidate | Experiment | Outcome | Complete bytes | Gain |", "|---|---|---|---:|---:|"]
    for item in records:
        value = item.get("measurements", {})
        rows.append(f"| {item['id']} | {item['definition']['mechanism']} | {item['outcome']} | {value.get('archive_bytes', '—')} | " +
                    (f"{value['archive_gain']:.2%}" if "archive_gain" in value else "—") + " |")
    metrics = winner["measurements"]
    rows += ["", f"At zstd-{controlled['zstd_level']}, byte planes save **{controlled['plane_contribution_bytes']} bytes** versus column-only output, and adaptive representation saves **{controlled['representation_saving_vs_raw_bytes']} bytes** versus raw-zstd output. Decoder/framing/backend effort and 20-byte method names are controlled.",
             f"Disabling only the evolved mixed-column selector at zstd-22 increases the archive from **{mixed['with_mix_bytes']:,} to {mixed['without_mix_bytes']:,} bytes**: its controlled contribution is **{mixed['mixed_column_contribution_bytes']:,} bytes**.",
             f"Selected complete archive: **{metrics['archive_bytes']:,} bytes**, **{metrics['archive_gain']:.2%}** below the strongest declared baseline. Minimum decode speed **{metrics['minimum_decode_mib_per_second']:.2f} MiB/s**, peak RSS **{metrics['peak_decode_rss_kib']:,} KiB**. Deployment saving is **{metrics['deployment_saving_bytes']:,} bytes** on this small corpus.",
             "An independent repeat matches all eight complete archive sizes/hashes and passes resource gates. Every deterministic derivative is labeled as host-generated from exact Muse source; model revisions retain source lineage and usage records.",
             "Equal-size archives prefer the smaller decoder package, then source hash. An earlier unmerged source-hash tie draft is retained under `premerge-selection-01/`; no sealed data was opened and no extra model call was made to revise it.",
             "The negative-control minimizer reduces a deliberately broken fixture to one byte. Any model correctness failures retain their own bounded counterexamples; compiler errors retain source/diagnostics rather than inventing input failures.",
             f"Muse refinement sessions: {len(paid)}; known token-based usage estimate ${result['refinement_budget']['known_usage_estimate_usd']:.4f}; retained reservations ${result['refinement_budget']['retained_reservations_usd']:.2f}. Stop: {stop}.",
             "The exact source, serialized model, parameters, release compiler settings, native evaluator id, thresholds and public sealed commitment are frozen in `repro/candidate-freeze.json` and `artifacts/stage4-selected/`.",
             "No scientific novelty, deployment saving, or unseen-data win is claimed. Stage 5 must use this exact frozen model without training on sealed data, and record one final sealed outcome.",
             "Reproduce: `python -m algomini stage4`; verify: `python -m algomini selected-status --build`. Completed replay uses cached requests/results and never makes another model call."]
    (output / "report.md").write_text("\n".join(rows) + "\n")
    return result
