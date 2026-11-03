"""Run from a checkout with: python -m algomini --help."""

import argparse
import json
import sys

from .common import load_config
from . import lab_cli



def main():
    parser = argparse.ArgumentParser(description="Local Algomini experiment tools")
    parser.add_argument("--config", default="run.toml")
    commands = parser.add_subparsers(dest="command", required=True)
    lab_cli.configure(commands)
    commands.add_parser("bootstrap-muse", help="Install/verify the pinned official Muse binary")
    version_parser = commands.add_parser("versions", help="Capture tool, OS, Python, and Conda versions")
    version_parser.add_argument("--check", action="store_true", help="Compare installed versions with the captured reference")
    commands.add_parser("prepare", help="Download and split the pinned source without normalizing bytes")
    commands.add_parser("stage1", help="Generate one Rust codec, compile it, and independently check its measurements")
    commands.add_parser("stage2", help="Freeze and verify the complete local development evaluator; no model calls")
    search_parser = commands.add_parser("stage3", help="Run/resume bounded Muse mechanism search using the frozen evaluator")
    search_parser.add_argument("--policy", default="search.toml")
    search_parser.add_argument("--extend-budget", action="store_true", help="Record an explicit increase of candidate/repair caps within project limits; retain the original deadline")
    refine_parser = commands.add_parser("stage4", help="Run controlled ablations, bounded refinement, and freeze a validation-selected candidate")
    refine_parser.add_argument("--policy", default="refine.toml")
    refine_parser.add_argument("--measure-only", action="store_true", help="Complete deterministic ablations/sweeps before paid refinement or final selection")
    selected_parser = commands.add_parser("selected-status", help="Verify the exact Stage 4 source/model/parameter freeze")
    selected_parser.add_argument("--build", action="store_true", help="Rebuild offline and check the selected executable hash")
    commands.add_parser("pin-sealed-verifier", help="Pin the Stage 5 verifier/runtime before opening any sealed file")
    commands.add_parser("stage5", help="Consume one frozen sealed evaluation or replay its stored result; install only on success")
    install_parser = commands.add_parser("install-codec", help="Install a verified compiled package using its reviewed manifest hash")
    install_parser.add_argument("package")
    install_parser.add_argument("destination")
    install_parser.add_argument("--package-id", required=True)
    commands.add_parser("research-profile", help="Profile exact training lexemes, deltas and token offsets")
    commands.add_parser("representation-probes", help="Measure exact training-only layout/byte-plane hypotheses before expanding search")
    memory_parser = commands.add_parser("research-memory", help="Rebuild or query versioned research/skill evidence")
    memory_parser.add_argument("query", nargs="?")
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
        if args.command in lab_cli.COMMANDS:
            result = lab_cli.run(args, root, cfg)
        elif args.command == "bootstrap-muse":
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
        elif args.command == "stage3":
            from .search import run
            result = run(root, cfg, args.policy, extend_budget=args.extend_budget)
        elif args.command == "stage4":
            from .refinement import run
            result = run(root, cfg, args.policy, measure_only=args.measure_only)
        elif args.command == "selected-status":
            from .selection import verify
            result = verify(root, cfg, rebuild=args.build)
        elif args.command == "pin-sealed-verifier":
            from .sealed import pin
            result = pin(root, cfg)
        elif args.command == "stage5":
            from .stage5 import run
            result = run(root, cfg)
        elif args.command == "install-codec":
            from .installation import install
            result = install(args.package, args.destination, expected_id=args.package_id)
        elif args.command == "research-profile":
            from .research import profile
            full = profile(root, cfg)
            result = {"split": full["split"], "inputs": full["inputs"], "result": "reports/stage3/profile-train.json"}
        elif args.command == "representation-probes":
            from .research import representation_probes
            full = representation_probes(root, cfg)
            result = {key: full[key] for key in ("split", "best_id", "best_payload_bytes", "reference_payload_bytes", "supports_further_breadth")}
        elif args.command == "research-memory":
            from .memory import rebuild, search
            result = {"matches": search(root, args.query)} if args.query else rebuild(root)
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
            from .foundation import run
            result = run(root, cfg, args.check_api)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 1 if result.get("ok") is False else 0
    except (OSError, ValueError, KeyError) as error:
        # Internal operations sanitize credential-bearing network failures before this boundary.
        print(f"algomini: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
