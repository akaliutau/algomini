"""Source-only Rust plugin manifests; no Python code is imported from plugins."""

import json
from pathlib import Path
import re
import tempfile

from .common import digest, sha256, write_json


IDENTIFIER = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")


def discover(root):
    plugins = root / "plugins"
    if plugins.is_symlink():
        raise ValueError("Plugin registry cannot be a symlink")
    result = {}
    codec_ids = set()
    for path in sorted(plugins.glob("*/manifest.json")):
        if path.is_symlink() or path.parent.is_symlink() or path.parent.parent.resolve() != plugins.resolve():
            raise ValueError("Plugin paths must remain inside the registry")
        if path.stat().st_size > 16384:
            raise ValueError("Plugin manifest exceeds 16 KiB")
        record = json.loads(path.read_text())
        identifier = record.get("id", "")
        if not isinstance(identifier, str) or not IDENTIFIER.fullmatch(identifier) or identifier != path.parent.name or identifier in result:
            raise ValueError("Invalid or duplicate plugin id")
        if (record.get("schema_version") != 1 or record.get("worker_protocol_version") != 1
                or record.get("format_version") not in (1, 2) or record.get("source") != "codec.rs"):
            raise ValueError("Unsupported plugin manifest contract")
        source = path.parent / "codec.rs"
        if source.is_symlink() or not source.is_file() or not 0 < source.stat().st_size <= 65536:
            raise ValueError("Plugin source must be a regular file below 64 KiB")
        checksum = sha256(source)
        if record.get("source_sha256") != checksum or record.get("codec_id") != checksum[:32]:
            raise ValueError("Plugin source/codec checksum mismatch")
        if record["codec_id"] in codec_ids:
            raise ValueError("Duplicate plugin codec id")
        codec_ids.add(record["codec_id"])
        result[identifier] = {**record, "source_path": source}
    return result


def resolve(root, identifier):
    plugins = discover(root)
    if identifier not in plugins:
        raise ValueError(f"Unknown plugin: {identifier}")
    return plugins[identifier]


def register(root, identifier, source, *, status="experimental", provenance=None):
    if not IDENTIFIER.fullmatch(identifier):
        raise ValueError("Invalid plugin id")
    source = Path(source)
    if source.is_symlink() or not source.is_file():
        raise ValueError("Plugin source must be a regular file")
    with source.open("rb") as stream:
        code = stream.read(65537)
    if not 0 < len(code) <= 65536:
        raise ValueError("Plugin source exceeds 64 KiB")
    plugins = root / "plugins"
    if plugins.is_symlink():
        raise ValueError("Plugin registry cannot be a symlink")
    plugins.mkdir(exist_ok=True, parents=True)
    if identifier in discover(root) or (plugins / identifier).exists() or (plugins / identifier).is_symlink():
        raise ValueError("Plugin already exists; register a new version id")
    checksum = digest(code)
    if any(item["codec_id"] == checksum[:32] for item in discover(root).values()):
        raise ValueError("Duplicate plugin codec id")
    record = {"schema_version": 1, "id": identifier, "source": "codec.rs", "source_sha256": checksum,
              "codec_id": checksum[:32], "format_version": 2, "worker_protocol_version": 1,
              "status": status, "provenance": provenance or {},
              "runtime_dependency": "libzstd.so.1; toolchain pinned by evaluator freeze"}
    # Publish a complete pair. Compiled binary hints never override a source rebuild.
    with tempfile.TemporaryDirectory(prefix=".register-", dir=plugins) as temporary:
        directory = Path(temporary)
        (directory / "codec.rs").write_bytes(code)
        write_json(directory / "manifest.json", record)
        directory.rename(plugins / identifier)
    return record
