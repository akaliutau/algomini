"""Explicit cloud job entry points; secrets stay in the jobs that need them."""

import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

from ..common import load_config, write_json
from ..execution import run_isolated
from . import coordinator, native, research
from .github import GitHub


def export(workspace, destination):
    workspace, destination = Path(workspace), Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    names = ["request.json", "failure.json"]
    if (workspace / "freeze.json").exists():
        names += ["freeze.json", "registry.json", "build.json", "research.json", "development.json", "development.alc",
                  "dictionary.json", "dictionary/dictionary.bin", "cloud-runtime.json", "algomini", "preparation.json",
                  "final.json", "terminal.json", "verification-started.json", "model-preflight.json"]
        registry = json.loads((workspace / "registry.json").read_text())
        names += list(native.assets(workspace, registry))
    for name in names:
        source = workspace / name
        if source.is_file() and not source.is_symlink():
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)


def smoke(library, workspace, image):
    _, cfg = load_config(library / "run.toml")
    workspace.mkdir(parents=True, exist_ok=True)
    registry = json.loads((library / "release/registry.json").read_text())
    binary, build = native.build(library, workspace, registry, image=image)
    probes = []
    with tempfile.TemporaryDirectory(prefix="cloud-denial-") as temporary:
        directory = Path(temporary)
        protected = directory / "original-secret"
        protected.write_text("must not be visible to worker")
        work = directory / "empty"
        work.mkdir()
        script = """import json,os,socket,sys
denied=[]
for operation in (lambda:open(sys.argv[1],'rb').read(),lambda:socket.socket(),lambda:os.fork()):
    try: operation();denied.append(False)
    except PermissionError:denied.append(True)
print(json.dumps(denied))
"""
        result = run_isolated([sys.executable, "-I", "-c", script, protected], cwd=work, ro=[sys.prefix], timeout=10)
        if result["returncode"] or json.loads(result["stdout"]) != [True, True, True]:
            raise ValueError("Cloud file/network/process containment failed")
    for item in registry["codecs"]:
        data = b"a,b\r\n1.00,-0\r\n" * 60 if "s4-" in item["id"] else b"<record>the quick brown fox</record>\n" * 60
        model = (library / item["model"]).read_bytes()
        checks = native.correctness(binary, item, cfg, data, model)
        encoded, _ = native.invoke(binary, item["id"], cfg, 6, data, model=model, source_sha256=item["source_sha256"])
        from ..archive import pack
        packed = pack(item["id"], [("sample.bin", encoded)], model if encoded[4] == 2 else b"")
        probes.append({"codec": item["id"], "checks": checks, "console": native.console_roundtrip(binary, cfg, data, item["id"], packed)})
    value = {"ok": True, "image": image, "build": build, "codecs": probes, "model_calls": 0,
             "containment": {"original_read_denied": True, "network_denied": True, "fork_denied": True}}
    write_json(workspace / "smoke.json", value)
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", type=Path, default=Path.cwd())
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("gate")
    p.add_argument("--event", type=Path, required=True)
    p.add_argument("--event-name", required=True)
    p.add_argument("--output", type=Path, required=True)
    p = commands.add_parser("claim")
    p.add_argument("--intake", type=Path, required=True)
    p.add_argument("--image", required=True)
    p.add_argument("--output", type=Path, required=True)
    for command in ("research", "freeze", "verify", "publish"):
        p = commands.add_parser(command)
        p.add_argument("--workspace", type=Path, required=True)
        if command in {"research", "verify"}:
            p.add_argument("--export", type=Path, required=True)
        if command in {"freeze", "verify"}:
            p.add_argument("--receipt", type=Path, required=True)
        if command == "publish":
            p.add_argument("--issue", type=int, required=True)
    p = commands.add_parser("smoke")
    p.add_argument("--workspace", type=Path, required=True)
    p.add_argument("--image", default="local")
    for command in ("release-build", "release-publish"):
        p = commands.add_parser(command)
        p.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.library.resolve()
    if hasattr(args, "workspace"):
        args.workspace = args.workspace.resolve()
    try:
        if args.command in {"gate", "claim", "freeze", "publish", "release-publish"}:
            gh = GitHub("akaliutau/algomini")
        if args.command == "gate":
            coordinator.gate(root, json.loads(args.event.read_text()), args.event_name, args.output, gh)
        elif args.command == "claim":
            coordinator.claim(root, json.loads(args.intake.read_text()), args.image, args.output, gh)
        elif args.command == "research":
            request = json.loads((args.workspace / "request.json").read_text())
            from .contracts import execution_guard
            execution_guard(request.get("execution"))
            try:
                research.search(root, args.workspace, request)
            except Exception as error:
                ledger = args.workspace / "reports/budget/ledger.json"
                usage = json.loads(ledger.read_text()) if ledger.exists() else []
                write_json(args.workspace / "failure.json", {"failure": str(error), "usage": usage, "charged_usd": sum(row["charged_usd"] for row in usage)})
                raise
            finally:
                export(args.workspace, args.export)
        elif args.command == "freeze":
            request = json.loads((args.workspace / "request.json").read_text())
            coordinator.freeze(root, args.workspace, request, args.receipt, gh)
        elif args.command == "verify":
            request = json.loads((args.workspace / "request.json").read_text())
            receipt = json.loads(args.receipt.read_text())
            from .contracts import execution_guard
            execution_guard(receipt.get("execution"))
            (args.workspace / "algomini").chmod(0o755)
            try:
                research.verify(root, args.workspace, request, receipt)
            finally:
                export(args.workspace, args.export)
        elif args.command == "publish":
            coordinator.publish(root, args.workspace, args.issue, gh)
        elif args.command == "smoke":
            smoke(root, args.workspace, args.image)
        else:
            from . import releasing
            if args.command == "release-build":
                releasing.build_release(root, args.output)
            else:
                releasing.publish_release(root, args.output, gh)
        return 0
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        print("algomini-cloud: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
