"""Run from a checkout with: python -m algomini --help."""

import argparse
from datetime import datetime, timezone
import json
import sys

from .common import load_config, sha256, write_json


def write_report(root, cfg, checks, profile, baselines):
    rows = ["# Stage 0: local experiment foundation", "",
            "Prepared locally from the pinned UCI Appliances Energy Prediction archive (CC BY 4.0).",
            "Original CSV bytes are preserved. Chronological day groups use a one-day gap at each split boundary.", "",
            f"Training profile: {profile['input']['bytes']:,} bytes, {profile['input']['rows']} records, {profile['input']['columns']} columns.",
            f"Baseline probe: {baselines['input']['bytes']:,} training bytes; all compression inputs stay below 2 MiB.", "",
            "| Baseline | Payload bytes | Model bytes | Total bytes | Exact reconstruction |",
            "|---|---:|---:|---:|---|"]
    for item in baselines["results"]:
        rows.append(f"| {item['id']} | {item['payload_bytes']} | {item['model_bytes']} | {item['archive_bytes']} | yes |")
    reference = baselines["reference"]
    rows += ["", f"The strongest baseline on this probe is **{baselines['best_baseline']}**.",
             f"The trusted raw-token transpose + zstd-19 reference uses **{reference['archive_bytes']} bytes**, "
             f"a **{reference['gain_vs_best_baseline']:.2%}** reduction on this training probe. Its framing is included.",
             "This is a known representation reference implemented in Python, not a Muse-generated codec or sealed-test result.",
             "No incremental decoder package saving has been established. Dictionary bytes are counted once for this one-probe workload; "
             "the final evaluator must account for sharing once across the declared corpus.", "",
             "Measured structure: " + "; ".join(profile["hypotheses"]), "",
             "API preflight: " + ("authenticated model catalog succeeded" if checks.get("api", {}).get("ok") else "offline presence/CLI checks only") + ". No model inference was performed.",
             "Candidate-code containment belongs to Stage 2. The current tools execute installed, trusted binaries only.",
             "Before paid research, verify the provider spending ceiling and explicitly enable generation in run.toml.", "",
             "Reproduce: `python -m algomini stage0 --check-api`. Check the captured environment with `python -m algomini versions --check`.",
             "See `repro/README.md`, `summary.json`, `profile-train.json`, `baselines-train.json`, and `observations.jsonl`."]
    (root / cfg["report_dir"] / "report.md").write_text("\n".join(rows) + "\n")


def main():
    parser = argparse.ArgumentParser(description="Local Algomini experiment tools")
    parser.add_argument("--config", default="run.toml")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("bootstrap-muse", help="Install/verify the pinned official Muse binary")
    version_parser = commands.add_parser("versions", help="Capture tool, OS, Python, and Conda versions")
    version_parser.add_argument("--check", action="store_true", help="Compare installed versions with the captured reference")
    commands.add_parser("prepare", help="Download and split the pinned source without normalizing bytes")
    commands.add_parser("stage1", help="Generate one Rust codec, compile it, and independently check its measurements")
    commands.add_parser("stage2", help="Freeze and verify the complete local development evaluator; no model calls")
    commands.add_parser("plugins", help="Discover source-verified plugin manifests")
    commands.add_parser("freeze-status", help="Verify trusted code, configuration, and native toolchain pins")
    evaluation_parser = commands.add_parser("evaluate", help="Measure a manifest plugin on fixed development probes")
    evaluation_parser.add_argument("codec")
    evaluation_parser.add_argument("--split", choices=("train", "validation"), default="validation")
    registration_parser = commands.add_parser("register-codec", help="Register a new source-only Rust plugin version")
    registration_parser.add_argument("id")
    registration_parser.add_argument("source")
    codec_parser = commands.add_parser("check-codec", help="Compile/check a Rust codec offline without model credentials")
    codec_parser.add_argument("source")
    for command in ("doctor", "stage0"):
        sub = commands.add_parser(command, help="Run preflight" if command == "doctor" else "Run the complete local Stage 0")
        sub.add_argument("--check-api", action="store_true", help="Authenticate GET /models; performs no model inference")
    for command in ("profile", "baselines"):
        sub = commands.add_parser(command, help="Run a bounded development-data probe")
        sub.add_argument("--split", choices=("train", "validation"), default="train")
    args = parser.parse_args()
    try:
        root, cfg = load_config(args.config)
        from . import dataset, environment, lab
        if args.command == "bootstrap-muse":
            result = environment.bootstrap_muse(root, cfg)
        elif args.command == "versions":
            if args.check:
                result = environment.verify_versions(root, cfg)
            else:
                environment.snapshot(root, cfg)
                result = {"environment": "repro/environment.json", "python_lock": "requirements.lock"}
        elif args.command == "doctor":
            result = environment.doctor(root, cfg, args.check_api)
        elif args.command == "prepare":
            manifest = dataset.prepare(root, cfg)
            result = {"manifest": cfg["dataset"]["manifest"],
                      "development_chunks": {name: len(paths) for name, paths in manifest["development"].items()},
                      "sealed_manifest_sha256": manifest["sealed"]["manifest_sha256"]}
        elif args.command == "profile":
            result = lab.profile(root, cfg, args.split)
        elif args.command == "baselines":
            result = lab.baselines(root, cfg, args.split)
        elif args.command == "stage1":
            from .discovery import first_loop
            result = first_loop(root, cfg)
        elif args.command == "stage2":
            from .stage2 import acceptance
            result = acceptance(root, cfg)
        elif args.command == "plugins":
            from .registry import discover
            result = {"plugins": [{key: value for key, value in plugin.items() if key != "source_path"}
                                  for plugin in discover(root).values()]}
        elif args.command == "freeze-status":
            from .freeze import verify
            result = verify(root, cfg)
        elif args.command == "evaluate":
            from .evaluator import evaluate
            full = evaluate(root, cfg, args.codec, args.split)
            result = {"score": full["score"], "freeze_id": full["freeze_id"],
                      "result": f"reports/evaluations/{args.codec}/{args.split}/result.json"}
        elif args.command == "register-codec":
            from .registry import register
            result = register(root, args.id, args.source)
        elif args.command == "check-codec":
            from pathlib import Path
            from .codecs import evaluate
            sample = dataset.bounded_sample(dataset.development_files(root, cfg, "train")[0], cfg["lab"]["baseline_probe_bytes"])
            training = dataset.bounded_sample(dataset.development_files(root, cfg, "train")[0], 2048)
            result = evaluate(root, cfg, Path(args.source).resolve(), sample, training_samples=[training])
        else:
            checks = environment.doctor(root, cfg, args.check_api)
            if not checks["ok"]:
                print(json.dumps(checks, indent=2))
                return 1
            if (root / "repro/environment.json").exists():
                version_check = environment.verify_versions(root, cfg)
                if not version_check["ok"]:
                    print(json.dumps(version_check, indent=2))
                    return 1
            else:
                environment.snapshot(root, cfg)
            manifest = dataset.prepare(root, cfg)
            profile = lab.profile(root, cfg)
            baselines = lab.baselines(root, cfg)
            report_dir = root / cfg["report_dir"]
            run_time = datetime.now(timezone.utc).isoformat()
            observations = [{"id": run_time + "/structure", "split": "train", "input": profile["input"],
                             "tags": ["telemetry", "csv", "timestamp", "cross-field"],
                             "hypotheses": profile["hypotheses"],
                             "measurements": {key: profile[key] for key in ("identical_lexeme_columns", "timestamp", "strongest_numeric_pairs")},
                             "evidence": "profile-train.json", "reproduce": profile["reproduce"]},
                            {"id": run_time + "/baselines", "split": "train", "input": baselines["input"],
                             "tags": ["compression", "column-transpose", "baselines"],
                             "measurements": baselines["results"], "reference": baselines["reference"],
                             "dictionary": baselines["dictionary"], "best_baseline": baselines["best_baseline"],
                             "evidence": "baselines-train.json", "reproduce": baselines["reproduce"]}]
            with (report_dir / "observations.jsonl").open("a") as stream:
                for record in observations:
                    record["environment_sha256"] = sha256(root / "repro/environment.json")
                    stream.write(json.dumps(record, sort_keys=True, allow_nan=False) + "\n")
            write_report(root, cfg, checks, profile, baselines)
            result = {"schema_version": 1, "local_foundation_ready": True,
                      "dataset_manifest": cfg["dataset"]["manifest"],
                      "sealed_manifest_sha256": manifest["sealed"]["manifest_sha256"],
                      "profile_input_bytes": profile["input"]["bytes"],
                      "baseline_input_bytes": baselines["input"]["bytes"],
                      "baseline_roundtrips": len(baselines["results"]),
                      "best_baseline": baselines["best_baseline"],
                      "reference_gain": baselines["reference"]["gain_vs_best_baseline"],
                      "inference_calls": 0,
                      "next_stage_requirements": ["First generated codec and fixed Rust wrapper in Stage 1",
                                                  "Candidate-code isolation and final evaluator in Stage 2",
                                                  "Verify provider spending cap before enabling paid research"],
                      "artifacts": {str(path.relative_to(root)): sha256(path)
                                    for path in [report_dir / "preflight.json", report_dir / "profile-train.json",
                                                 report_dir / "baselines-train.json", report_dir / "report.md",
                                                 root / "repro/environment.json"]}}
            write_json(report_dir / "summary.json", result)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 1 if result.get("ok") is False else 0
    except (OSError, ValueError, KeyError) as error:
        # Internal operations sanitize credential-bearing network failures before this boundary.
        print(f"algomini: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
