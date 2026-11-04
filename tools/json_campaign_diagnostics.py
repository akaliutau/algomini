"""Development-only entropy and reversible JSON lexical-layout experiments.

These are measured research representations, not release codecs or theoretical
compression limits. The campaign option verifies the development-file identity;
no final-file URL is fetched or final-file path opened.
"""

import argparse
from collections import Counter
import json
import math
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys

from algomini.archive import pack
from algomini.common import digest, sha256, write_json
from algomini.labtools import MAX_INPUT, run


SCALAR = re.compile(rb'-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?|true|false|null')
PROSPECTIVE_ID = "cx-" + "0" * 32 + "-" + "0" * 16


def conditional_entropy(data, order):
    """In-sample conditional byte entropy: descriptive, with no model cost."""
    symbols = Counter(data[i - order:i + 1] for i in range(order, len(data)))
    contexts = Counter()
    for symbol, count in symbols.items():
        contexts[symbol[:-1]] += count
    bits = sum(n * math.log2(n) for n in contexts.values()) - sum(n * math.log2(n) for n in symbols.values())
    return {"order": order, "observations": sum(symbols.values()), "contexts": len(contexts),
            "bits_per_observation": max(0., bits) / max(1, len(data) - order), "ideal_bytes_without_model": max(0., bits) / 8}


def array_records(data):
    """Count exact object bodies; parsing never supplies reserialized codec bytes."""
    try:
        text = data.decode("utf-8")
        parsed = json.loads(text)
    except (ValueError, UnicodeError, RecursionError) as error:
        return {"valid_json": False, "reason": str(error)[:180]}
    result = {"valid_json": True, "root": type(parsed).__name__}
    if not isinstance(parsed, list):
        return result
    decoder, position, records = json.JSONDecoder(), text.index("[") + 1, []
    midpoint = len(data[:len(data) // 2].decode("utf-8", errors="ignore"))
    left, right = set(), set()
    while True:
        while text[position].isspace():
            position += 1
        if text[position] == "]":
            break
        start = position
        _, position = decoder.raw_decode(text, position)
        body = text[start:position]
        records.append(body)
        if position <= midpoint:
            left.add(body)
        elif start >= midpoint:
            right.add(body)
        while text[position].isspace():
            position += 1
        if text[position] == ",":
            position += 1
    counts = Counter(records)
    return {**result, "array_items": len(records), "distinct_exact_item_bodies": len(counts),
            "duplicate_item_bodies": len(records) - len(counts),
            "multiplicities": sorted(Counter(counts.values()).items()),
            "byte_half_distinct_bodies": {"training": len(left), "validation": len(right), "shared": len(left & right)},
            "scope": "Within this development file only; equal file hashes are not required for record overlap"}


def lexemes(data):
    position = 0
    while position < len(data):
        if data[position] == 34:
            end = position + 1
            while end < len(data):
                if data[end] == 34:
                    yield position, end + 1
                    position = end + 1
                    break
                if data[end] == 92:
                    end += 1
                end += 1
            else:
                return  # Keep an unterminated tail opaque; never rescan its quotes.
        else:
            match = SCALAR.match(data, position)
            if match:
                yield position, match.end()
                position = match.end()
            else:
                position += 1


def transpose_lexemes(data):
    """Group raw lexemes by preceding key; retain all gaps and their order.

    This also accepts fragments: it is a byte-preserving layout, not JSON
    normalization. The decoder needs no schema, key names or training model.
    """
    if b"\0" in data:
        raise ValueError("Lexical reference requires NUL-free input")
    groups, streams, layout = {}, [], bytearray()
    previous, end = b"", 0
    for start, finish in lexemes(data):
        raw, gap = data[start:finish], data[end:start]
        key = previous if previous.startswith(b'"') and gap.strip() == b":" else None
        if key not in groups:
            if len(groups) == 255:
                raise ValueError("Too many lexical channels")
            groups[key] = len(groups)
            streams.append([])
        channel = groups[key]
        layout.extend(gap)
        layout.extend((0, channel))
        streams[channel].append(raw)
        previous, end = raw, finish
    layout.extend(data[end:])
    parts = [bytes(layout)] + [b"\0".join(values) for values in streams]
    return b"JCL1" + bytes([len(streams)]) + b"".join(struct.pack("<I", len(part)) for part in parts) + b"".join(parts)


def restore_lexemes(encoded):
    if len(encoded) < 9 or encoded[:4] != b"JCL1":
        raise ValueError("Invalid lexical header")
    count = encoded[4]
    header = 5 + 4 * (count + 1)
    if len(encoded) < header:
        raise ValueError("Truncated lexical dimensions")
    sizes = struct.unpack_from("<" + "I" * (count + 1), encoded, 5)
    if header + sum(sizes) != len(encoded):
        raise ValueError("Invalid lexical dimensions")
    position, parts = header, []
    for size in sizes:
        parts.append(encoded[position:position + size])
        position += size
    layout = parts[0]
    streams = [iter(part.split(b"\0")) for part in parts[1:]]
    position, output = 0, bytearray()
    while position < len(layout):
        if layout[position]:
            output.append(layout[position])
            position += 1
            continue
        if position + 1 >= len(layout) or layout[position + 1] >= count:
            raise ValueError("Invalid lexical channel")
        value = next(streams[layout[position + 1]], None)
        if value is None:
            raise ValueError("Missing lexical value")
        output.extend(value)
        position += 2
    if any(next(stream, None) is not None for stream in streams):
        raise ValueError("Unused lexical values")
    return bytes(output)


def compress(method, data):
    argv = {"xz-9e": ["xz", "-c", "-9e", "--threads=1"],
            "zstd-22": ["zstd", "-q", "--ultra", "-22", "-T1", "-c"]}[method]
    return subprocess.run(argv, input=data, capture_output=True, check=True, timeout=30).stdout


def decompress(method, data):
    executable = {"xz-9e": "xz", "zstd-22": "zstd"}[method]
    return subprocess.run([executable, "-q", "-d", "-c"], input=data, capture_output=True, check=True, timeout=30).stdout


def experiment(data, scope):
    transformed = transpose_lexemes(data)
    if restore_lexemes(transformed) != data:
        raise ValueError("Lexical reference failed exact reconstruction")
    rows = []
    for method in ("xz-9e", "zstd-22"):
        raw = compress(method, data)
        baseline_bytes = len(pack(method, [("sample.bin", raw)]))
        encoded = compress(method, transformed)
        if decompress(method, raw) != data or restore_lexemes(decompress(method, encoded)) != data:
            raise ValueError("Compressed reference failed exact reconstruction")
        # Count the prospective 40-byte ALG2 frame, mode byte and full ALC1
        # collection identity. These are size estimates, not valid Rust frames.
        proposed_bytes = len(pack(PROSPECTIVE_ID, [("sample.bin", bytes(40) + b"\1" + encoded)]))
        rows.append({"method": method, "raw_stream_bytes": len(raw), "baseline_archive_bytes": baseline_bytes,
                     "lexical_stream_bytes": len(encoded), "prospective_archive_bytes": proposed_bytes,
                     "prospective_framing_bytes": proposed_bytes - len(encoded), "model_bytes": 0})
    best = min(row["baseline_archive_bytes"] for row in rows)
    for row in rows:
        row["gain_vs_best_baseline"] = 1 - row["prospective_archive_bytes"] / best
    return {"scope": scope, "input_bytes": len(data), "input_sha256": digest(data),
            "validity": array_records(data), "representation_bytes": len(transformed), "exact_reconstruction": True,
            "best_baseline_bytes": best, "results": rows}


def diagnose(data, campaign=None):
    if not data or len(data) > MAX_INPUT:
        raise ValueError("Supply a nonempty development file of at most 2 MiB")
    if campaign:
        request = json.loads((campaign / "request.json").read_text())
        if digest(data) != request["dataset"]["train"]["sha256"]:
            raise ValueError("Input is not this campaign's declared development file")
    midpoint = max(1, len(data) // 2)
    result = {"schema_version": 1, "input_bytes": len(data), "input_sha256": digest(data),
              "tool_sha256": sha256(__file__), "python": sys.version.split()[0],
              "scope": "Development-only research. Final data not opened; no paid model calls or Docker.",
              "entropy_scope": "In-sample finite-order estimates, not a theoretical minimum; model costs omitted and high orders can overfit.",
              "archive_scope": "Reversible Python reference plus explicit prospective Rust framing. No Rust port, release qualification or speed/resource claim.",
              "byte_structure": run("byte-structure", data, family="json")["facts"],
              "json_records": array_records(data),
              "prefix_probe": run("json-lexemes", data[:65536], family="json")["facts"],
              "entropy": [conditional_entropy(data, order) for order in range(5)],
              "tools": {}}
    for tool in ("xz", "zstd"):
        executable = shutil.which(tool)
        result["tools"][tool] = {"path": executable, "sha256": sha256(executable),
            "version": subprocess.run([executable, "--version"], capture_output=True, check=True, text=True).stdout.splitlines()[0]}
    result["experiments"] = [experiment(part, scope) for scope, part in (
        ("training_byte_half", data[:midpoint]), ("validation_byte_half", data[midpoint:] or data), ("full_development", data))]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--campaign", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with args.input.open("rb") as stream:
        data = stream.read(MAX_INPUT + 1)
    result = diagnose(data, args.campaign)
    write_json(args.output, result)
    for experiment_result in result["experiments"]:
        best = min(experiment_result["results"], key=lambda row: row["prospective_archive_bytes"])
        print(f"{experiment_result['scope']}: baseline {experiment_result['best_baseline_bytes']:,}; "
              f"lexical/{best['method']} {best['prospective_archive_bytes']:,}; gain {best['gain_vs_best_baseline']:.2%}")


if __name__ == "__main__":
    main()
