"""Small entry point for the maintained laboratory and environment tools."""

import argparse
import json
import sys

from .common import load_config
from . import lab_cli


def main():
    parser = argparse.ArgumentParser(description="Algomini research laboratory")
    parser.add_argument("--config", default="run.toml")
    commands = parser.add_subparsers(dest="command", required=True)
    lab_cli.configure(commands)
    commands.add_parser("bootstrap-muse", help="Install and verify the pinned coding CLI")
    commands.add_parser("versions", help="Record current tool and Python versions")
    doctor = commands.add_parser("doctor", help="Check local prerequisites without inference")
    doctor.add_argument("--check-api", action="store_true")
    args = parser.parse_args()
    try:
        root, cfg = load_config(args.config)
        if args.command in lab_cli.COMMANDS:
            result = lab_cli.run(args, root, cfg)
        else:
            from . import environment
            if args.command == "bootstrap-muse":
                result = environment.bootstrap_muse(root, cfg)
            elif args.command == "doctor":
                result = environment.doctor(root, cfg, args.check_api)
            else:
                result = environment.snapshot(root, cfg, destination=".cache/environment.json", write_locks=False)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 1 if result.get("ok") is False else 0
    except (OSError, ValueError, KeyError) as error:
        print(f"algomini-lab: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
