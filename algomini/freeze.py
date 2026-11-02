"""Pin the trusted evaluator independently of mutable search and plugin files."""

from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import re
import shutil
import sys

from .common import sha256, write_json
from .environment import command_output


RECORD = "repro/evaluator-freeze.json"
ANCHOR = "repro/evaluator-freeze.sha256"
PYTHON = ("__init__", "common", "dataset", "environment", "baselines", "archive", "execution",
          "_isolate", "codecs", "registry", "evaluator", "freeze")
CONFIG = ("schema_version", "seed", "dataset", "split", "lab", "limits", "objective", "baselines", "evaluation")


def code_hashes(root):
    paths = [root / f"algomini/{name}.py" for name in PYTHON]
    tree = [path for path in (root / "rust/codec-worker").rglob("*") if "target" not in path.parts]
    if any(path.is_symlink() for path in [root / "rust", root / "rust/codec-worker", root / "algomini", *paths, *tree]):
        raise ValueError("Frozen source cannot contain symlinks")
    paths += [path for path in tree if path.is_file()]
    return {path.relative_to(root).as_posix(): sha256(path) for path in sorted(paths)}


def configuration(cfg):
    return {key: cfg[key] for key in CONFIG}


def runtime():
    tools = {"python": [sys.executable, "--version"], "gzip": ["gzip", "--version"],
             "xz": ["xz", "--version"], "zstd": ["zstd", "--version"],
             "rustc": [command_output(["rustup", "which", "rustc"]), "--version"],
             "cargo": [command_output(["rustup", "which", "cargo"]), "--version"],
             "cc": ["cc", "--version"], "ld": ["ld", "--version"]}
    result = {"machine": platform.machine(), "tools": {}, "libraries": {}}
    for name, argv in tools.items():
        executable = Path(shutil.which(argv[0])).resolve()
        result["tools"][name] = {"version": command_output(argv).splitlines()[0], "sha256": sha256(executable)}
        # Pin dynamically loaded native dependencies without baking in the
        # installation prefix (which may differ in a cloud checkout).
        for line in command_output(["ldd", str(executable)]).splitlines():
            match = re.search(r"(?:=>\s+)?(/[^\s]+)\s+\(", line)
            if match:
                library = Path(match[1]).resolve()
                result["libraries"][f"{name}/{library.name}"] = sha256(library)
    # These are loaded at runtime rather than linked into the Python launcher.
    import ctypes
    for name in ("libseccomp.so.2", "libzstd.so.1"):
        ctypes.CDLL(name)
    for line in Path("/proc/self/maps").read_text().splitlines():
        parts = line.split()
        if len(parts) >= 6 and any(name in parts[-1] for name in ("libseccomp.so", "libzstd.so")):
            path = Path(parts[-1]).resolve()
            result["libraries"][path.name] = sha256(path)
    # Rust's libraries are not necessarily reported by ldd on the compiler.
    sysroot = Path(command_output([tools["rustc"][0], "--print", "sysroot"]))
    result["rust_libraries"] = {path.relative_to(sysroot).as_posix(): sha256(path)
                                for path in sorted((sysroot / "lib/rustlib").rglob("*"))
                                if path.is_file() and path.suffix in (".rlib", ".rmeta", ".a", ".so")}
    return result


def create(root, cfg):
    if (root / RECORD).exists() or (root / ANCHOR).exists():
        raise ValueError("Evaluator already frozen; verify it instead of overwriting the freeze")
    record = {"schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
              "code": code_hashes(root), "configuration": configuration(cfg),
              "dataset_manifest_sha256": sha256(root / cfg["dataset"]["manifest"]), "runtime": runtime(),
              "runner": {"kernel": platform.release(), "python": platform.python_version()},
              "trust": "Pin this record's SHA-256 in the reviewed commit/publisher; its adjacent hash is an accidental-change check, not a signature."}
    write_json(root / RECORD, record)
    identifier = sha256(root / RECORD)
    (root / ANCHOR).write_text(identifier + "\n")
    return {"ok": True, "freeze_id": identifier, "files": len(record["code"])}


def verify(root, cfg, *, expected_id=None):
    record_path = root / RECORD
    identifier = sha256(record_path)
    anchor = (root / ANCHOR).read_text().strip()
    if identifier != anchor or (expected_id is not None and identifier != expected_id):
        raise ValueError("Evaluator freeze record checksum mismatch")
    record = json.loads(record_path.read_text())
    differences = []
    if record["code"] != code_hashes(root):
        differences.append("trusted source/file set")
    if record["configuration"] != configuration(cfg):
        differences.append("scoring configuration")
    if record["dataset_manifest_sha256"] != sha256(root / cfg["dataset"]["manifest"]):
        differences.append("public dataset manifest")
    if record["runtime"] != runtime():
        differences.append("compiler/baseline/native runtime")
    if differences:
        raise ValueError("Frozen evaluator changed: " + ", ".join(differences))
    return {"ok": True, "freeze_id": identifier, "files": len(record["code"])}
