"""Register immutable source/model assets locally for review; never publish or run."""

import argparse
import json
from pathlib import Path

from algomini.cloud.contracts import SLUG
from algomini.common import digest, write_json


def bounded(path, *, nonempty=False):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("Codec assets must be regular nonsymlink files")
    with path.open("rb") as stream:
        value = stream.read(65537)
    if len(value) > 65536 or nonempty and not value:
        raise ValueError("Codec source/model exceeds its bound or source is empty")
    return value


def register(root, name, source, model=None, *, replace=False):
    root = Path(root).resolve()
    if not SLUG.fullmatch(name):
        raise ValueError("Use a lowercase plugin name of at most 64 characters")
    registry_path = root / "release/registry.json"
    registry = json.loads(registry_path.read_text())
    if name in registry["aliases"] and not replace:
        raise ValueError("Alias exists; --replace is required to select a new immutable version")
    code = bounded(source, nonempty=True)
    learned = bounded(model) if model else b""
    source_hash, model_hash = digest(code), digest(learned)
    previous = next((row for row in registry["codecs"] if row["source_sha256"] == source_hash and row["model_sha256"] == model_hash), None)
    if previous:
        identifier = previous["id"]
    else:
        if len(registry["codecs"]) >= 128:
            raise ValueError("Registry exceeds 128 codecs")
        identifier = "cx-" + source_hash[:32] + "-" + model_hash[:16]
        directory = root / "plugins" / identifier
        if (root / "plugins").is_symlink() or directory.is_symlink():
            raise ValueError("Codec output directories must not be symlinks")
        directory.mkdir(parents=True, exist_ok=True)
        for filename, value in (("codec.rs", code), ("model.bin", learned)):
            path = directory / filename
            if path.is_symlink() or path.exists() and path.read_bytes() != value:
                raise ValueError("Immutable codec assets already differ")
            path.write_bytes(value)
        registry["codecs"].append({"id": identifier, "codec_id": source_hash[:32],
            "source": f"plugins/{identifier}/codec.rs", "source_sha256": source_hash,
            "model": f"plugins/{identifier}/model.bin", "model_sha256": model_hash,
            "origin": "Local source seed; requires campaign qualification"})
    registry["aliases"][name] = identifier
    write_json(registry_path, registry)
    return identifier


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--name", required=True)
    parser.add_argument("--model", type=Path)
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    print(register(Path.cwd(), args.name, args.source, args.model, replace=args.replace))


if __name__ == "__main__":
    main()
