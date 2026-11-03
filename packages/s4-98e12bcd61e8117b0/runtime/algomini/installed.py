"""Compiler-free runtime for a verified compiled plugin directory."""

import sys
sys.dont_write_bytecode = True

import argparse
import ctypes
import json
from pathlib import Path
import platform
import tempfile

from . import archive, codecs
from .common import digest, sha256


GATES = {"exact_reconstruction", "archive_gain", "decode_speed", "decode_rss"}


def passing(evidence):
    gates = evidence.get("score", {}).get("gates", {})
    return evidence.get("status") == "passed" and set(gates) == GATES and all(value is True for value in gates.values())


def checked_path(root, name):
    relative = Path(name)
    path = root / relative
    if relative.is_absolute() or ".." in relative.parts or path.is_symlink() or not path.resolve().is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError("Invalid installed package path")
    return path


def verify(directory, *, expected_id=None):
    directory = Path(directory).resolve()
    manifest_path = directory / "manifest.json"
    checksum = sha256(manifest_path)
    if checksum != (directory / "manifest.sha256").read_text().strip() or (expected_id and checksum != expected_id):
        raise ValueError("Installed manifest checksum mismatch")
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("schema_version") != 1 or manifest.get("status") != "verified" or manifest["machine"] != platform.machine():
        raise ValueError("Unsupported/unverified plugin package or architecture")
    for name, attributes in manifest["files"].items():
        path = checked_path(directory, name)
        if path.stat().st_size != attributes["bytes"] or sha256(path) != attributes["sha256"]:
            raise ValueError("Installed package file checksum mismatch: " + name)
    actual = {path.relative_to(directory).as_posix() for path in directory.rglob("*") if path.is_file()}
    if actual != set(manifest["files"]) | {"manifest.json", "manifest.sha256"}:
        raise ValueError("Installed package has unmanifested files")
    selected = directory / "candidate-freeze.json"
    if sha256(selected) != manifest["candidate_freeze_id"]:
        raise ValueError("Installed package binds another selected candidate")
    candidate = json.loads(selected.read_text())
    binary = directory / "bin/codec-worker"
    model = directory / "model.bin"
    if (manifest["id"] != candidate["candidate_id"] or sha256(binary) != candidate["compiler"]["binary_sha256"]
            or sha256(model) != candidate["model"]["sha256"]
            or sha256(directory / "codec.rs") != candidate["source"]["sha256"]
            or manifest["source_sha256"] != candidate["source"]["sha256"]):
        raise ValueError("Installed binary/model/source binding mismatch")
    evidence = json.loads((directory / "verification.json").read_text())
    if (not passing(evidence) or evidence["candidate_freeze_id"] != manifest["candidate_freeze_id"]
            or evidence["protocol_id"] != manifest["protocol_id"]
            or evidence["evaluator_freeze_id"] != manifest["evaluator_freeze_id"]
            or sha256(directory / "verification.json") != manifest["verification_sha256"]):
        raise ValueError("Installed package has no passing sealed evidence")
    for name in ("libseccomp.so.2", "libzstd.so.1"):
        ctypes.CDLL(name)
    loaded = {Path(line.split()[-1]).name: Path(line.split()[-1]) for line in Path("/proc/self/maps").read_text().splitlines()
              if len(line.split()) >= 6 and any(token in line.split()[-1] for token in ("libseccomp.so", "libzstd.so"))}
    for name, library_checksum in manifest["native_libraries"].items():
        if name not in loaded or sha256(loaded[name]) != library_checksum:
            raise ValueError("Installed native dependency differs: " + name)
    return manifest, checksum


def encode(directory, input_path, output_path):
    manifest, _ = verify(directory)
    directory = Path(directory)
    with Path(input_path).open("rb") as stream:
        data = stream.read(manifest["config"]["lab"]["max_input_bytes"] + 1)
    if len(data) > manifest["config"]["lab"]["max_input_bytes"]:
        raise ValueError("Input exceeds installed codec bound")
    model = (directory / "model.bin").read_bytes()
    encoded, _ = codecs.invoke(directory, manifest["config"], directory / "bin/codec-worker", 6, data,
                               model=model, source_sha256=manifest["source_sha256"])
    packed = archive.pack(manifest["id"], [(Path(input_path).name, encoded)], model if encoded[4] == 2 else b"")
    with Path(output_path).open("xb") as stream:
        stream.write(packed)
    return {"archive_bytes": len(packed), "archive_sha256": digest(packed)}


def decode(directory, input_path, output_directory):
    manifest, _ = verify(directory)
    directory = Path(directory)
    with Path(input_path).open("rb") as stream:
        packed = stream.read(archive.MAX_ARCHIVE + 1)
    method, model, blocks = archive.unpack(packed)
    if method != manifest["id"] or (model and model != (directory / "model.bin").read_bytes()):
        raise ValueError("Archive/plugin model binding mismatch")
    destination = Path(output_directory)
    destination.mkdir(parents=True, exist_ok=True)
    if any((destination / label).exists() or (destination / label).is_symlink() for label, _ in blocks):
        raise ValueError("Installed decoder refuses existing output")
    # Keep failed decodes out of the output directory; the native processes
    # receive only counted compressed bytes/model, never originals or outputs.
    with tempfile.TemporaryDirectory(prefix="algomini-decoded-", dir=destination.parent) as temporary:
        temporary = Path(temporary)
        rows = []
        for label, payload in blocks:
            restored, _ = codecs.invoke(directory, manifest["config"], directory / "bin/codec-worker", 2, payload,
                                         model=model, source_sha256=manifest["source_sha256"])
            (temporary / label).write_bytes(restored)
            rows.append({"label": label, "bytes": len(restored), "sha256": digest(restored)})
        for row in rows:
            # Exclusive creation protects against output races/symlinks.
            with (destination / row["label"]).open("xb") as stream:
                stream.write((temporary / row["label"]).read_bytes())
    return {"files": rows, "decoded_bytes": sum(row["bytes"] for row in rows)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plugin", required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("verify")
    encoding = commands.add_parser("encode")
    encoding.add_argument("input")
    encoding.add_argument("output")
    decoding = commands.add_parser("decode")
    decoding.add_argument("input")
    decoding.add_argument("output_directory")
    args = parser.parse_args()
    try:
        if args.command == "verify":
            manifest, checksum = verify(args.plugin)
            result = {"ok": True, "id": manifest["id"], "package_id": checksum}
        elif args.command == "encode":
            result = encode(args.plugin, args.input, args.output)
        else:
            result = decode(args.plugin, args.input, args.output_directory)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError) as error:
        print(f"algomini installed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
