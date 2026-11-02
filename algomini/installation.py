"""Build/install only the selected compiled plugin after passing sealed evidence."""

import ctypes
import json
from pathlib import Path
import platform
import shutil
import tempfile

from . import codecs, installed, sealed, selection
from .common import sha256, write_json


RUNTIME = ("__init__", "archive", "common", "codecs", "environment", "execution", "_isolate", "installed")


def package(root, cfg):
    result = sealed.final_result(root)
    if not installed.passing(result):
        raise ValueError("Sealed verification failed; no package or installation is allowed")
    protocol, protocol_id = sealed.verify_protocol(root, cfg)
    if (result["protocol_id"] != protocol_id or result["candidate_freeze_id"] != sealed.EXPECTED_CANDIDATE
            or result["evaluator_freeze_id"] != protocol["evaluator_freeze_id"]):
        raise ValueError("Sealed outcome does not bind the pinned protocol/candidate/evaluator")
    frozen = json.loads((root / selection.RECORD).read_text())
    directory = root / "packages" / frozen["candidate_id"]
    if directory.exists():
        manifest, checksum = installed.verify(directory)
        if manifest["verification_sha256"] != sha256(root / sealed.OUTPUT / "result.json"):
            raise ValueError("Existing package binds another verification result")
        return directory, manifest, checksum
    binary, _ = codecs.build(root, cfg, root / frozen["source"]["path"])
    directory.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".package-", dir=directory.parent) as temporary:
        target = Path(temporary)
        (target / "bin").mkdir()
        shutil.copyfile(binary, target / "bin/codec-worker")
        (target / "bin/codec-worker").chmod(0o755)
        shutil.copyfile(root / frozen["source"]["path"], target / "codec.rs")
        shutil.copyfile(root / frozen["model"]["path"], target / "model.bin")
        shutil.copyfile(root / selection.RECORD, target / "candidate-freeze.json")
        shutil.copyfile(root / sealed.OUTPUT / "result.json", target / "verification.json")
        module_dir = target / "runtime/algomini"
        module_dir.mkdir(parents=True)
        for name in RUNTIME:
            shutil.copyfile(root / f"algomini/{name}.py", module_dir / f"{name}.py")
        (target / "decode.py").write_text("import sys\nfrom pathlib import Path\nsys.dont_write_bytecode=True\nsys.path.insert(0,str(Path(__file__).resolve().parent/'runtime'))\nfrom algomini.installed import main\nraise SystemExit(main())\n")
        public = json.loads((root / cfg["dataset"]["manifest"]).read_text())
        write_json(target / "dataset-attribution.json", public["source"])
        libraries = {}
        for name in ("libseccomp.so.2", "libzstd.so.1"):
            ctypes.CDLL(name)
        for line in Path("/proc/self/maps").read_text().splitlines():
            parts = line.split()
            if len(parts) >= 6 and any(name in parts[-1] for name in ("libseccomp.so", "libzstd.so")):
                library = Path(parts[-1])
                libraries[library.name] = sha256(library)
        files = {path.relative_to(target).as_posix(): {"sha256": sha256(path), "bytes": path.stat().st_size}
                 for path in sorted(target.rglob("*")) if path.is_file()}
        manifest = {"schema_version": 1, "status": "verified", "id": frozen["candidate_id"],
                    "source_sha256": frozen["source"]["sha256"], "codec_id": frozen["source"]["sha256"][:32],
                    "candidate_freeze_id": sealed.EXPECTED_CANDIDATE, "protocol_id": result["protocol_id"],
                    "evaluator_freeze_id": protocol["evaluator_freeze_id"], "machine": platform.machine(),
                    "verification_sha256": sha256(root / sealed.OUTPUT / "result.json"),
                    "native_libraries": libraries, "python_requirement": ">=3.12; standard library only",
                    "config": {"lab": {"max_input_bytes": cfg["lab"]["max_input_bytes"]}, "limits": cfg["limits"]},
                    "files": files, "payload_files_bytes": sum(value["bytes"] for value in files.values()),
                    "decoder_executable_bytes": frozen["compiler"]["binary_bytes"],
                    "trust": "Use the reviewed package manifest SHA-256 as the external installation pin; adjacent hash is not a signature"}
        write_json(target / "manifest.json", manifest)
        checksum = sha256(target / "manifest.json")
        (target / "manifest.sha256").write_text(checksum + "\n")
        installed.verify(target, expected_id=checksum)
        target.rename(directory)
    return directory, manifest, checksum


def install(package_path, destination, *, expected_id):
    package_path, destination = Path(package_path), Path(destination)
    installed.verify(package_path, expected_id=expected_id)
    if destination.exists():
        installed.verify(destination, expected_id=expected_id)
        return {"ok": True, "destination": str(destination), "package_id": expected_id, "already_installed": True}
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".install-", dir=destination.parent) as temporary:
        target = Path(temporary) / "plugin"
        shutil.copytree(package_path, target)
        installed.verify(target, expected_id=expected_id)
        target.rename(destination)
    return {"ok": True, "destination": str(destination), "package_id": expected_id, "already_installed": False}
