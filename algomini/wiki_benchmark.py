"""Counted Wikipedia research measurements using the unchanged native harness."""

import argparse
import json
from pathlib import Path
import tomllib
from zipfile import ZipFile

from . import archive, baselines, codecs, evaluator, freeze
from .common import digest, download, load_config, sha256, write_json


OUTPUT = "reports/wikipedia-search"


def window(root, offset, size, *, final=False):
    if offset < 0 or not 0 < size <= 2 * 1024**2:
        raise ValueError("Invalid bounded corpus window")
    policy = tomllib.loads((root / "wiki-search.toml").read_text())["later_window"]
    if offset < policy["offset"] + policy["bytes"] and offset + size > policy["offset"]:
        if not final or not (root / OUTPUT / "verification/consumed.json").exists():
            raise ValueError("Reserved later window is unavailable before the final claim")
    spec = json.loads((root / "datasets/wikipedia/manifest.json").read_text())
    if offset + size > spec["member"]["bytes"]:
        raise ValueError("Window exceeds the committed corpus")
    source = download(spec["archive"]["url"], root / ".cache/datasets/enwik8.zip",
                      spec["archive"]["sha256"], spec["archive"]["bytes"])
    with ZipFile(source) as zipped, zipped.open(spec["member"]["name"]) as stream:
        stream.seek(offset)
        data = stream.read(size)
    if len(data) != size:
        raise ValueError("Truncated corpus window")
    return data


def dictionary(root, cfg):
    output = root / OUTPUT
    policy = {"archive_manifest_sha256": sha256(root / "datasets/wikipedia/manifest.json"),
              "offset": 1000000, "sample_bytes": 65536, "samples": 16, "dictionary_bytes": 8192,
              "baseline_arguments": cfg["baselines"],
              "scope": "Disjoint external training bytes, never the reserved later window; dictionary counted once"}
    path = output / "baseline-policy.json"
    if path.exists() and json.loads(path.read_text()) != policy:
        raise ValueError("Wikipedia baseline training policy changed")
    if not path.exists():
        write_json(path, policy)
    directory = root / ".cache/wikipedia-dictionary"
    record, model = directory / "training.json", directory / "dictionary.bin"
    if record.exists():
        value = json.loads(record.read_text())
        if sha256(model) != value["sha256"] or value["policy_sha256"] != sha256(path):
            raise ValueError("Wikipedia training dictionary checksum mismatch")
        return model, value
    data = window(root, policy["offset"], policy["samples"] * policy["sample_bytes"])
    directory.mkdir(parents=True, exist_ok=True)
    samples = []
    for index in range(policy["samples"]):
        sample = directory / f"sample-{index:02d}.bin"
        sample.write_bytes(data[index * 65536:(index + 1) * 65536])
        samples.append(sample)
    _, measurement = baselines.measure(["zstd", "-q", "--train", "-T1", "--dictID=1", "--maxdict=8192",
                                        "-o", str(model), *map(str, samples)], b"", cfg, ro=samples, rw=[directory])
    value = {"sha256": sha256(model), "bytes": model.stat().st_size, "training_sha256": digest(data),
             "sample_sha256": [sha256(sample) for sample in samples], "policy_sha256": sha256(path), "resources": measurement}
    write_json(record, value)
    write_json(output / "dictionary-training.json", value)
    return model, value


def baseline_corpus(root, cfg, data, label):
    model_path, training = dictionary(root, cfg)
    definition = {"input_sha256": digest(data), "label": label, "dictionary_sha256": training["sha256"],
                  "harness_sha256": sha256(__file__), "commands": cfg["baselines"]}
    key = digest(json.dumps(definition, sort_keys=True).encode())
    path = root / OUTPUT / "baselines" / (key + ".json")
    if path.exists():
        value = json.loads(path.read_text())
        if value["definition"] != definition:
            raise ValueError("Wikipedia baseline cache mismatch")
        return value["result"]
    results = []
    for baseline in cfg["baselines"]:
        model = model_path.read_bytes() if baseline.get("dictionary") else b""
        args = lambda key: [part.replace("{dictionary}", str(model_path)).replace("{input_bytes}", str(len(data))) for part in baseline[key]]
        encoded, encoding = baselines.measure(args("encode"), data, cfg, ro=[model_path] if model else [])
        measurements = []
        for _ in range(cfg["evaluation"]["decode_repetitions"]):
            restored, measurement = baselines.measure(args("decode"), encoded, cfg, ro=[model_path] if model else [])
            if restored != data:
                raise ValueError("Wikipedia baseline reconstruction failed")
            measurements.append(measurement)
        result, _ = evaluator.counted(baseline["id"], [(label, encoded)], model, [{"label": label,
            "input_bytes": len(data), "block_bytes": len(encoded), "encode": encoding,
            "decode": evaluator.resources(measurements, len(data))}])
        results.append(result)
    best = min(results, key=lambda row: row["archive_bytes"])
    result = {"results": results, "best_id": best["id"], "best_bytes": best["archive_bytes"], "dictionary": training}
    write_json(path, {"definition": definition, "result": result})
    return result


def eligible(score):
    # User explicitly ranks size first and speed second in this new campaign.
    return score["gates"]["exact_reconstruction"] and score["gates"]["decode_rss"]


def measure(root, cfg, source, data, *, label="enwik8-first-1000000.xml", publish=True, preflight=None):
    if not 0 < len(data) <= cfg["lab"]["max_input_bytes"]:
        raise ValueError("Invalid bounded benchmark input")
    pinned = freeze.verify(root, cfg)
    binary, build = codecs.build(root, cfg, source)
    preflight = data[:2048] if preflight is None else preflight
    definition = {"source_sha256": sha256(source), "binary_sha256": build["binary_sha256"], "input_sha256": digest(data),
                  "preflight_sha256": digest(preflight),
                  "label": label, "native_freeze_id": pinned["freeze_id"], "harness_sha256": sha256(__file__),
                  "objective_sha256": sha256(root / OUTPUT / "objective.json")}
    key = digest(json.dumps(definition, sort_keys=True).encode())
    result_path = root / OUTPUT / "evaluations" / (key + ".json")
    archive_path = root / ".cache/wikipedia-evaluations" / (key + ".alc")
    if result_path.exists():
        value = json.loads(result_path.read_text())
        if value["definition"] != definition or sha256(archive_path) != value["candidate"]["archive_sha256"]:
            raise ValueError("Wikipedia candidate cache checksum mismatch")
        return value
    checks = codecs.correctness(root, cfg, binary, preflight)
    for tiny in (b"banana bandana banana " * 200, b'{"key":"value","x":-0.0}\r\n' * 200, bytes(range(256)) * 32):
        encoded, _ = codecs.invoke(root, cfg, binary, 4, tiny, original_len=len(tiny))
        restored, _ = codecs.invoke(root, cfg, binary, 5, encoded, original_len=len(tiny))
        if restored != tiny:
            raise ValueError("Wikipedia extended tiny correctness failed")
    encoded, encoding = codecs.invoke(root, cfg, binary, 6, data, source_sha256=build["source_sha256"])
    measurements = []
    for _ in range(cfg["evaluation"]["decode_repetitions"]):
        restored, measurement = codecs.invoke(root, cfg, binary, 2, encoded, source_sha256=build["source_sha256"])
        if restored != data:
            raise ValueError("Wikipedia exact reconstruction failed")
        measurements.append(measurement)
    candidate, packed = evaluator.counted("wk-" + build["source_sha256"][:16], [(label, encoded)], b"", [{"label": label,
        "input_bytes": len(data), "input_sha256": digest(data), "block_bytes": len(encoded), "encode": encoding,
        "decode": evaluator.resources(measurements, len(data))}])
    _, model, blocks = archive.unpack(packed)
    restored, _ = codecs.invoke(root, cfg, binary, 2, blocks[0][1], model=model, source_sha256=build["source_sha256"])
    if restored != data or sha256(source) != build["source_sha256"]:
        raise ValueError("Counted archive/source integrity failed")
    candidate.update(correctness=checks, build=build, source_sha256=build["source_sha256"], decoder_package_bytes=build["binary_bytes"],
                     trained_model_sha256=digest(b""), candidate_training_calls=0, mode=encoded[40] if encoded[4] != 1 else "raw")
    baseline = baseline_corpus(root, cfg, data, label)
    score = evaluator.score(cfg, baseline, candidate)
    result = {"schema_version": 1, "definition": definition, "input_bytes": len(data), "candidate": candidate,
              "baselines": baseline, "score": score, "eligible": eligible(score),
              "size_win": score["archive_gain"] > 0, "meets_size_target": score["gates"]["archive_gain"],
              "speed_priority": "secondary; the old 10 MiB/s gate is diagnostic only", "scope": "Development benchmark, not unseen verification"}
    if publish:
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        archive_path.write_bytes(packed)
        write_json(result_path, result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source")
    parser.add_argument("--bytes", type=int, default=1000000)
    args = parser.parse_args()
    root, cfg = load_config("run.toml")
    if not 0 < args.bytes <= 1000000:
        raise ValueError("Research only exposes the existing development prefix")
    data = (root / ".cache/datasets/enwik8-first-1000000.xml").read_bytes()[:args.bytes]
    result = measure(root, cfg, Path(args.source), data)
    print(json.dumps({"candidate_id": result["candidate"]["id"], "archive_bytes": result["candidate"]["archive_bytes"],
        "baseline": result["baselines"]["best_id"], "baseline_bytes": result["baselines"]["best_bytes"],
        "eligible": result["eligible"], "size_win": result["size_win"], "score": result["score"]}, indent=2))
