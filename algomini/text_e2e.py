"""Reproducible complex-text integration; no generation or new unseen-data claim."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from zipfile import ZipFile

from . import archive, baselines, evaluator, installation, installed, memory, sealed
from .campaigns import Campaign
from .common import digest, download, sha256, worker_environment, write_json


FIXTURE = "datasets/wikipedia/manifest.json"
IMPLEMENTATIONS = ("text_e2e", "campaigns", "memory", "labtools", "semantic", "_embed")


def extract_prefix(archive_path, spec, destination):
    """Stream-verify the published member; persist only the bounded requested prefix."""
    member, prefix = spec["member"], spec["prefix"]
    if prefix["offset"] != 0 or not 0 < prefix["bytes"] <= 2 * 1024**2:
        raise ValueError("Invalid fixture prefix bound")
    md5, sha1, result, total = hashlib.md5(), hashlib.sha1(), bytearray(), 0
    with ZipFile(archive_path) as zipped:
        if zipped.namelist() != [member["name"]] or zipped.getinfo(member["name"]).file_size != member["bytes"]:
            raise ValueError("Unexpected corpus archive members/dimensions")
        with zipped.open(member["name"]) as source:
            while block := source.read(65536):
                total += len(block)
                if total > member["bytes"]:
                    raise ValueError("Corpus member exceeds its bound")
                md5.update(block)
                sha1.update(block)
                result.extend(block[:max(0, prefix["bytes"] - len(result))])
    if (total != member["bytes"] or md5.hexdigest() != member["md5"] or sha1.hexdigest() != member["sha1"]
            or len(result) != prefix["bytes"] or digest(result) != prefix["sha256"]):
        raise ValueError("Published corpus or prefix checksum mismatch")
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(result)
    return destination


def prepare_fixture(root):
    spec = json.loads((root / FIXTURE).read_text())
    path = root / spec["prefix"]["cache_path"]
    if path.exists():
        if path.stat().st_size != spec["prefix"]["bytes"] or sha256(path) != spec["prefix"]["sha256"]:
            raise ValueError("Cached Wikipedia prefix checksum mismatch")
        return path, spec
    source = root / ".cache/datasets/enwik8.zip"
    download(spec["archive"]["url"], source, spec["archive"]["sha256"], spec["archive"]["bytes"])
    return extract_prefix(source, spec, path), spec


def clean_roundtrip(package, package_id, data, label):
    with tempfile.TemporaryDirectory(prefix="algomini-text-install-") as temporary:
        temporary = Path(temporary)
        plugin = temporary / "plugin"
        installation.install(package, plugin, expected_id=package_id)
        original = temporary / label
        original.write_bytes(data)
        compressed = temporary / "encoded.alc"
        env = worker_environment()
        env["PATH"] = "/usr/bin:/bin"
        command = [sys.executable, "-I", "-B", str(plugin / "decode.py"), "--plugin", str(plugin)]
        subprocess.run([*command, "encode", str(original), str(compressed)], cwd=temporary,
                       env=env, check=True, capture_output=True, timeout=120)
        original.unlink()
        decoded = subprocess.run([*command, "decode", str(compressed), str(temporary / "decoded")], cwd=temporary,
                                 env=env, check=True, capture_output=True, timeout=120, text=True)
        proof = json.loads(decoded.stdout)
        restored = temporary / "decoded" / label
        if restored.stat().st_size != len(data) or sha256(restored) != digest(data):
            raise ValueError("Installed complex-text reconstruction failed")
        installed.verify(plugin, expected_id=package_id)
        return compressed.read_bytes(), {"ok": True, "input_bytes": len(data), "sha256": digest(data),
            "original_removed_before_decode": True, "fresh_install_and_cwd": True,
            "isolated_python_imports": True, "native_workers_contained": True,
            "compiler_not_needed": True, "decoded_files": proof["files"]}


def compare_baselines(cfg, data, label):
    results = []
    for baseline in cfg["baselines"]:
        if baseline.get("dictionary"):
            continue  # No new training corpus in this integration fixture.
        encode = [part.replace("{input_bytes}", str(len(data))) for part in baseline["encode"]]
        encoded, encoding = baselines.measure(encode, data, cfg)
        measurements = []
        for _ in range(cfg["evaluation"]["decode_repetitions"]):
            restored, measurement = baselines.measure(baseline["decode"], encoded, cfg)
            if restored != data:
                raise ValueError("Complex-text baseline reconstruction failed")
            measurements.append(measurement)
        result, _ = evaluator.counted(baseline["id"], [(label, encoded)], b"", [{"label": label,
            "input_bytes": len(data), "block_bytes": len(encoded), "encode": encoding,
            "decode": evaluator.resources(measurements, len(data))}])
        results.append(result)
    return results


def run(root, cfg, *, output=None):
    root = Path(root)
    output = Path(output) if output else root / "reports/midstage2"
    protocol, _ = sealed.verify_protocol(root, cfg)
    selected = json.loads((root / "reports/stage5/summary.json").read_text())
    package = root / selected["package"]
    manifest, package_id = installed.verify(package, expected_id=selected["package_id"])
    definition = {"schema_version": 1, "package_id": package_id, "fixture_manifest_sha256": sha256(root / FIXTURE),
                  "implementations": {name: sha256(root / f"algomini/{name}.py") for name in IMPLEMENTATIONS},
                  "evaluator_freeze_id": protocol["evaluator_freeze_id"], "baseline_definitions": cfg["baselines"]}
    key = digest(json.dumps(definition, sort_keys=True).encode())
    result_path, terminal = output / "result.json", output / "terminal.json"
    if result_path.exists() or terminal.exists():
        if not result_path.is_file() or not terminal.is_file() or json.loads(terminal.read_text())["result_sha256"] != sha256(result_path):
            raise ValueError("Complex-text result cache checksum mismatch")
        state = json.loads(terminal.read_text())
        for name, checksum in state["artifacts"].items():
            relative = Path(name)
            if relative.is_absolute() or ".." in relative.parts or sha256(output / relative) != checksum:
                raise ValueError("Integration campaign artifact checksum mismatch")
        result = json.loads(result_path.read_text())
        if result["key"] != key:
            raise ValueError("Integration definition changed; choose a new --output directory")
        return result
    path, fixture = prepare_fixture(root)
    with path.open("rb") as stream:
        data = stream.read(2 * 1024**2 + 1)
    if len(data) != fixture["prefix"]["bytes"] or len(data) > cfg["lab"]["max_input_bytes"] or digest(data) != fixture["prefix"]["sha256"]:
        raise ValueError("Invalid integration fixture bytes")
    prepared = {}
    with tempfile.TemporaryDirectory(prefix="algomini-text-campaigns-") as temporary:
        temporary = Path(temporary)
        json_sample = temporary / "events.jsonl"
        json_sample.write_bytes(b'{"event":"sample","x":-0.0,"x":1e0}\n{"event":"sample","x":1.00}\n')
        for name, sample, family in (("wikipedia", path, "xml"), ("json-control", json_sample, "json")):
            campaign = Campaign.create(temporary / name, root, sample, campaign_id=name, family=family)
            prepared[name] = campaign.prepare()
            write_json(output / "campaigns" / f"{name}.json", prepared[name])
    packed, proof = clean_roundtrip(package, package_id, data, path.name)
    baseline = compare_baselines(cfg, data, path.name)
    method, model, blocks = archive.unpack(packed)
    if method != manifest["id"]:
        raise ValueError("Integration archive method mismatch")
    best = min(baseline, key=lambda row: row["archive_bytes"])
    result = {"schema_version": 1, "ok": True, "key": key, "definition": definition,
              "fixture": fixture, "input_bytes": len(data), "input_sha256": digest(data), "campaigns": prepared,
              "package_id": package_id, "archive_bytes": len(packed), "archive_sha256": digest(packed),
              "model_bytes": len(model), "blocks": len(blocks), "installation": proof, "baselines": baseline,
              "best_baseline": best["id"], "archive_gain": 1 - len(packed) / best["archive_bytes"],
              "paid_model_calls": 0, "candidate_training_calls": 0, "docker_used": False,
              "scope": "Public integration fixture; existing frozen codec, no generated text specialist or unseen-data claim"}
    write_json(result_path, result)
    artifacts = {str(path.relative_to(output)): sha256(path) for path in (output / "campaigns").glob("*.json")}
    write_json(terminal, {"result_sha256": sha256(result_path), "artifacts": artifacts})
    cache = root / ".cache/text-e2e" / key
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "archive.alc").write_bytes(packed)
    memory.append(root, {"id": "wikipedia-text-integration", "tags": ["xml", "text", "integration", "fallback"],
        "split": "train", "hypothesis": "Validate the existing codec's general-byte fallback on a complex Wikipedia prefix",
        "input_sha256": digest(data), "outcome": "exact reconstruction", "measurements": {"archive_bytes": len(packed),
        "best_baseline_bytes": best["archive_bytes"], "archive_gain": result["archive_gain"]},
        "scope": result["scope"], "evidence": "result.json"}, directory=output)
    lines = ["# M2: isolated Wikipedia integration", "", f"Exact input: **{len(data):,} bytes**, SHA-256 `{digest(data)}`.",
        "The fixture is the unmodified prefix of enwik8, the published March 2006 Wikipedia XML benchmark. Full-member published MD5/SHA-1 and archive/prefix SHA-256 are pinned.",
        "Two independent campaign workspaces prepared XML and JSON research briefs. Their inputs/caches remained separate; retrieval stayed within four documents/6,000 excerpt characters.",
        "A fresh compiled installation encoded the complete prefix, deleted its supplied original and reconstructed the exact hash in a separate directory. No compiler, paid generation, candidate training or Docker was used.", "",
        "| Method | Complete collection bytes |", "|---|---:|"]
    lines += [f"| {row['id']} | {row['archive_bytes']} |" for row in baseline]
    lines += [f"| Existing frozen candidate | {len(packed)} |", "",
        f"Best dictionary-free baseline: **{best['id']}**. Existing candidate archive gain: **{result['archive_gain']:.2%}**.",
        "This tests integration and exact fallback behavior. It is not a new text-optimized algorithm, a new sealed evaluation, or a claim of improved compression. Dictionary baselines are excluded because this fixture introduces no training corpus.",
        "Raw/source/encoded fixture data stay in ignored caches. Campaign reports, native baseline resources, provenance and hashes are in `result.json` and `campaigns/`.",
        "Replay: `python -m algomini text-e2e`. A new `--output` directory performs another integration run while preserving this record."]
    (output / "report.md").write_text("\n".join(lines) + "\n")
    return result
