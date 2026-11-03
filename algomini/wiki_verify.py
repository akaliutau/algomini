"""Freeze one Wikipedia codec and consume one later-window verification."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import tomllib

from . import codecs, freeze, wiki_benchmark
from .common import digest, load_config, sha256, write_json


RECORD = "repro/wikipedia-freeze.json"
ANCHOR = "repro/wikipedia-freeze.sha256"
OUTPUT = "reports/wikipedia-search/verification"
ARTIFACTS = "artifacts/wikipedia-selected"


def pin(root, cfg, source, evaluation):
    if (root / RECORD).exists() or (root / ANCHOR).exists():
        raise ValueError("Wikipedia selection already frozen")
    value = json.loads(evaluation.read_text())
    native = freeze.verify(root, cfg)
    if (not value["eligible"] or not value["size_win"] or value["definition"]["source_sha256"] != sha256(source)
            or value["definition"]["input_sha256"] != sha256(root / ".cache/datasets/enwik8-first-1000000.xml")
            or value["definition"]["harness_sha256"] != sha256(root / "algomini/wiki_benchmark.py")
            or value["definition"]["objective_sha256"] != sha256(root / "reports/wikipedia-search/objective.json")):
        raise ValueError("Selection is not the measured, eligible development winner")
    directory = root / ARTIFACTS
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "codec.rs").write_bytes(source.read_bytes())
    (directory / "model.bin").write_bytes(b"")
    write_json(directory / "development.json", value)
    write_json(directory / "build.json", value["candidate"]["build"])
    paths = [str(path.relative_to(root)) for path in sorted(directory.iterdir())]
    paths += ["algomini/wiki_benchmark.py", "algomini/wiki_verify.py", "run.toml", "wiki-search.toml",
              "datasets/wikipedia/manifest.json", "reports/wikipedia-search/objective.json",
              "reports/wikipedia-search/baseline-policy.json", "reports/wikipedia-search/dictionary-training.json"]
    record = {"schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
              "evaluator_freeze_id": native["freeze_id"], "source_sha256": sha256(source),
              "binary_sha256": value["candidate"]["build"]["binary_sha256"],
              "files": {path: sha256(root / path) for path in paths},
              "window": tomllib.loads((root / "wiki-search.toml").read_text())["later_window"],
              "selection": "Smallest eligible complete archive; speed secondary; all parameters in frozen source",
              "candidate_training": "No external model; deterministic adaptive counts reconstructed from decoded bytes",
              "once": "Commit this freeze before reading the reserved window; claim first; no tuning after any outcome",
              "trust": "Commit and externally pin this record ID; adjacent checksum alone is not authentication"}
    write_json(root / RECORD, record)
    (root / ANCHOR).write_text(sha256(root / RECORD) + "\n")
    return verify(root, cfg)


def verify(root, cfg):
    identifier = sha256(root / RECORD)
    if identifier != (root / ANCHOR).read_text().strip():
        raise ValueError("Wikipedia freeze checksum mismatch")
    record = json.loads((root / RECORD).read_text())
    if any(sha256(root / path) != expected for path, expected in record["files"].items()):
        raise ValueError("Frozen Wikipedia files changed")
    freeze.verify(root, cfg, expected_id=record["evaluator_freeze_id"])
    _, build = codecs.build(root, cfg, root / ARTIFACTS / "codec.rs")
    if build["binary_sha256"] != record["binary_sha256"]:
        raise ValueError("Frozen Wikipedia binary changed")
    return record, identifier


def claim(root, receipt):
    path = root / OUTPUT / "consumed.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as stream:
        json.dump(receipt, stream, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def replay(root, identifier):
    directory = root / OUTPUT
    if not (directory / "terminal.json").is_file():
        raise ValueError("Verification was consumed without a final outcome; never rerun")
    terminal = json.loads((directory / "terminal.json").read_text())
    if (terminal["freeze_id"] != identifier or terminal["receipt_sha256"] != sha256(directory / "consumed.json")
            or terminal["result_sha256"] != sha256(directory / "result.json")):
        raise ValueError("Final Wikipedia outcome checksum mismatch")
    return json.loads((directory / "result.json").read_text())


def run(root, cfg):
    frozen, identifier = verify(root, cfg)
    directory = root / OUTPUT
    if (directory / "consumed.json").exists():
        return replay(root, identifier)
    committed = [RECORD, ANCHOR, *frozen["files"]]
    for command in (["git", "ls-files", "--error-unmatch", "--", *committed],
                    ["git", "diff", "--exit-code", "HEAD", "--", *committed]):
        completed = subprocess.run(command, cwd=root, capture_output=True)
        if completed.returncode:
            raise ValueError("Commit the exact frozen protocol and codec before final verification")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    claim(root, {"freeze_id": identifier, "pre_verification_commit": head,
                 "claimed_at": datetime.now(timezone.utc).isoformat(), "window": frozen["window"]})
    try:
        window = frozen["window"]
        data = wiki_benchmark.window(root, window["offset"], window["bytes"], final=True)
        preflight = (root / ".cache/datasets/enwik8-first-1000000.xml").read_bytes()[:2048]
        result = wiki_benchmark.measure(root, cfg, root / ARTIFACTS / "codec.rs", data,
                   label=f"enwik8-offset-{window['offset']}.xml", publish=False, preflight=preflight)
        verify(root, cfg)
        result.update(freeze_id=identifier, input_sha256=digest(data),
                      scope="One later window of the same public corpus; no tuning after this outcome",
                      size_first_win=result["eligible"] and result["size_win"])
    except Exception as error:
        result = {"freeze_id": identifier, "size_first_win": False, "failure": str(error),
                  "scope": "Final verification failed; run consumed; no retuning or retry"}
    write_json(directory / "result.json", result)
    write_json(directory / "terminal.json", {"freeze_id": identifier,
               "receipt_sha256": sha256(directory / "consumed.json"), "result_sha256": sha256(directory / "result.json")})
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    selection = commands.add_parser("pin")
    selection.add_argument("source", type=Path)
    selection.add_argument("evaluation", type=Path)
    commands.add_parser("run")
    commands.add_parser("status")
    args = parser.parse_args()
    root, cfg = load_config("run.toml")
    if args.command == "pin":
        _, identifier = pin(root, cfg, args.source, args.evaluation)
        print(json.dumps({"freeze_id": identifier}))
    else:
        result = run(root, cfg) if args.command == "run" else replay(root, verify(root, cfg)[1])
        print(json.dumps(result, indent=2))
