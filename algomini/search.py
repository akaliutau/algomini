"""Resumable, budgeted Stage 3 research; official scoring runs in a fresh process."""

from datetime import datetime, timezone
import fcntl
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib

from . import codecs, coder, environment, evaluator, freeze, memory, registry, research
from .common import digest, sha256, worker_environment, write_json
from .dataset import bounded_sample, development_files


MECHANISMS = {
    "column-layout": "Replace per-cell length varints with a reversible raw-token column layout (for example NUL separators), preserving dimensions and every line ending. Select block-local duplicate-column references when useful. Compare transformed and ordinary zstd payloads. The simple NUL-column reference already beats the Stage 1 varint layout, so make its metadata and inverse precise.",
    "decimal-deltas": "Represent supported decimal tokens as exact integer mantissas, signs and decimal scales, then encode temporal deltas/zigzag varints per column before zstd. Preserve trailing zeros, negative zero, leading signs, and unsupported tokens with explicit exceptions or fallback. Select this numeric representation only when useful. Never reconstruct through floating point; checked i128 arithmetic is available.",
    "cross-field": "Test earlier-column prediction and exact duplicate columns within a block. For supported numeric tokens, encode exact residuals against an earlier field or a previous row, storing the selected predictor and lexical information. Preserve all exceptions. Compare block-local predictor choices by actual encoded size. Relationships are observed evidence, never guaranteed schema facts.",
    "temporal-prefixes": "Encode each column's successive raw tokens using longest common prefix plus suffix, optionally previous-token equality runs. Keep metadata streams apart from suffix bytes before zstd so tiny prefix values do not disrupt lexeme patterns. Preserve arbitrary spellings and line endings. Compare with a plain raw-token transposition escape; raw prefix savings alone do not establish compressed savings.",
    "timestamp-dedup": "Exploit regular timestamp tokens and repeated columns using reversible template/delta or prefix changes and block-local equality references. Keep exact original timestamp spelling, calendar transitions and exceptions. Other columns remain raw token streams before zstd. Store all schema and predictor choices in the payload, not assumptions about this sample.",
    "byteplanes": "Investigate column-specific byte-plane or digit-position streams for variable-width numeric lexemes. Store exact token lengths and transpose character positions within each column before zstd; avoid numeric conversion. Account for all lengths/line endings and choose plain transpose if the representation is larger. This is a distinct representation experiment, not a zstd-level sweep.",
}


def load_policy(root, path="search.toml"):
    path = Path(path)
    if not path.is_absolute():
        path = root / path
    policy = tomllib.loads(path.read_text())
    if (policy["schema_version"] != 1 or not registry.IDENTIFIER.fullmatch(policy["run_id"]) or len(policy["run_id"]) > 20
            or not 4 <= policy["maximum_initial_candidates"] <= 12
            or not 0 <= policy["maximum_repairs"] <= 4
            or not 3 <= policy["elite_count"] <= 4
            or not 3 <= policy["minimum_diverse_elites"] <= policy["elite_count"]
            or not 0 < policy["screen_bytes"] <= 65536
            or not 0 < policy["maximum_run_seconds"] <= 14400
            or not 0 <= policy["profile_distance"] <= 1
            or not 1 <= policy["retrieve_documents"] <= 4 or not 0 < policy["retrieve_chars"] <= 6000):
        raise ValueError("Invalid bounded research policy")
    mechanisms = policy["initial_mechanisms"] + policy["expansion_mechanisms"]
    if len(policy["initial_mechanisms"]) != 4 or len(set(mechanisms)) != len(mechanisms) or any(item not in MECHANISMS for item in mechanisms):
        raise ValueError("Declare four distinct initial mechanisms and unique supported expansion mechanisms")
    return policy


def fingerprint(value):
    return digest(json.dumps(value, sort_keys=True).encode())


def profile_vector(record):
    return [row["block_bytes"] / row["input_bytes"] for row in record["measurements"]["files"]]


def diverse_elites(records, *, count=4, distance=.01):
    eligible = [record for record in records if record.get("outcome") == "measured"
                and record["measurements"]["archive_gain"] > 0
                and all(record["measurements"]["gates"][key] for key in ("exact_reconstruction", "decode_speed", "decode_rss"))]
    ranked = sorted(eligible, key=lambda record: (record["measurements"]["archive_bytes"], record["id"]))
    selected, families, seen = [], set(), set()
    for record in ranked:
        if record["mechanism"] not in families and record["source_sha256"] not in seen:
            selected.append(record)
            families.add(record["mechanism"])
            seen.add(record["source_sha256"])
            if len(selected) == count - 1:
                break
    # Reserve one exploration slot for a distinct per-probe compression profile.
    remaining = [record for record in ranked if record["source_sha256"] not in seen and record["mechanism"] not in families]
    if remaining and len(selected) < count:
        def separation(record):
            vector = profile_vector(record)
            return min(max(abs(a - b) for a, b in zip(vector, profile_vector(other))) for other in selected)
        exploratory = max(remaining, key=lambda record: (separation(record), -record["measurements"]["archive_bytes"]))
        if separation(exploratory) >= distance:
            selected.append(exploratory)
        else:
            selected.append(remaining[0])
    return [{"id": record["id"], "mechanism": record["mechanism"], "source_sha256": record["source_sha256"],
             "archive_bytes": record["measurements"]["archive_bytes"], "archive_gain": record["measurements"]["archive_gain"],
             "selection": "archive rank with distinct mechanism" if index < count - 1 else "exploration profile"}
            for index, record in enumerate(selected)]


def brief(root, cfg, profile, mechanism, retrieved, *, previous=None):
    first = profile["columns"][0]
    compact = {"split": "train", "inputs": profile["inputs"], "identical_columns": profile["identical_columns"],
               "timestamp": profile["timestamp"], "fields": first["fields"], "limitations": profile["limitations"]}
    contract = (root / "rust/codec-worker/src/api.rs").read_text()
    feedback = ""
    if previous:
        feedback = "\nRepair this exact mechanism with the smallest compiler/correctness fix. Preserve working transforms; do not redesign unrelated functions. Host failure:\n" + previous["failure"] + "\nPrevious source:\n" + previous.get("source", "")
    return f"""Create exactly one file codec.rs with Muse's native write_file tool now.
This is an independent lossless Rust telemetry-compression experiment: {mechanism}.
Hypothesis: {MECHANISMS[mechanism]}

Fixed parent API (do not redefine):
{contract}
Use crate::{{Codec, CodecError}}, export pub struct Candidate, implement Codec.
Safe helpers: crate::toolkit::zstd_compress(input, level) and
crate::toolkit::zstd_decompress(encoded), both Result<Vec<u8>, String>.
Use zstd level 19 for these experiments so backend effort matches the strong
reference. Encoder time is allowed up to 30 seconds per <=2 MiB block; only
decode throughput is a speed acceptance gate. Level 9 weakened earlier layouts.
No extra crates/modules/dependencies, unsafe code, filesystem, environment,
processes or networking. Keep code compact (roughly 150-250 lines), bounds-check
dimensions and checked arithmetic before allocations. Stateless train is fine.
Use binary varints or fixed integers for compact counted transform metadata.

Generic arbitrary bytes, empty input, NUL, malformed CSV, CRLF, final lines
without newline, JSON duplicate keys and numeric spelling variations MUST
round-trip through encode/decode directly. Use a tagged plain zstd fallback for
unsupported layouts. The wrapper's raw escape is not a substitute for a correct
algorithm. Compare alternatives in encode if needed, then record the chosen tag.
Bound inputs at 2 MiB, output/model at the API limits. Exact bytes matter: never
round decimals, discard exceptions, canonicalize quoting or assume learned
equality, timestamps or column counts always hold. Never embed sample records.
The host adds/counts a 40-byte frame plus a common corpus container. Numeric
tokens are often DOUBLE-QUOTED, space-padded, and written with long decimal
fractions or scientific exponents. The statistics unwrap these only for analysis;
your inverse must retain the quotes, padding and spelling, or use a reversible
fallback for unsupported tokens. Detect raw layout conditions before transforming.

Measured train-only facts (not guarantees):
{json.dumps(compact, separators=(',', ':'))}

Relevant prior research retrieved from versioned files:
{json.dumps(retrieved, separators=(',', ':'))}

The Stage 1 length-varint transposition module used 43,084 bytes on fixed
development validation probes versus xz 46,253 and raw-token transpose+zstd
40,434. Validation bytes are NOT supplied here. Improve the mechanism using
training facts; the frozen independent evaluator reports actual results.

prompt.txt contains the full task and api.rs the same contract. sample.csv is
an optional <=4 KiB training excerpt. At most one optional read_file call;
then write_file codec.rs immediately. Do not reread project files or create
README/tests. The host compiles/tests after the write; shell is disabled.
Explain the transform and its limitations in a few source comments.
{feedback}
"""


def cached_evaluation(root, cfg, identifier, directory):
    plugin = registry.resolve(root, identifier)
    expected = {"freeze_id": freeze.verify(root, cfg)["freeze_id"], "source_sha256": plugin["source_sha256"],
                "inputs": [{"label": label, "bytes": len(data), "sha256": digest(data)}
                           for label, data in evaluator.probes(root, cfg, "validation")],
                "training_sha256": [digest(value) for value in evaluator.training_samples(root, cfg)]}
    key = fingerprint(expected)
    path, stamp = directory / "evaluation.json", directory / "evaluation-cache.json"

    def identity(measured):
        candidate = measured["candidate"]
        artifact = root / "reports/evaluations" / identifier / "validation/archive.alc"
        if (not artifact.is_file() or artifact.is_symlink() or artifact.stat().st_size != candidate["archive_bytes"]
                or sha256(artifact) != candidate["archive_sha256"]):
            raise ValueError("Cached counted archive checksum mismatch")
        return {**expected, "model_sha256": candidate["trained_model_sha256"],
                "build_fingerprint": candidate["build"]["fingerprint"], "archive_sha256": candidate["archive_sha256"]}

    if path.exists() or stamp.exists():
        if not path.exists() or not stamp.exists():
            raise ValueError("Incomplete evaluation cache; inspect retained artifacts")
        record = json.loads(stamp.read_text())
        request_key = record.get("request_key", record["key"])
        if request_key != key or record["result_sha256"] != sha256(path):
            raise ValueError("Evaluation cache key/checksum mismatch")
        measured = json.loads(path.read_text())
        full = identity(measured)
        if record.get("schema_version") == 2 and record["key"] != fingerprint(full):
            raise ValueError("Evaluation model/build cache checksum mismatch")
        if record.get("schema_version") != 2:
            write_json(stamp, {"schema_version": 2, "request_key": key, "key": fingerprint(full),
                               "definition": full, "result_sha256": sha256(path)})
        return measured, True
    command = [sys.executable, "-m", "algomini.evaluator", "--config", str(root / "run.toml"),
               "evaluate", identifier, "--split", "validation"]
    result = subprocess.run(command, cwd=root, env=worker_environment(), capture_output=True, text=True, timeout=1200)
    if result.returncode:
        raise ValueError("Frozen evaluator rejected candidate: " + result.stderr[-3000:])
    original = root / "reports/evaluations" / identifier / "validation/result.json"
    measured = json.loads(original.read_text())
    if (measured["freeze_id"] != expected["freeze_id"] or measured["inputs"] != expected["inputs"]
            or measured["candidate"]["source_sha256"] != plugin["source_sha256"]
            or measured["candidate"]["training_sample_sha256"] != expected["training_sha256"]):
        raise ValueError("Frozen evaluation result provenance mismatch")
    write_json(path, measured)
    full = identity(measured)
    write_json(stamp, {"schema_version": 2, "request_key": key, "key": fingerprint(full), "definition": full, "result_sha256": sha256(path)})
    return measured, False


def measured_summary(evaluated):
    candidate = evaluated["candidate"]
    return {"archive_bytes": candidate["archive_bytes"], "archive_sha256": candidate["archive_sha256"],
            "archive_gain": evaluated["score"]["archive_gain"], "gates": evaluated["score"]["gates"],
            "minimum_decode_mib_per_second": evaluated["score"]["minimum_decode_mib_per_second"],
            "peak_decode_rss_kib": evaluated["score"]["peak_decode_rss_kib"],
            "deployment_saving_bytes": evaluated["score"]["deployment_saving_bytes"],
            "files": [{key: row[key] for key in ("label", "input_bytes", "block_bytes", "raw_escape")}
                      for row in candidate["files"]]}


def observation(root, cfg, policy, profile, identifier, mechanism, *, previous=None):
    directory = root / "reports" / policy["run_id"] / "candidates" / identifier
    directory.mkdir(parents=True, exist_ok=True)
    outcome = directory / "outcome.json"
    if outcome.exists():
        record = json.loads(outcome.read_text())
        if record.get("source_sha256") and sha256(directory / "codec.rs") != record["source_sha256"]:
            raise ValueError("Recorded candidate source changed")
        if record["outcome"] == "measured":
            measured, _ = cached_evaluation(root, cfg, identifier, directory)
            if measured["candidate"]["source_sha256"] != record["source_sha256"] or measured_summary(measured) != record["measurements"]:
                raise ValueError("Recorded measurement provenance changed")
        memory.append(root, record, run_id=policy["run_id"])
        return record
    sample = bounded_sample(development_files(root, cfg, "train")[0], policy["screen_bytes"])
    request_path = directory / "request.json"
    if request_path.exists():
        request = json.loads(request_path.read_text())
        if request["sample_sha256"] != digest(sample):
            raise ValueError("Candidate generation input changed")
    else:
        retrieved = memory.search(root, mechanism + " telemetry csv", limit=policy["retrieve_documents"], max_chars=policy["retrieve_chars"], family="csv")
        prompt = brief(root, cfg, profile, mechanism, retrieved, previous=previous)
        kind = "repair" if previous else "initial"
        attempt_id, _, _ = coder.generation_request(root, cfg, prompt, sample, phase="discovery", kind=kind)
        request = {"prompt": prompt, "prompt_sha256": digest(prompt.encode()), "sample_sha256": digest(sample),
                   "attempt_id": attempt_id, "kind": kind, "retrieved": retrieved}
        write_json(request_path, request)
    record = {"id": identifier, "schema_version": 1, "mechanism": mechanism,
              "tags": ["telemetry", "csv", mechanism], "hypothesis": MECHANISMS[mechanism],
              "parent_source_sha256": previous.get("source_sha256") if previous else None,
              "source_sha256": None, "split": "validation", "profile_sha256": fingerprint(profile),
              "freeze_id": (root / freeze.ANCHOR).read_text().strip(), "generation_attempt_id": request["attempt_id"],
              "kind": request["kind"], "inputs": profile["inputs"], "outcome": "rejected",
              "controller_sha256": {name: sha256(root / f"algomini/{name}.py") for name in ("search", "coder", "research", "memory")},
              "reproduce": ["python", "-m", "algomini", "stage3"]}
    stage = "generation"
    try:
        print(f"[{identifier}] Muse {request['kind']}: {mechanism}", flush=True)
        source, generation = coder.generate(root, cfg, request["prompt"], sample, phase="discovery", kind=request["kind"])
        write_json(directory / "generation.json", generation)
        shutil.copyfile(source, directory / "codec.rs")
        record["source_sha256"] = sha256(source)
        # Exact-source duplicates are recorded without paying for another measurement.
        duplicate = next((item for item in registry.discover(root).values() if item["source_sha256"] == record["source_sha256"]), None)
        if duplicate is not None and duplicate["id"] != identifier:
            record.update(outcome="duplicate", duplicate_of=duplicate["id"])
        else:
            stage = "tiny correctness and training screen"
            measured = codecs.evaluate(root, cfg, source, sample, training_samples=[sample[:2048]])
            measured["scope"] = "Tiny/direct correctness and bounded training screen; not official validation scoring"
            write_json(directory / "screen.json", measured)
            stage = "manifest registration"
            if identifier not in registry.discover(root):
                registry.register(root, identifier, source, provenance={"model": generation["model"], "mechanism": mechanism,
                                  "attempt_id": generation["attempt_id"], "parent_source_sha256": record["parent_source_sha256"]})
            elif registry.resolve(root, identifier)["source_sha256"] != record["source_sha256"]:
                raise ValueError("Plugin id already binds different source; preserve it and use a new run id")
            stage = "frozen validation"
            print(f"[{identifier}] tiny checks passed; frozen validation", flush=True)
            evaluated, _ = cached_evaluation(root, cfg, identifier, directory)
            candidate = evaluated["candidate"]
            record.update(outcome="measured", measurements=measured_summary(evaluated),
                          evidence=(directory / "evaluation.json").relative_to(root).as_posix())
            print(f"[{identifier}] {candidate['archive_bytes']} bytes; gain {evaluated['score']['archive_gain']:.2%}", flush=True)
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        record.update(failure=str(error)[-4000:], failed_stage=stage)
        cached = root / ".cache/coder" / request["attempt_id"]
        if (cached / "generation.json").exists():
            shutil.copyfile(cached / "generation.json", directory / "generation.json")
        source = cached / "workspace/codec.rs"
        if source.is_file() and not source.is_symlink() and 0 < source.stat().st_size <= 65536:
            shutil.copyfile(source, directory / "codec.rs")
            record["source_sha256"] = sha256(source)
        print(f"[{identifier}] rejected at {stage}: {record['failure'][:250]}", flush=True)
    write_json(outcome, record)
    memory.append(root, record, run_id=policy["run_id"])
    return record


def seed_record(root):
    measured = json.loads((root / "reports/stage2/evaluation-first.json").read_text())
    candidate = measured["candidate"]
    source = registry.resolve(root, "muse-telemetry-v1")
    if measured["freeze_id"] != (root / freeze.ANCHOR).read_text().strip() or source["source_sha256"] != candidate["source_sha256"]:
        raise ValueError("Stage 2 seed provenance mismatch")
    return {"id": "muse-telemetry-v1", "mechanism": "length-varint-transpose", "tags": ["csv", "column-layout", "seed"],
            "hypothesis": "Historical Muse raw-token column transposition using per-cell lengths", "parent_source_sha256": None,
            "source_sha256": candidate["source_sha256"], "outcome": "measured", "split": "validation",
            "freeze_id": measured["freeze_id"], "evidence": "reports/stage2/evaluation-first.json",
            "measurements": {"archive_bytes": candidate["archive_bytes"], "archive_gain": measured["score"]["archive_gain"],
                             "gates": measured["score"]["gates"],
                             "files": [{key: row[key] for key in ("label", "input_bytes", "block_bytes", "raw_escape")} for row in candidate["files"]]}}


def budget_extension(previous, current):
    old, new = json.loads(json.dumps(previous)), json.loads(json.dumps(current))
    for field in ("maximum_repairs", "maximum_initial_candidates"):
        if new["policy"][field] < old["policy"][field]:
            return False
        new["policy"][field] = old["policy"][field]
    return old == new


def run(root, cfg, policy_path="search.toml", *, extend_budget=False):
    policy = load_policy(root, policy_path)
    output = root / "reports" / policy["run_id"]
    output.mkdir(parents=True, exist_ok=True)
    with (output / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _run(root, cfg, policy, output, extend_budget=extend_budget)


def _run(root, cfg, policy, output, *, extend_budget=False):
    pinned = freeze.verify(root, cfg)
    version = environment.verify_versions(root, cfg)
    if not version["ok"]:
        raise ValueError("Installed environment differs from the pinned reference")
    profile_path = output / "profile-train.json"
    profile = json.loads(profile_path.read_text()) if profile_path.exists() else research.profile(root, cfg, run_id=policy["run_id"])
    definition = {"policy": policy, "freeze_id": pinned["freeze_id"], "profile_sha256": fingerprint(profile), "coder": cfg["coder"]}
    state_path = output / "run.json"
    if state_path.exists():
        state = json.loads(state_path.read_text())
        if state["definition_sha256"] != fingerprint(definition):
            if not extend_budget or not budget_extension(state["definition"], definition):
                raise ValueError("Search definition changed; preserve this run and choose a new run_id (or explicitly extend only candidate/repair caps)")
            write_json(output / "checkpoints" / (state["definition_sha256"] + "-policy.json"), state)
            memory.append(root, {"id": "budget-extension-" + fingerprint(definition), "tags": ["search-policy", "budget"],
                                "hypothesis": "Use remaining declared project candidate/repair allowance without changing data, model, evaluator or existing requests",
                                "previous_policy": state["definition"]["policy"], "new_policy": policy}, run_id=policy["run_id"])
            state.update(definition=definition, definition_sha256=fingerprint(definition))
            write_json(state_path, state)
    else:
        state = {"started_at": datetime.now(timezone.utc).isoformat(), "definition_sha256": fingerprint(definition), "definition": definition}
        write_json(state_path, state)
    memory.append(root, {"id": "exact-token-profile-v1", "tags": ["csv", "decimal", "prefix", "timestamp", "cross-field"],
                        "split": "train", "hypothesis": profile["hypotheses"], "inputs": profile["inputs"],
                        "evidence": profile_path.relative_to(root).as_posix(), "profile_sha256": fingerprint(profile)}, run_id=policy["run_id"])
    records = [seed_record(root)]
    memory.append(root, records[0], run_id=policy["run_id"])
    repairs, attempted, stop = 0, 0, "initial_pool_complete"
    started = datetime.fromisoformat(state["started_at"])
    for index, mechanism in enumerate(policy["initial_mechanisms"] + policy["expansion_mechanisms"]):
        elites = diverse_elites(records, count=policy["elite_count"], distance=policy["profile_distance"])
        if index >= 4:
            if len(elites) >= policy["minimum_diverse_elites"]:
                stop = "diverse_initial_pool_ready; preserve evolution for Stage 4"
                break
            diagnostics_path = output / "representation-probes.json"
            diagnostics = json.loads(diagnostics_path.read_text()) if diagnostics_path.exists() else {}
            probe = bounded_sample(development_files(root, cfg, "train")[0], cfg["lab"]["baseline_probe_bytes"])
            diagnostic_support = (diagnostics.get("supports_further_breadth", False) and diagnostics.get("split") == "train"
                                  and diagnostics.get("input", {}).get("sha256") == digest(probe))
            if not diagnostic_support and not any(record.get("measurements", {}).get("archive_gain", 0) > records[0]["measurements"]["archive_gain"] for record in records[1:]):
                stop = "no_validation_improvement; inspect profiles before further population breadth"
                break
        if attempted >= policy["maximum_initial_candidates"]:
            stop = "initial_candidate_limit"
            break
        identifier = f"{policy['run_id']}-{mechanism}"
        exists = (output / "candidates" / identifier / "outcome.json").exists()
        if not exists and (datetime.now(timezone.utc) - started).total_seconds() >= policy["maximum_run_seconds"]:
            stop = "time_limit"
            break
        record = observation(root, cfg, policy, profile, identifier, mechanism)
        records.append(record)
        attempted += 1
        repair_number = 0
        while record["outcome"] == "rejected" and repairs < policy["maximum_repairs"]:
            previous = {"failure": record["failure"], "source_sha256": record["source_sha256"]}
            source = output / "candidates" / record["id"] / "codec.rs"
            previous["source"] = source.read_text() if source.exists() else "No codec.rs was written; write the required file immediately."
            repair_number += 1
            repair_id = identifier + f"-r{repair_number}"
            repair_exists = (output / "candidates" / repair_id / "outcome.json").exists()
            if repair_exists or (datetime.now(timezone.utc) - started).total_seconds() < policy["maximum_run_seconds"]:
                record = observation(root, cfg, policy, profile, repair_id, mechanism, previous=previous)
                records.append(record)
                repairs += 1
            else:
                break
    elites = diverse_elites(records, count=policy["elite_count"], distance=policy["profile_distance"])
    ledger = json.loads((root / "reports/budget/ledger.json").read_text())
    discovery = [entry for entry in ledger if entry["phase"] == "discovery"]
    result = {"schema_version": 1, "ok": len(elites) >= policy["minimum_diverse_elites"], "freeze": freeze.verify(root, cfg),
              "initial_candidates": attempted, "repair_sessions": repairs, "elites": elites, "stop_reason": stop,
              "records": [{key: record[key] for key in ("id", "mechanism", "outcome", "source_sha256")} for record in records],
              "discovery_budget": {"attempts": len(discovery), "known_usage_estimate_usd": sum(entry["charged_usd"] for entry in discovery if entry.get("cost_is_estimate")),
                                   "retained_reservations_usd": sum(entry["charged_usd"] for entry in discovery if not entry.get("cost_is_estimate")),
                                   "total_allocated_usd": cfg["budget"]["discovery_usd"]},
              "sealed_test_opened": False, "docker_used": False, "index": memory.rebuild(root),
              "scope": "Development search pool; final candidate selection, ablations, and sealed evaluation remain later stages"}
    if elites:
        leader = next(record for record in records if record["id"] == elites[0]["id"])
        result["leading_candidate"] = {"id": leader["id"], "source_sha256": leader["source_sha256"],
                                       "measurements": leader["measurements"], "evidence": leader["evidence"]}
    result_path = output / "results.json"
    if result_path.exists():
        prior = json.loads(result_path.read_text())
        if prior["records"] != result["records"]:
            checkpoint = output / "checkpoints" / fingerprint(prior)
            write_json(checkpoint.with_suffix(".json"), prior)
            if (output / "report.md").exists():
                shutil.copyfile(output / "report.md", checkpoint.with_suffix(".md"))
    write_json(result_path, result)
    lines = ["# Stage 3: evidence-driven local search", "", f"Frozen evaluator: `{pinned['freeze_id']}`.",
             "Four distinct initial mechanisms are screened on tiny/training inputs before fixed validation. No Docker or sealed test access.", "",
             "| Candidate | Mechanism | Outcome | Corpus bytes | Gain vs baseline |", "|---|---|---|---:|---:|"]
    for record in records:
        metrics = record.get("measurements", {})
        lines.append(f"| {record['id']} | {record['mechanism']} | {record['outcome']} | {metrics.get('archive_bytes', '—')} | " +
                     (f"{metrics['archive_gain']:.2%}" if "archive_gain" in metrics else "—") + " |")
    lines += ["", "Elites: " + ", ".join(item["id"] for item in elites) + ".", f"Stop: {stop}.",
              "The pool favors correct modules with positive archive gain and passing speed/RSS gates, preserving distinct mechanisms and an exploration slot.",
              "Source duplicates are recorded and not remeasured. Requests, source, lineage, failures, train screens, frozen validation, resource measurements and cache fingerprints live beside each candidate.",
              "Retrieval uses versioned observations and project skills with a rebuildable SQLite FTS5 index. No embeddings were needed.",
              f"Discovery known token-based usage estimate: ${result['discovery_budget']['known_usage_estimate_usd']:.4f}; retained incomplete-call reservations: ${result['discovery_budget']['retained_reservations_usd']:.2f}.",
              "Measured archive gains are development results. Deployment package cost is reported separately; no unseen-data or scientific-novelty claim is made.",
              "Reproduce: `python -m algomini stage3`. Completed records reuse verified generation/evaluation caches; replay makes no new paid calls."]
    if elites and elites[0]["id"] != "muse-telemetry-v1":
        leading = json.loads((root / result["leading_candidate"]["evidence"]).read_text())
        metrics = leading["score"]
        lines += ["", f"Leading candidate **{elites[0]['id']}** uses **{elites[0]['archive_bytes']:,} bytes** versus the best baseline's **{leading['baselines']['best_bytes']:,}** and the structured reference's **{leading['reference']['archive_bytes']:,}**.",
                  f"Minimum per-probe decode: **{metrics['minimum_decode_mib_per_second']:.2f} MiB/s**; peak RSS: **{metrics['peak_decode_rss_kib']:,} KiB**. Complete incremental executable: **{leading['candidate']['decoder_package_bytes']:,} bytes**; deployment saving: **{metrics['deployment_saving_bytes']:,} bytes**.",
                  "The structured reference comparison is narrower than the baseline gain; Stage 4 ablations must determine what each transform contributed."]
    (output / "report.md").write_text("\n".join(lines) + "\n")
    return result
