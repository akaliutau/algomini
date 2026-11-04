"""One bounded development search and a distinct single-use final verifier."""

import copy
import json
from pathlib import Path
import re
import shutil
import struct
import time

from .. import archive, coder, memory, semantic
from ..sampling import fingerprints, overlap, partition
from ..campaigns import Campaign
from ..common import digest, load_config, sha256, write_json
from ..environment import check_api
from . import native
from .contracts import DatasetFile, execution_guard, fetch_dataset, validate_snapshot


def configuration(library, request):
    _, original = load_config(Path(library) / "run.toml")
    cfg = copy.deepcopy(original)
    rules = request["policy"]
    # The readonly Git checkout has no .tools directory. The executable was
    # checksum-verified into the immutable image alongside this module.
    tool_root = Path(__file__).parents[2]
    cfg["coder"]["executable"] = str((tool_root / cfg["coder"]["executable"]).resolve())
    cfg["coder"]["api_source"] = str((tool_root / "crates/algomini-cli/src/api.rs").resolve())
    cfg["budget"].update(total_usd=rules["request_reservation_usd"], framework_usd=0, discovery_usd=rules["request_reservation_usd"],
        refinement_usd=0, reserve_usd=0, initial_candidates=rules["maximum_initial_candidates"],
        evolved_candidates=rules["maximum_evolved_candidates"], repair_sessions=rules["maximum_repairs"],
        generation_enabled=request.get("mode", "generate") == "generate")
    cfg["evaluation"]["decode_repetitions"] = rules["decode_repetitions"]
    return cfg


def persist_asset(workspace, source, model, base):
    checksum, model_hash = sha256(source), digest(model)
    match = next((row for row in base["codecs"] if row["source_sha256"] == checksum and row["model_sha256"] == model_hash), None)
    if match:
        return match
    identifier = "cx-" + checksum[:32] + "-" + model_hash[:16]
    directory = Path(workspace) / "plugins" / identifier
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "codec.rs").write_bytes(source.read_bytes())
    (directory / "model.bin").write_bytes(model)
    item = {"id": identifier, "codec_id": checksum[:32], "source": f"plugins/{identifier}/codec.rs", "source_sha256": checksum,
            "model": f"plugins/{identifier}/model.bin", "model_sha256": model_hash}
    note = " ".join(line.lstrip()[2:].strip() for line in source.read_text().splitlines()[:6] if line.lstrip().startswith("//"))[:240]
    if note:
        item["description"] = note
    return item


def registry_for(base, item, name):
    registry = copy.deepcopy(base)
    if not any(row["id"] == item["id"] for row in registry["codecs"]):
        registry["codecs"].append(item)
    registry["aliases"][name] = item["id"]
    return registry


def prompt(library, request, prepared, previous=None, failure=None, feedback=None):
    api = (Path(library) / "crates/algomini-cli/src/api.rs").read_text()
    parent = previous.read_text() if previous else "No parent source; choose a reversible representation."
    return f"""Write exactly one file codec.rs implementing a lossless compressor for {request['dataset']['family']} bytes.
Interface supplied by the trusted crate (do not redefine it):
{api}
Use `use crate::{{Codec, CodecError}};`, export `pub struct Candidate;`, and implement Codec.
Allowed safe helpers: crate::toolkit::zstd_compress(input, level), zstd_decompress(input), crc32(input).
No external crates, unsafe code, files, processes, environment, network, or extra modules. Maximum source 65536 bytes.
All transform tables, exceptions and learned models must be serialized and counted. Do not embed sample records.
Preserve arbitrary bytes, whitespace, numeric spellings, quoting and line endings. Handle empty/binary/malformed input exactly.
The native wrapper supplies raw fallback, bounds and framing; direct tiny controls bypass that fallback.
Optimize complete archive size first; speed second. Hard limits: 256 MiB, encode 30 seconds, decode 10 seconds.
You only see development data. Never request test data. sample.bin is a bounded training preview, not necessarily a complete document.
Use complete-record layouts when supported and reversible byte fallbacks for fragments and binary content.
The host trains on one development partition and measures on the other. Learned state must fit 65536 bytes and is counted in full.
Compare model + payload + native frame + collection identity, not the transformed payload alone.
Do not return the parent unchanged or merely wrap zstd again; propose a mechanism supported by the measured gap.
If useful, start encode output with a documented mode byte so source review can distinguish structured and fallback paths.
Development probe evidence and bounded retrieved knowledge:
{json.dumps({'probes': prepared['probes'], 'retrieved': prepared['retrieved']}, sort_keys=True)}
Measured development feedback (no final data):
{json.dumps(feedback or {}, sort_keys=True)}
Parent (a reviewed seed or previous measured candidate; evolve only when supported):
{parent}
Previous failure, if any: {failure or 'none'}
"""


def trial_summary(row):
    candidate = row["result"]["candidate"]
    return {"id": row["item"]["id"], "origin": row["origin"], "eligible": row["result"]["eligible"],
            "archive_gain": row["result"]["score"]["archive_gain"],
            **{key: candidate.get(key) for key in ("archive_bytes", "block_bytes", "payload_bytes", "model_bytes", "collection_bytes", "encoding_mode", "raw_escape")}}


def audit_attempts(workspace):
    """Retain reviewed source and bounded redacted diagnostics, never prompts/data."""
    workspace = Path(workspace)
    ledger = workspace / "reports/budget/ledger.json"
    entries = json.loads(ledger.read_text()) if ledger.is_file() else []
    files = {}
    for entry in entries:
        identifier = entry["attempt_id"]
        if not re.fullmatch(r"[0-9a-f]{64}", identifier):
            raise ValueError("Invalid coder attempt identity")
        directory = workspace / ".cache/coder" / identifier
        for source, name, cap in ((directory / "generation.json", "generation.json", 32768),
                                 (directory / "workspace/codec.rs", "codec.rs", 65536),
                                 (directory / "model.bin", "model.bin", 65536),
                                 (directory / "stderr.txt", "stderr.txt", 16384),
                                 (directory / "events.jsonl", "events-tail.jsonl", 32768)):
            if not source.is_file() or source.is_symlink():
                continue
            data = source.read_bytes()[-cap:]
            relative = f"attempts/{identifier}/{name}"
            target = workspace / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            files[relative] = digest(data)
    return {"schema_version": 1, "files": files, "scope": "Bounded source and redacted CLI diagnostics; losing attempts included"}


def knowledge_library(library, workspace):
    prior = workspace / "prior-observations.json"
    if not prior.is_file():
        return library
    rows = json.loads(prior.read_text())
    if not isinstance(rows, list) or len(rows) > 128 or any(row.get("split") not in {"train", "validation"} for row in rows):
        raise ValueError("Invalid prior development evidence")
    destination = workspace / "knowledge"
    for source in memory.sources(library):
        relative = source.relative_to(library)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        if source.name == "SKILL.md" and source.with_name("metadata.json").is_file():
            shutil.copyfile(source.with_name("metadata.json"), target.with_name("metadata.json"))
    target = destination / "reports/reused/observations.jsonl"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows))
    for name in ("catalog.jsonl", "vectors.jsonl", "index-manifest.json"):
        source = library / "memory" / name
        if source.is_file():
            target = destination / "memory" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
    return destination


def search(library, workspace, request):
    library, workspace = Path(library), Path(workspace)
    validate_snapshot(request, library)
    execution_guard(request.get("execution"))
    workspace.mkdir(parents=True, exist_ok=True)
    terminal = workspace / "research.json"
    if terminal.exists():
        check_freeze(library, workspace, request)
        return json.loads(terminal.read_text())
    started = time.monotonic()
    cfg = configuration(library, request)
    if cfg["budget"]["generation_enabled"]:
        if not request.get("execution"):
            raise ValueError("Paid cloud research requires its durable Actions execution claim")
    train = fetch_dataset(request, "train", workspace)
    write_json(workspace / "dataset-manifest.json", request["dataset"])
    original = workspace / "train.bin"
    original.write_bytes(train)
    knowledge = knowledge_library(library, workspace)
    embedder = semantic.load(library) or semantic.load(Path(__file__).parents[2])
    preparation = Campaign.create(workspace / "campaign", knowledge, original, campaign_id=request["id"], family=request["dataset"]["family"]).prepare(embedder=embedder)
    base = json.loads((library / "release/registry.json").read_text())
    framing_floor = min(len(archive.pack(item["id"], [("sample.bin", bytes(40))])) for item in base["codecs"])
    for relative, data in native.assets(library, base).items():
        path = workspace / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    training, validation, sampling = partition(train, request["dataset"]["family"])
    write_json(workspace / "sampling.json", sampling)
    write_json(workspace / "development-signatures.json", fingerprints(train, request["dataset"]["family"]))
    dictionary, dictionary_record = native.reference_dictionary(workspace, cfg, training)
    baseline = native.references(cfg, validation, dictionary)
    write_json(workspace / "preparation.json", preparation)
    write_json(workspace / "dictionary.json", dictionary_record)
    write_json(workspace / "cloud-runtime.json", native.runtime(library, request["image"]))
    candidates, failures = [], []

    def evaluate(source, origin, generation=None):
        previous = next((row for row in candidates if row["item"]["source_sha256"] == sha256(source)), None)
        if previous:
            row = {**previous, "origin": origin, "generation": generation, "duplicate_of": previous["item"]["id"]}
            candidates.append(row)
            return row
        item = persist_asset(workspace, source, b"", base)
        registry = registry_for(base, item, request["plugin"])
        binary, _ = native.build(library, workspace, registry, asset_root=workspace, image=request["image"])
        training_frame = struct.pack("<I", len(training)) + training if training else b""
        model, _ = native.invoke(binary, item["id"], cfg, 3, training_frame)
        if generation:
            model_path = workspace / ".cache/coder" / generation["attempt_id"] / "model.bin"
            model_path.parent.mkdir(parents=True, exist_ok=True)
            model_path.write_bytes(model)
        item = persist_asset(workspace, source, model, base)
        registry = registry_for(base, item, request["plugin"])
        binary, build = native.build(library, workspace, registry, asset_root=workspace, image=request["image"])
        checks = native.correctness(binary, item, cfg, training, model)
        result, _ = native.measure(binary, item, cfg, validation, model, baseline, checks=checks)
        row = {"item": item, "registry": registry, "build": build, "result": result, "origin": origin, "generation": generation}
        candidates.append(row)
        write_json(workspace / "candidates" / (item["id"] + ".json"), row)
        return row

    seed_alias = {"csv": "telemetry-csv", "json": "json-lexical", "binary": "json-lexical"}.get(request["dataset"]["family"], "wiki-xml")
    seeds = list(dict.fromkeys((base["aliases"][seed_alias], base["aliases"]["json-lexical"])))
    seed = next(row for row in base["codecs"] if row["id"] == seeds[0])
    for identifier in seeds:
        item = next(row for row in base["codecs"] if row["id"] == identifier)
        try:
            evaluate(workspace / item["source"], "reused reviewed seed; not a newly generated algorithm")
        except ValueError as error:
            failures.append({"origin": "seed", "id": identifier, "failure": str(error)})
    if cfg["budget"]["generation_enabled"] and baseline["best_bytes"] > framing_floor:
        preflight = check_api(library, cfg)
        if not preflight["ok"]:
            raise ValueError("Model catalog preflight failed before inference: " + json.dumps(preflight, sort_keys=True))
        write_json(workspace / "model-preflight.json", preflight)
        # Each launch also reserves against the request-local $20 ledger in coder.generate.
        attempts = ["initial"] * request["policy"]["maximum_initial_candidates"] + ["evolved"] * request["policy"]["maximum_evolved_candidates"]
        repair_used = 0
        for attempt, kind in enumerate(attempts):
            if time.monotonic() - started >= request["policy"]["maximum_research_seconds"]:
                break
            best = min((row for row in candidates if row["result"]["eligible"]), key=lambda row: row["result"]["candidate"]["archive_bytes"], default=None)
            parent = workspace / best["item"]["source"] if best else workspace / seed["source"]
            feedback = {"sampling": sampling, "best_baseline_bytes": baseline["best_bytes"], "best_baseline": baseline["best_id"],
                "target_gain": request["policy"]["archive_gain_target"], "trials": [trial_summary(row) for row in candidates], "failures": failures[-3:]}
            text = prompt(library, request, preparation, parent, feedback=feedback) + f"\nTrial {attempt + 1}: propose a distinct reversible refinement, and explain its mechanism/applicability briefly in source comments.\n"
            source = None
            try:
                source, record = coder.generate(workspace, cfg, text, training, attempt=attempt, phase="discovery", kind=kind)
                evaluate(source, "Muse-generated " + kind, record)
            except ValueError as error:
                failures.append({"origin": kind, "failure": str(error)})
                if source is not None and repair_used < request["policy"]["maximum_repairs"] and time.monotonic() - started < request["policy"]["maximum_research_seconds"]:
                    repair_used += 1
                    try:
                        repair_prompt = prompt(library, request, preparation, source, str(error), feedback)
                        repaired, record = coder.generate(workspace, cfg, repair_prompt, training, attempt=attempt, phase="discovery", kind="repair")
                        evaluate(repaired, "Muse-generated repair", record)
                    except ValueError as repair_error:
                        failures.append({"origin": "repair", "failure": str(repair_error)})
            if candidates and any(row["generation"] for row in candidates) and min(row["result"]["candidate"]["archive_bytes"] for row in candidates) <= baseline["best_bytes"] * (1 - request["policy"]["archive_gain_target"]):
                break
    passing = [row for row in candidates if row["result"]["eligible"]]
    if not passing:
        raise ValueError("No candidate passed development correctness/resource controls")
    selected = min(passing, key=lambda row: (row["result"]["candidate"]["archive_bytes"], -row["result"]["score"]["minimum_decode_mib_per_second"]))
    item, registry = selected["item"], selected["registry"]
    binary, build = native.build(library, workspace, registry, asset_root=workspace, image=request["image"])
    model = (workspace / item["model"]).read_bytes()
    development, packed = native.measure(binary, item, cfg, train, model, native.references(cfg, train, dictionary), checks=selected["result"]["candidate"]["correctness"])
    development["console"] = native.console_roundtrip(binary, cfg, train, item["id"], packed)
    write_json(workspace / "development.json", development)
    (workspace / "development.alc").write_bytes(packed)
    shutil.copyfile(binary, workspace / "algomini")
    (workspace / "algomini").chmod(0o755)
    write_json(workspace / "registry.json", registry)
    write_json(workspace / "build.json", build)
    write_json(workspace / "request.json", request)
    ledger = workspace / "reports/budget/ledger.json"
    usage = json.loads(ledger.read_text()) if ledger.exists() else []
    result = {"schema_version": 1, "request_id": request["id"], "selected": item, "origin": selected["origin"],
              "development": development, "failures": failures, "usage": usage,
              "trials": [{key: row[key] for key in ("item", "origin", "result", "generation")} for row in candidates],
              "charged_usd": sum(row["charged_usd"] for row in usage), "search_seconds": time.monotonic() - started,
              "mode": request.get("mode", "generate"), "split": "validation", "retrieved": preparation["retrieved"]}
    result["validation"] = selected["result"]
    result["sampling"] = sampling
    result["minimum_candidate_archive_bytes"] = framing_floor
    result["generation_skipped_for_framing_floor"] = baseline["best_bytes"] <= framing_floor
    result["development_gate"] = {"passed": all(value["eligible"] and value["score"]["archive_gain"] > request["policy"]["release_minimum_archive_gain"] for value in (selected["result"], development)),
                                  "requirement": "Correct, within resources and smaller than the strongest baseline on validation and full development"}
    write_json(workspace / "attempts.json", audit_attempts(workspace))
    result["environment"] = native.host_environment()
    write_json(terminal, result)
    freeze_record = {"schema_version": 1, "request_sha256": sha256(workspace / "request.json"),
        "files": {name: sha256(workspace / name) for name in ("algomini", "registry.json", "build.json", "research.json", "development.json", "development.alc", "sampling.json", "development-signatures.json", "attempts.json", "preparation.json", "cloud-runtime.json", "dataset-manifest.json", "dictionary.json", "dictionary/dictionary.bin", item["source"], item["model"])},
        "runtime": native.runtime(library, request["image"]), "selected": item}
    write_json(workspace / "freeze.json", freeze_record)
    return result


def check_freeze(library, workspace, request):
    validate_snapshot(request, library)
    workspace = Path(workspace)
    record = json.loads((workspace / "freeze.json").read_text())
    if json.loads((workspace / "request.json").read_text()) != request or record["request_sha256"] != sha256(workspace / "request.json") or record["runtime"] != native.runtime(library, request["image"]):
        raise ValueError("Frozen cloud request/runtime changed")
    for name, checksum in record["files"].items():
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts or not (workspace / relative).resolve().is_relative_to(workspace.resolve()) or (workspace / relative).is_symlink() or sha256(workspace / relative) != checksum:
            raise ValueError("Frozen cloud artifact checksum mismatch")
    return record, sha256(workspace / "freeze.json")


def verify(library, workspace, request, receipt):
    workspace = Path(workspace)
    frozen, identifier = check_freeze(library, workspace, request)
    if not json.loads((workspace / "research.json").read_text())["development_gate"]["passed"]:
        raise ValueError("Development did not qualify; final input must remain closed")
    if request.get("execution") and not receipt.get("execution"):
        raise ValueError("Missing durable final-test execution claim")
    execution_guard(receipt.get("execution"))
    if receipt["freeze_id"] != identifier or receipt["request_id"] != request["id"] or not receipt.get("test_consumed_at"):
        raise ValueError("Missing durable final-test claim")
    if (workspace / "verification-started.json").exists():
        raise ValueError("Verification already consumed in this workspace")
    # Remote claim is authoritative across runners; local exclusive creation also catches accidental repeats.
    with (workspace / "verification-started.json").open("x") as stream:
        json.dump(receipt, stream)
    result = {"schema_version": 1, "request_id": request["id"], "freeze_id": identifier,
              "split": "test", "scope": "One frozen final file; no tuning afterwards", "size_first_win": False,
              "test_previously_used": bool(receipt.get("test_previously_used"))}
    result["environment"] = native.host_environment()
    try:
        cfg = configuration(library, request)
        data = fetch_dataset(request, "test", workspace)
        result["record_overlap"] = overlap(json.loads((workspace / "development-signatures.json").read_text()), data, request["dataset"]["family"])
        item = frozen["selected"]
        model = (workspace / item["model"]).read_bytes()
        measured, packed = native.measure(workspace / "algomini", item, cfg, data, model,
            native.references(cfg, data, workspace / "dictionary/dictionary.bin"), checks={"passed": True, "source": "frozen development controls"})
        measured["console"] = native.console_roundtrip(workspace / "algomini", cfg, data, item["id"], packed)
        result.update(measured)
        (workspace / "final.alc").write_bytes(packed)
        check_freeze(library, workspace, request)
    except Exception as error:
        result["failure"] = str(error)
    write_json(workspace / "final.json", result)
    write_json(workspace / "terminal.json", {"freeze_id": identifier, "result_sha256": sha256(workspace / "final.json"), "receipt": receipt})
    return result
