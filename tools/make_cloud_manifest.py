"""Create a two-file cloud descriptor without uploading files or inspecting test contents."""

import argparse
import json
from pathlib import Path

from algomini.cloud.contracts import DatasetFile, manifest, policy
from algomini.common import sha256


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--family", choices=("csv", "json", "xml", "text", "binary"), required=True)
    for part in ("train", "test"):
        parser.add_argument("--" + part, type=Path, required=True)
        parser.add_argument("--" + part + "-url", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--license", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).parents[1]
    value = {"schema_version": 1, "family": args.family, "provenance": {"source": args.source, "license": args.license}}
    for part in ("train", "test"):
        path = getattr(args, part)
        value[part] = {"url": getattr(args, part + "_url"), "bytes": path.stat().st_size, "sha256": sha256(path)}
        DatasetFile.parse(value[part], policy(root))
    encoded = json.dumps(value, indent=2, sort_keys=True) + "\n"
    manifest(encoded, policy(root))
    with args.output.open("x") as stream:
        stream.write(encoded)
    print(args.output)


if __name__ == "__main__":
    main()
