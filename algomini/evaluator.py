"""Trusted development-only evaluator. Invoke directly for frozen measurements."""

import argparse
import json
from pathlib import Path
import statistics
import struct
import sys
import time

from . import archive, baselines, codecs, freeze, registry
from .common import digest, load_config, sha256, write_json
from .dataset import bounded_sample, development_files


def training_samples(root, cfg):
    paths = development_files(root, cfg, "train")[1:1 + cfg["lab"]["dictionary_samples"]]
    samples = [bounded_sample(path, cfg["lab"]["dictionary_sample_bytes"]) for path in paths]
    if sum(len(value) + 4 for value in samples) > cfg["lab"]["max_input_bytes"]:
        raise ValueError("Combined training input exceeds the 2 MiB cap")
    return samples


def probes(root, cfg, split):
    paths = development_files(root, cfg, split)
    indices = sorted({round(position * (len(paths) - 1)) for position in cfg["evaluation"]["probe_positions"]})
    selected = [(paths[index].name, bounded_sample(paths[index], cfg["evaluation"]["probe_bytes"])) for index in indices]
    if not selected or any(not data or len(data) > cfg["lab"]["max_input_bytes"] for _, data in selected):
        raise ValueError("Invalid bounded evaluation probes")
    return selected


def resources(measurements, input_bytes):
    elapsed = statistics.median(value["execution_seconds"] for value in measurements)
    return {"repetitions": measurements, "median_execution_seconds": elapsed,
            "decode_mib_per_second": input_bytes / 1024**2 / elapsed,
            "peak_rss_kib": max(value["peak_rss_kib"] for value in measurements)}


def counted(method, blocks, model, rows):
    packed = archive.pack(method, blocks, model)
    if archive.unpack(packed) != (method, model, blocks):
        raise ValueError("Collection framing failed roundtrip")
    payload = sum(len(data) for _, data in blocks)
    return {"id": method, "archive_bytes": len(packed), "archive_sha256": digest(packed),
            "block_bytes": payload, "model_bytes": len(model),
            "collection_bytes": len(packed) - payload - len(model), "files": rows, "roundtrip": True}, packed


def baseline_corpus(root, cfg, samples):
    dictionary, training = baselines.train_dictionary(root, cfg)
    results = []
    for baseline in cfg["baselines"]:
        model = dictionary.read_bytes() if baseline.get("dictionary") else b""
        blocks, rows = [], []
        for label, data in samples:
            substitute = lambda part: part.replace("{dictionary}", str(dictionary)).replace("{input_bytes}", str(len(data)))
            encode, decode = ([substitute(part) for part in baseline[key]] for key in ("encode", "decode"))
            encoded, encoding = baselines.measure(encode, data, cfg, ro=[dictionary] if model else [])
            decoding = []
            # A decoder gets only the archived model, never training samples or originals.
            for _ in range(cfg["evaluation"]["decode_repetitions"]):
                restored, measurement = baselines.measure(decode, encoded, cfg, ro=[dictionary] if model else [])
                if restored != data:
                    raise ValueError(f"Baseline roundtrip failed: {baseline['id']}/{label}")
                decoding.append(measurement)
            blocks.append((label, encoded))
            rows.append({"label": label, "input_bytes": len(data), "input_sha256": digest(data),
                         "block_bytes": len(encoded), "encode": encoding, "decode": resources(decoding, len(data))})
        result, _ = counted(baseline["id"], blocks, model, rows)
        result.update(encode_command=baseline["encode"], decode_command=baseline["decode"])
        results.append(result)
    # Explicitly select a complete configuration, never the best method per file.
    best = min(results, key=lambda item: item["archive_bytes"])
    return {"results": results, "best_id": best["id"], "best_bytes": best["archive_bytes"], "dictionary": training}


def reference_corpus(cfg, samples):
    blocks, rows = [], []
    for label, data in samples:
        started = time.perf_counter()
        transformed = baselines.transpose_tokens(data)
        transform_seconds = time.perf_counter() - started
        encoded, encoding = baselines.measure(["zstd", "-q", "-T1", f"--stream-size={len(transformed)}", "-19", "-c"], transformed, cfg)
        encoding["host_transform_seconds"] = transform_seconds
        decoding = []
        for _ in range(cfg["evaluation"]["decode_repetitions"]):
            raw, measurement = baselines.measure(["zstd", "-q", "-d", "-c"], encoded, cfg)
            started = time.perf_counter()
            restored = baselines.restore_tokens(raw)
            restore_seconds = time.perf_counter() - started
            if restored != data:
                raise ValueError("Structured reference failed roundtrip")
            measurement["host_restore_seconds"] = restore_seconds
            measurement["execution_seconds"] += restore_seconds
            decoding.append(measurement)
        blocks.append((label, encoded))
        rows.append({"label": label, "input_bytes": len(data), "block_bytes": len(encoded),
                     "encode": encoding, "decode": resources(decoding, len(data))})
    result, _ = counted("raw-token-transpose-zstd-19", blocks, b"", rows)
    result["resource_scope"] = "zstd child timing plus measured Python transform/restore; RSS is child only; reference is not an installable candidate"
    return result


def candidate_corpus(root, cfg, identifier, samples):
    plugin = registry.resolve(root, identifier)
    binary, compilation = codecs.build(root, cfg, plugin["source_path"])
    values = training_samples(root, cfg)
    training = b"".join(struct.pack("<I", len(value)) + value for value in values)
    model, training_resources = codecs.invoke(root, cfg, binary, 3, training)
    checks = codecs.correctness(root, cfg, binary, samples[0][1], model)
    rows, blocks = [], []
    for label, data in samples:
        encoded, encoding = codecs.invoke(root, cfg, binary, 6, data, model=model, source_sha256=plugin["source_sha256"])
        decoding = []
        for _ in range(cfg["evaluation"]["decode_repetitions"]):
            restored, measurement = codecs.invoke(root, cfg, binary, 2, encoded, model=model, source_sha256=plugin["source_sha256"])
            if restored != data:
                raise ValueError(f"Candidate failed exact reconstruction: {label}")
            decoding.append(measurement)
        blocks.append((label, encoded))
        rows.append({"label": label, "input_bytes": len(data), "input_sha256": digest(data),
                     "block_bytes": len(encoded), "payload_bytes": len(encoded) - 40,
                     "framing_bytes": 40, "raw_escape": encoded[4] == 1,
                     "encode": encoding, "decode": resources(decoding, len(data))})
    used_model = model if any(payload[4] == 2 for _, payload in blocks) else b""
    result, packed = counted(identifier, blocks, used_model, rows)
    # Decode the bytes that are actually counted; do not use unarchived model state.
    method, stored_model, stored_blocks = archive.unpack(packed)
    for (label, data), (stored_label, payload) in zip(samples, stored_blocks):
        restored, _ = codecs.invoke(root, cfg, binary, 2, payload, model=stored_model, source_sha256=plugin["source_sha256"])
        if method != identifier or label != stored_label or restored != data:
            raise ValueError("Counted candidate collection failed exact reconstruction")
    if sha256(plugin["source_path"]) != compilation["source_sha256"]:
        raise ValueError("Plugin source changed during evaluation")
    result.update(source_sha256=compilation["source_sha256"], build=compilation, correctness=checks,
                  trained_model_bytes=len(model), trained_model_sha256=digest(model),
                  training_sample_sha256=[digest(value) for value in values], training=training_resources,
                  decoder_package_bytes=compilation["binary_bytes"])
    return result, packed


def score(cfg, baseline, candidate):
    baseline_bytes = baseline["best_bytes"]
    gain = 1 - candidate["archive_bytes"] / baseline_bytes
    speed = min(row["decode"]["decode_mib_per_second"] for row in candidate["files"])
    rss = max(row["decode"]["peak_rss_kib"] for row in candidate["files"])
    gates = {"exact_reconstruction": candidate["roundtrip"] and candidate["correctness"]["passed"],
             "archive_gain": gain >= cfg["objective"]["minimum_archive_gain"],
             "decode_speed": speed >= cfg["limits"]["candidate_min_decode_mib_per_second"],
             "decode_rss": rss <= cfg["limits"]["candidate_memory_mib"] * 1024}
    return {"archive_gain": gain, "minimum_decode_mib_per_second": speed, "peak_decode_rss_kib": rss,
            "deployment_saving_bytes": baseline_bytes - candidate["archive_bytes"] - candidate["decoder_package_bytes"],
            "deployment_assumption": "Common runtime/libzstd already available; one complete candidate executable is incremental",
            "gates": gates, "meets_development_targets": all(gates.values())}


def evaluate(root, cfg, identifier, split="validation", *, expected_freeze=None):
    pinned = freeze.verify(root, cfg, expected_id=expected_freeze)
    samples = probes(root, cfg, split)
    candidate, packed = candidate_corpus(root, cfg, identifier, samples)
    baseline = baseline_corpus(root, cfg, samples)
    reference = reference_corpus(cfg, samples)
    result = {"schema_version": 2, "freeze_id": pinned["freeze_id"], "split": split,
              "inputs": [{"label": label, "bytes": len(data), "sha256": digest(data)} for label, data in samples],
              "input_bytes": sum(len(data) for _, data in samples), "baselines": baseline,
              "reference": reference, "candidate": candidate, "score": score(cfg, baseline, candidate),
              "timing_scope": cfg["evaluation"]["timing"], "rss_scope": cfg["evaluation"]["rss"],
              "scope": "Fixed development probes, not a sealed-test result or installation approval",
              "reproduce": ["python", "-m", "algomini.evaluator", "evaluate", identifier, "--split", split]}
    freeze.verify(root, cfg, expected_id=pinned["freeze_id"])
    directory = root / "reports/evaluations" / identifier / split
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "archive.alc").write_bytes(packed)
    write_json(directory / "result.json", result)
    return result


def local_encode(root, cfg, identifier, input_path, output_path, model_path=None):
    freeze.verify(root, cfg)
    plugin = registry.resolve(root, identifier)
    binary, _ = codecs.build(root, cfg, plugin["source_path"])
    with Path(input_path).open("rb") as stream:
        data = stream.read(cfg["lab"]["max_input_bytes"] + 1)
    if len(data) > cfg["lab"]["max_input_bytes"]:
        raise ValueError("Encode input exceeds 2 MiB")
    model = b""
    if model_path:
        with Path(model_path).open("rb") as stream:
            model = stream.read(65537)
        if len(model) > 65536:
            raise ValueError("Shared model exceeds 64 KiB")
    encoded, _ = codecs.invoke(root, cfg, binary, 6, data, model=model, source_sha256=plugin["source_sha256"])
    packed = archive.pack(identifier, [(Path(input_path).name, encoded)], model if encoded[4] == 2 else b"")
    freeze.verify(root, cfg)
    with Path(output_path).open("xb") as stream:
        stream.write(packed)
    return {"archive_bytes": len(packed), "archive_sha256": digest(packed)}


def local_decode(root, cfg, input_path, output_directory):
    freeze.verify(root, cfg)
    with Path(input_path).open("rb") as stream:
        packed = stream.read(archive.MAX_ARCHIVE + 1)
    method, model, blocks = archive.unpack(packed)
    plugin = registry.resolve(root, method)
    binary, _ = codecs.build(root, cfg, plugin["source_path"])
    # Validate all blocks before publishing any output. The native decoder sees
    # only stdin, its executable, and system libraries, in a fresh empty cwd.
    results = [(label, codecs.invoke(root, cfg, binary, 2, payload, model=model,
                                    source_sha256=plugin["source_sha256"])[0]) for label, payload in blocks]
    freeze.verify(root, cfg)
    directory = Path(output_directory)
    directory.mkdir(parents=True, exist_ok=True)
    if any((directory / label).exists() or (directory / label).is_symlink() for label, _ in results):
        raise ValueError("Decode refuses to overwrite existing output")
    for label, data in results:
        with (directory / label).open("xb") as stream:
            stream.write(data)
    return {"files": len(results), "decoded_bytes": sum(len(data) for _, data in results)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="run.toml")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("freeze-status")
    commands.add_parser("plugins")
    evaluation = commands.add_parser("evaluate")
    evaluation.add_argument("codec")
    evaluation.add_argument("--split", choices=("train", "validation"), default="validation")
    encode = commands.add_parser("encode")
    encode.add_argument("codec")
    encode.add_argument("input")
    encode.add_argument("output")
    encode.add_argument("--model")
    decode = commands.add_parser("decode")
    decode.add_argument("input")
    decode.add_argument("output_directory")
    args = parser.parse_args()
    try:
        root, cfg = load_config(args.config)
        if args.command == "freeze-status":
            result = freeze.verify(root, cfg)
        elif args.command == "plugins":
            result = {"plugins": [{key: value for key, value in item.items() if key != "source_path"}
                                   for item in registry.discover(root).values()]}
        elif args.command == "encode":
            result = local_encode(root, cfg, args.codec, args.input, args.output, args.model)
        elif args.command == "decode":
            result = local_decode(root, cfg, args.input, args.output_directory)
        else:
            full = evaluate(root, cfg, args.codec, args.split)
            result = {"freeze_id": full["freeze_id"], "split": full["split"], "score": full["score"],
                      "result": f"reports/evaluations/{args.codec}/{args.split}/result.json"}
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError) as error:
        print(f"algomini evaluator: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
