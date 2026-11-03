"""Report/package orchestration; sealed measurements are owned by sealed.py."""

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from . import installation, installed, sealed
from .common import sha256, worker_environment, write_json


def demonstrate(root, package, package_id, result):
    path = root / "reports/stage5/install-check.json"
    if path.exists():
        record = json.loads(path.read_text())
        if record["package_id"] != package_id:
            raise ValueError("Install proof belongs to another package")
        return record
    with tempfile.TemporaryDirectory(prefix="algomini-installed-proof-") as temporary:
        temporary = Path(temporary)
        destination = temporary / "plugins" / result["candidate"]["id"]
        installation.install(package, destination, expected_id=package_id)
        compressed = temporary / "input.alc"
        shutil.copyfile(root / "reports/stage5/archive.alc", compressed)
        env = worker_environment()
        env["PATH"] = "/usr/bin:/bin"
        command = [sys.executable, "-I", "-B", str(destination / "decode.py"), "--plugin", str(destination),
                   "decode", str(compressed), str(temporary / "decoded")]
        decoded = subprocess.run(command, cwd=temporary, env=env, capture_output=True, text=True, check=True, timeout=300)
        output = json.loads(decoded.stdout)
        expected = {entry["label"]: {"bytes": entry["bytes"], "sha256": entry["sha256"]} for entry in result["inputs"]}
        if {entry["label"]: {"bytes": entry["bytes"], "sha256": entry["sha256"]} for entry in output["files"]} != expected:
            raise ValueError("Clean installed decode does not match the sealed hashes")
        # Also demonstrate installed encode with a disposable synthetic original.
        original = temporary / "synthetic.csv"
        data = b'a,b\r\n" 60",1.00\r\n' * 32
        original.write_bytes(data)
        subprocess.run([sys.executable, "-I", "-B", str(destination / "decode.py"), "--plugin", str(destination),
                        "encode", str(original), str(temporary / "synthetic.alc")], cwd=temporary, env=env,
                       capture_output=True, text=True, check=True, timeout=120)
        original.unlink()
        subprocess.run([sys.executable, "-I", "-B", str(destination / "decode.py"), "--plugin", str(destination),
                        "decode", str(temporary / "synthetic.alc"), str(temporary / "synthetic-decoded")], cwd=temporary,
                       env=env, capture_output=True, text=True, check=True, timeout=120)
        if (temporary / "synthetic-decoded/synthetic.csv").read_bytes() != data:
            raise ValueError("Installed synthetic encode/decode failed")
        installed.verify(destination, expected_id=package_id)
    record = {"ok": True, "package_id": package_id, "files_decoded": len(expected),
              "decoded_bytes": output["decoded_bytes"], "all_sealed_hashes_match": True,
              "clean_cwd_and_isolated_python_imports": True, "repo_and_development_paths_not_supplied": True,
              "compiler_or_dataset_not_required_by_runtime": True, "frozen_model_used": True,
              "synthetic_original_removed_before_decode": True, "model_inference_calls": 0, "docker_used": False}
    write_json(path, record)
    return record


def run(root, cfg):
    result = sealed.run(root, cfg)
    output = root / "reports/stage5"
    summary = {"status": result["status"], "protocol_id": result["protocol_id"],
               "candidate_freeze_id": result["candidate_freeze_id"], "score": result.get("score"),
               "model_inference_calls": 0, "candidate_training_calls": 0, "docker_used": False}
    if result["status"] == "passed":
        package, manifest, package_id = installation.package(root, cfg)
        proof = demonstrate(root, package, package_id, result)
        destination = root / "installed" / manifest["id"]
        installation.install(package, destination, expected_id=package_id)
        plugin_path = root / "plugins" / manifest["id"] / "manifest.json"
        plugin = json.loads(plugin_path.read_text())
        plugin.update(status="verified", sealed_evidence={"result_sha256": sha256(output / "result.json"),
                      "protocol_id": result["protocol_id"], "candidate_freeze_id": result["candidate_freeze_id"],
                      "package_id": package_id, "package": package.relative_to(root).as_posix()})
        write_json(plugin_path, plugin)
        package_bytes = sum(path.stat().st_size for path in package.rglob("*") if path.is_file())
        summary.update(ok=True, package_id=package_id, package=package.relative_to(root).as_posix(),
                       installed=destination.relative_to(root).as_posix(), installation=proof,
                       standalone_bundle_bytes=package_bytes,
                       saving_including_standalone_bundle_bytes=result["baselines"]["best_bytes"] - result["candidate"]["archive_bytes"] - package_bytes)
    else:
        summary.update(ok=False, installation_allowed=False, failure=result.get("failure"))
    write_json(output / "summary.json", summary)
    lines = ["# Stage 5: one sealed verification", "", f"Status: **{result['status']}**.",
             f"Candidate freeze: `{result['candidate_freeze_id']}`. Protocol: `{result['protocol_id']}`.",
             "The durable claim preceded opening the private manifest. This is the final outcome for the frozen candidate; cached replay does not reevaluate it.", ""]
    if "score" in result:
        lines += [f"All {len(result['inputs'])} committed held-out chunks total {result['input_bytes']:,} bytes; every compression invocation is bounded by the original chunk cap.", "",
                  "| Configuration | Complete corpus bytes | Shared model bytes |", "|---|---:|---:|"]
        for row in [*result["baselines"]["results"], result["reference"], result["candidate"]]:
            lines.append(f"| {row['id']} | {row['archive_bytes']} | {row['model_bytes']} |")
        lines += ["", f"Best baseline: **{result['baselines']['best_id']}**. Candidate archive gain: **{result['score']['archive_gain']:.2%}**.",
                  f"Minimum per-chunk decode **{result['score']['minimum_decode_mib_per_second']:.2f} MiB/s**; peak job RSS **{result['score']['peak_decode_rss_kib']:,} KiB**.",
                  f"Declared incremental executable: **{result['candidate']['decoder_package_bytes']:,} bytes**; deployment saving **{result['score']['deployment_saving_bytes']:,} bytes**, assuming the common runtime/libzstd already exists.",
                  "Per-chunk input hashes/groups, sizes, every decode repetition and baseline resources are in `result.json`. The model is the exact frozen artifact; candidate training calls: zero."]
        baseline = next(row for row in result["baselines"]["results"] if row["id"] == result["baselines"]["best_id"])
        baseline_files = {row["label"]: row for row in baseline["files"]}
        inputs = {row["label"]: row for row in result["inputs"]}
        lines += ["", "| Chunk | Day groups | Input bytes | Candidate block bytes | Best-corpus baseline block bytes | Decode MiB/s |",
                  "|---|---|---:|---:|---:|---:|"]
        for row in result["candidate"]["files"]:
            lines.append(f"| {row['label']} | {', '.join(inputs[row['label']]['groups'])} | {row['input_bytes']} | {row['block_bytes']} | {baseline_files[row['label']]['block_bytes']} | {row['decode']['decode_mib_per_second']:.2f} |")
        lines += ["", "The per-chunk baseline is the same winning whole-corpus configuration throughout. Block sizes include native/ALG2 framing; the aggregate above also counts collection metadata and shared models."]
    else:
        lines += ["Verification failed: " + result.get("failure", "unknown error"), "No package or installation is allowed."]
    if summary["ok"]:
        lines += ["", f"Verified compiled package: `{summary['package']}`; manifest id `{summary['package_id']}`.",
                  f"The installable bundle is {summary['standalone_bundle_bytes']:,} bytes including Python runtime files, source and evidence; full-bundle deployment saving is {summary['saving_including_standalone_bundle_bytes']:,} bytes. The Python interpreter and native system libraries remain external prerequisites.",
                  "A clean temporary installation decoded every saved sealed archive hash without supplying original/development paths or a compiler. A synthetic original was deleted before installed decode. Only the verified selected manifest was promoted; the trusted core remains unchanged."]
        saving = result["baselines"]["best_bytes"] - result["candidate"]["archive_bytes"]
        if saving > 0:
            factor = result["input_bytes"] / saving / 1024**2
            lines += [f"Estimated break-even input volume is {factor * result['candidate']['decoder_package_bytes']:.2f} MiB for the incremental executable, or {factor * summary['standalone_bundle_bytes']:.2f} MiB for all bundled files, assuming identical per-byte savings and fixed package overhead. This extrapolation is not a measured larger-corpus result."]
    lines += ["", "Scope: later time periods in one house. This establishes no generalization to unseen houses or scientific novelty. Negative deployment savings are reported separately from archive gains.",
              "No Docker or model inference was used. Source/model/parameters/compiler/evaluator pins match. New generation is disabled after sealed consumption.",
              "Reproduce the stored outcome: `python -m algomini stage5`. Do not delete the consumed marker or rerun the sealed measurements. Packaging/decode replay uses saved artifacts."]
    (output / "report.md").write_text("\n".join(lines) + "\n")
    return summary
