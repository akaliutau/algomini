"""One bounded development search and a distinct single-use final verifier."""

import copy
import json
from pathlib import Path
import shutil
import struct
import time

from .. import coder
from ..campaigns import Campaign
from ..common import digest, load_config, sha256, write_json
from ..environment import check_api
from . import native
from .contracts import DatasetFile, execution_guard, validate_snapshot


def configuration(library, request):
    _, original = load_config(Path(library) / "run.toml")
    cfg = copy.deepcopy(original)
    rules = request["policy"]
    cfg["coder"]["executable"] = str((Path(library) / cfg["coder"]["executable"]).resolve())
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
    identifier = "cx-" + checksum[:16] + "-" + model_hash[:8]
    directory = Path(workspace) / "plugins" / identifier
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "codec.rs").write_bytes(source.read_bytes())
    (directory / "model.bin").write_bytes(model)
    return {"id": identifier, "codec_id": checksum[:32], "source": f"plugins/{identifier}/codec.rs", "source_sha256": checksum,
            "model": f"plugins/{identifier}/model.bin", "model_sha256": model_hash}


def registry_for(base, item, name):
    registry = copy.deepcopy(base)
    if not any(row["id"] == item["id"] for row in registry["codecs"]):
        registry["codecs"].append(item)
    registry["aliases"][name] = item["id"]
    return registry


def prompt(library, request, prepared, previous=None, failure=None):
    api = (Path(library) / "rust/codec-worker/src/api.rs").read_text()
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
You only see development data. Never request test data. Read at most sample.csv once and write codec.rs promptly.
Development probe evidence and bounded retrieved knowledge:
{json.dumps({'probes': prepared['probes'], 'retrieved': prepared['retrieved']}, sort_keys=True)}
Parent (a reviewed seed or previous measured candidate; evolve only when supported):
{parent}
Previous failure, if any: {failure or 'none'}
"""


def search(library, workspace, request):
    library, workspace = Path(library), Path(workspace)
    validate_snapshot(request, library)
    execution_guard(request.get("execution"))
    workspace.mkdir(parents=True, exist_ok=True)
    terminal = workspace / "research.json"
    if terminal.exists():
        return json.loads(terminal.read_text())
    started = time.monotonic()
    cfg = configuration(library, request)
    if cfg["budget"]["generation_enabled"]:
        preflight = check_api(library, cfg)
        if not preflight["ok"]:
            raise ValueError("Model catalog preflight failed before inference: " + str(preflight.get("http_status", preflight.get("reason"))))
        write_json(workspace / "model-preflight.json", preflight)
    train = DatasetFile.parse(request["dataset"]["train"], request["policy"]).fetch(request["policy"])
    original = workspace / "train.bin"
    original.write_bytes(train)
    preparation = Campaign.create(workspace / "campaign", library, original, campaign_id=request["id"], family=request["dataset"]["family"]).prepare()
    base = json.loads((library / "release/registry.json").read_text())
    for relative, data in native.assets(library, base).items():
        path = workspace / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    shutil.copytree(library / "rust/codec-worker", workspace / "rust/codec-worker", ignore=shutil.ignore_patterns("target"))
    training = train[:max(1, len(train) // 2)]
    validation = train[max(1, len(train) // 2):] or train
    dictionary, dictionary_record = native.reference_dictionary(workspace, cfg, training)
    baseline = native.references(cfg, validation, dictionary)
    write_json(workspace / "preparation.json", preparation)
    write_json(workspace / "dictionary.json", dictionary_record)
    write_json(workspace / "cloud-runtime.json", native.runtime(library, request["image"]))
    candidates, failures = [], []

    def evaluate(source, origin, generation=None):
        item = persist_asset(workspace, source, b"", base)
        registry = registry_for(base, item, request["plugin"])
        binary, _ = native.build(library, workspace, registry, asset_root=workspace, image=request["image"])
        training_frame = struct.pack("<I", len(training)) + training
        model, _ = native.invoke(binary, item["id"], cfg, 3, training_frame)
        item = persist_asset(workspace, source, model, base)
        registry = registry_for(base, item, request["plugin"])
        binary, build = native.build(library, workspace, registry, asset_root=workspace, image=request["image"])
        checks = native.correctness(binary, item, cfg, training, model)
        result, _ = native.measure(binary, item, cfg, validation, model, baseline, checks=checks)
        row = {"item": item, "registry": registry, "build": build, "result": result, "origin": origin, "generation": generation}
        candidates.append(row)
        write_json(workspace / "candidates" / (item["id"] + ".json"), row)
        return row

    seed_alias = "telemetry-csv" if request["dataset"]["family"] == "csv" else "wiki-xml"
    seed = next(row for row in base["codecs"] if row["id"] == base["aliases"][seed_alias])
    try:
        evaluate(workspace / seed["source"], "reused reviewed seed; not a newly generated algorithm")
    except ValueError as error:
        failures.append({"origin": "seed", "failure": str(error)})
    if cfg["budget"]["generation_enabled"]:
        # Each launch also reserves against the request-local $20 ledger in coder.generate.
        attempts = ["initial"] * request["policy"]["maximum_initial_candidates"] + ["evolved"] * request["policy"]["maximum_evolved_candidates"]
        repair_used = 0
        for attempt, kind in enumerate(attempts):
            if time.monotonic() - started >= request["policy"]["maximum_research_seconds"]:
                break
            best = min(candidates, key=lambda row: row["result"]["candidate"]["archive_bytes"], default=None)
            parent = workspace / best["item"]["source"] if best else workspace / seed["source"]
            text = prompt(library, request, preparation, parent) + f"\nTrial {attempt + 1}: propose a distinct reversible refinement, and explain its mechanism/applicability briefly in source comments.\n"
            try:
                source, record = coder.generate(workspace, cfg, text, training, attempt=attempt, phase="discovery", kind=kind)
                evaluate(source, "Muse-generated " + kind, record)
            except ValueError as error:
                failures.append({"origin": kind, "failure": str(error)})
                if repair_used < request["policy"]["maximum_repairs"] and time.monotonic() - started < request["policy"]["maximum_research_seconds"]:
                    repair_used += 1
                    try:
                        repair_prompt = prompt(library, request, preparation, parent, str(error))
                        repaired, record = coder.generate(workspace, cfg, repair_prompt, training, attempt=attempt, phase="discovery", kind="repair")
                        evaluate(repaired, "Muse-generated repair", record)
                    except ValueError as repair_error:
                        failures.append({"origin": "repair", "failure": str(repair_error)})
            if candidates and any(row["generation"] for row in candidates) and min(row["result"]["candidate"]["archive_bytes"] for row in candidates) <= baseline["best_bytes"] * .9:
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
    write_json(terminal, result)
    freeze_record = {"schema_version": 1, "request_sha256": sha256(workspace / "request.json"),
        "files": {name: sha256(workspace / name) for name in ("algomini", "registry.json", "build.json", "research.json", "development.json", "development.alc", "dictionary.json", "dictionary/dictionary.bin", item["source"], item["model"])},
        "runtime": native.runtime(library, request["image"]), "selected": item}
    write_json(workspace / "freeze.json", freeze_record)
    return result


def check_freeze(library, workspace, request):
    validate_snapshot(request, library)
    workspace = Path(workspace)
    record = json.loads((workspace / "freeze.json").read_text())
    if record["request_sha256"] != sha256(workspace / "request.json") or record["runtime"] != native.runtime(library, request["image"]):
        raise ValueError("Frozen cloud request/runtime changed")
    for name, checksum in record["files"].items():
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts or (workspace / relative).is_symlink() or sha256(workspace / relative) != checksum:
            raise ValueError("Frozen cloud artifact checksum mismatch")
    return record, sha256(workspace / "freeze.json")


def verify(library, workspace, request, receipt):
    workspace = Path(workspace)
    frozen, identifier = check_freeze(library, workspace, request)
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
    try:
        cfg = configuration(library, request)
        data = DatasetFile.parse(request["dataset"]["test"], request["policy"]).fetch(request["policy"])
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
