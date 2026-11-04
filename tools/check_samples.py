"""Measure a reviewed Rust codec on local development/regression fixtures; no AI."""

import argparse
import json
from pathlib import Path

from algomini.common import load_config, sha256, write_json
from algomini.cloud import native
from algomini import archive
from algomini.labtools import MAX_INPUT
from algomini.sampling import fingerprints, overlap, partition


def bounded(path):
    with Path(path).open("rb") as stream:
        value = stream.read(archive.MAX_FILE + 1)
    if len(value) > archive.MAX_FILE:
        raise ValueError("Sample exceeds 64 MiB")
    return value


def check(root, workspace, development, *, family="json", plugin="general", regression=None, registry_path=None):
    root, workspace = Path(root).resolve(), Path(workspace).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    _, cfg = load_config(root / "run.toml")
    registry_path = Path(registry_path).resolve() if registry_path else root / "release/registry.json"
    asset_root = registry_path.parent.parent
    registry = json.loads(registry_path.read_text())
    identifier = registry["aliases"].get(plugin, plugin)
    item = next((row for row in registry["codecs"] if row["id"] == identifier), None)
    if item is None:
        raise ValueError(f"Plugin {plugin!r} is not registered. Create a codec request, or select a fixture with --registry.")
    data = bounded(development)
    research = data[:MAX_INPUT]
    training, validation, sampling = partition(research, family)
    sampling.update(research_bytes=len(research), development_bytes=len(data), scope="Research uses first 2 MiB; complete files measured")
    dictionary, dictionary_record = native.reference_dictionary(workspace, cfg, training)
    binary, build = native.build(root, workspace, registry, asset_root=asset_root)
    model = (asset_root / item["model"]).read_bytes()
    controls = native.correctness(binary, item, cfg, data, model)
    result = {"scope": "Known local development/regression fixtures; not fresh final-test qualification",
              "model_calls": 0, "docker_used": False, "tool_sha256": sha256(__file__),
              "build": build, "environment": native.host_environment(), "sampling": sampling,
              "dictionary": dictionary_record, "results": {}}

    def measure(name, sample):
        references = native.references(cfg, sample, dictionary)
        measured, packed = native.measure(binary, item, cfg, sample, model, references, checks=controls)
        measured["console"] = native.console_roundtrip(binary, cfg, sample, item["id"], packed)
        result["results"][name] = measured

    measure("validation", validation)
    measure("development", data)
    # Explicit opt-in; old acceptance data is only a known regression fixture.
    if regression:
        sample = bounded(regression)
        measure("known_regression", sample)
        result["regression_record_overlap"] = overlap(fingerprints(research, family), sample[:MAX_INPUT], family)
    write_json(workspace / "result.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--development", type=Path, required=True)
    parser.add_argument("--regression", type=Path)
    parser.add_argument("--family", choices=("json", "csv", "text", "xml", "binary"), default="json")
    parser.add_argument("--plugin", default="general")
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--registry", type=Path, help="Explicit alternate release/registry.json, for test fixtures")
    args = parser.parse_args()
    result = check(Path.cwd(), args.workspace, args.development, family=args.family, plugin=args.plugin, regression=args.regression,
                   registry_path=args.registry)
    for name, measured in result["results"].items():
        print(f"{name}: {measured['candidate']['archive_bytes']:,} vs {measured['baselines']['best_bytes']:,} bytes; "
              f"gain {measured['score']['archive_gain']:.2%}; mode {measured['candidate']['encoding_mode']}")


if __name__ == "__main__":
    main()
