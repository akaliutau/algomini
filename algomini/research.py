"""Exact-token development probes; decimal statistics never rewrite CSV bytes."""

from collections import Counter
from datetime import datetime
from decimal import Decimal, InvalidOperation, localcontext
import re
import struct

from .common import digest, sha256, write_json
from . import baselines
from .dataset import bounded_sample, development_files
from .evaluator import probes


DECIMAL = re.compile(rb"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?\Z")


def layout(data):
    rows, spans, cursor = [], [], 0
    for line in data.splitlines(keepends=True):
        ending = 2 if line.endswith(b"\r\n") else int(line.endswith(b"\n"))
        body = line[:-ending] if ending else line
        cells, positions, start = body.split(b","), [], cursor
        for cell in cells:
            positions.append([start, start + len(cell)])
            start += len(cell) + 1
        if any(data[a:b] != cell for (a, b), cell in zip(positions, cells)):
            raise ValueError("Token offset reconstruction failed")
        rows.append(cells)
        spans.append({"line": [cursor, cursor + len(line)], "tokens": positions, "ending_bytes": ending})
        cursor += len(line)
    if not rows or any(len(row) != len(rows[0]) for row in rows):
        raise ValueError("Probe requires rectangular raw comma layout")
    return rows, spans


def prefix_length(left, right):
    return next((index for index, (a, b) in enumerate(zip(left, right)) if a != b), min(len(left), len(right)))


def column_stats(name, values):
    scales, decimals, lexical = Counter(), [], Counter()
    for token in values:
        quoted = len(token) >= 2 and token.startswith(b'"') and token.endswith(b'"')
        body = token[1:-1] if quoted else token
        number = body.strip(b" \t")
        lexical["quoted_tokens"] += quoted
        lexical["leading_padding_tokens"] += len(body) != len(body.lstrip(b" \t"))
        lexical["trailing_padding_tokens"] += len(body) != len(body.rstrip(b" \t"))
        if DECIMAL.fullmatch(number):
            try:
                value = Decimal(number.decode("ascii"))
                if value.is_finite():
                    decimals.append(value)
                    significand = re.split(rb"[eE]", number)[0]
                    scales[len(significand.partition(b".")[2])] += 1
                    lexical["exponent_tokens"] += b"e" in number.lower()
                    lexical["negative_zero"] += value.is_zero() and value.is_signed()
                    lexical["leading_plus"] += number.startswith(b"+")
            except (InvalidOperation, UnicodeError):
                pass
    pairs = list(zip(values, values[1:]))
    result = {"field": name, "tokens": len(values), "raw_token_bytes": sum(map(len, values)),
              "distinct_tokens": len(set(values)), "decimal_tokens": len(decimals),
              "fraction_digits": sorted(scales.items()), "lexical_exceptions": dict(lexical),
              "consecutive_equal": sum(a == b for a, b in pairs),
              "consecutive_prefix_bytes": sum(prefix_length(a, b) for a, b in pairs)}
    if len(decimals) == len(values) and decimals:
        scale = max(0, max(-value.as_tuple().exponent for value in decimals))
        if scale <= 24 and all(abs(value) < Decimal("1e18") for value in decimals):
            with localcontext() as context:
                context.prec = 80
                multiplier = Decimal(10) ** scale
                integers = [int(value * multiplier) for value in decimals]
            deltas = [b - a for a, b in zip(integers, integers[1:])]
            result["scaled_integer"] = {"scale": scale, "max_magnitude_bits": max(abs(value).bit_length() for value in integers),
                                         "median_delta_bits": sorted(abs(value).bit_length() for value in deltas)[len(deltas) // 2] if deltas else 0}
    return result


def profile(root, cfg, *, run_id="stage3"):
    inputs, all_columns, duplicates, temporal, offsets = [], [], [], [], []
    for label, data in probes(root, cfg, "train"):
        rows, spans = layout(data)
        names = [token.decode("ascii").strip('"') for token in rows[0]]
        columns = list(zip(*rows[1:]))
        inputs.append({"label": label, "bytes": len(data), "sha256": digest(data), "rows": len(rows) - 1})
        all_columns.append({"label": label, "fields": [column_stats(name, values) for name, values in zip(names, columns)]})
        duplicates.append({"label": label, "pairs": [[names[i], names[j]] for i in range(len(columns))
                                                     for j in range(i + 1, len(columns)) if columns[i] == columns[j]]})
        try:
            dates = [datetime.fromisoformat(token.decode("ascii").strip('"')) for token in columns[0]]
            temporal.append({"label": label, "step_seconds_counts": Counter((b - a).total_seconds() for a, b in zip(dates, dates[1:])).most_common()})
        except (ValueError, UnicodeError):
            temporal.append({"label": label, "unsupported": True})
        offset_path = root / ".cache/lab/layout" / (digest(data) + ".json")
        write_json(offset_path, {"input_sha256": digest(data), "offsets": spans})
        offsets.append({"label": label, "path": offset_path.relative_to(root).as_posix(), "sha256": sha256(offset_path),
                        "first_record": spans[1] if len(spans) > 1 else spans[0]})
    result = {"schema_version": 1, "id": "exact-token-profile-v1", "split": "train", "inputs": inputs,
              "columns": all_columns, "identical_columns": duplicates, "timestamp": temporal, "offsets": offsets,
              "hypotheses": ["Compare NUL-separated column streams with per-cell length prefixes, counting dimensions and line endings.",
                             "Test exact signed integer mantissas plus decimal scales and temporal deltas; use exceptions for unsupported spellings.",
                             "Test block-local equality and earlier-field predictors; store references and residuals without assuming relationships persist.",
                             "Test previous-token prefix/suffix coding; measure its metadata cost after zstd rather than assuming raw prefix savings compress."],
              "limitations": "Raw comma layout probe, not a general quoted-CSV parser. Decimal statistics do not authorize rounding or reserialization.",
              "reproduce": ["python", "-m", "algomini", "research-profile"]}
    write_json(root / "reports" / run_id / "profile-train.json", result)
    return result


def representation(data, mode):
    """Training hypothesis only: separate headers, deduplicate data, transpose bytes."""
    rows, spans = layout(data)
    if len(rows) < 2 or b"\0" in data or mode not in (0, 1):
        raise ValueError("Unsupported representation probe input")
    header = data[:spans[0]["line"][1]]
    columns = list(zip(*rows[1:]))
    count = len(rows) - 1
    out = bytearray(b"RPB1" + bytes([mode]) + struct.pack("<III", len(header), count, len(columns)) + header)
    out.extend(record["ending_bytes"] for record in spans[1:])
    for index, column in enumerate(columns):
        reference = next((prior for prior in range(index) if column == columns[prior]), 65535)
        out.extend(struct.pack("<H", reference))
        if reference != 65535:
            continue
        if mode == 0:
            blob = b"\0".join(column)
        else:
            if any(len(token) > 65535 for token in column):
                raise ValueError("Token exceeds byte-plane prototype bound")
            lengths = struct.pack(f"<{count}H", *map(len, column))
            blob = lengths + bytes(token[position] for position in range(max(map(len, column)))
                                   for token in column if position < len(token))
        out.extend(struct.pack("<I", len(blob)) + blob)
    return bytes(out)


def restore_representation(data):
    cursor = 0

    def take(size):
        nonlocal cursor
        if cursor + size > len(data):
            raise ValueError("Truncated representation probe")
        value = data[cursor:cursor + size]
        cursor += size
        return value

    if take(4) != b"RPB1":
        raise ValueError("Invalid representation probe")
    mode = take(1)[0]
    header_size, count, width = struct.unpack("<III", take(12))
    if mode > 1 or count == 0 or width == 0 or count * width > 2 * 1024**2:
        raise ValueError("Invalid representation dimensions")
    header, endings = take(header_size), take(count)
    if any(value > 2 for value in endings):
        raise ValueError("Invalid line endings")
    columns = []
    for index in range(width):
        reference = struct.unpack("<H", take(2))[0]
        if reference != 65535:
            if reference >= index:
                raise ValueError("Invalid duplicate reference")
            columns.append(columns[reference])
            continue
        blob = take(struct.unpack("<I", take(4))[0])
        if mode == 0:
            column = blob.split(b"\0")
        else:
            if len(blob) < 2 * count:
                raise ValueError("Truncated token lengths")
            lengths = struct.unpack_from(f"<{count}H", blob)
            if sum(lengths) != len(blob) - 2 * count:
                raise ValueError("Invalid byte-plane dimensions")
            cells, offset = [bytearray() for _ in range(count)], 2 * count
            for position in range(max(lengths)):
                for row, length in enumerate(lengths):
                    if position < length:
                        cells[row].append(blob[offset])
                        offset += 1
            column = list(map(bytes, cells))
        if len(column) != count:
            raise ValueError("Invalid token count")
        columns.append(column)
    if cursor != len(data):
        raise ValueError("Trailing representation data")
    return header + b"".join(b",".join(column[row] for column in columns) + (b"", b"\n", b"\r\n")[ending]
                             for row, ending in enumerate(endings))


def representation_probes(root, cfg, *, run_id="stage3"):
    from . import codecs, registry

    path = development_files(root, cfg, "train")[0]
    data = bounded_sample(path, cfg["lab"]["baseline_probe_bytes"])
    reference = baselines.transpose_tokens(data)
    variants = [("raw-token-transpose", reference, baselines.restore_tokens),
                ("header-separated-dedup", representation(data, 0), restore_representation),
                ("header-separated-byteplanes", representation(data, 1), restore_representation)]
    results = []
    for name, transformed, restore in variants:
        for level in (9, 19):
            compressed, encoding = baselines.measure(["zstd", "-q", "-T1", f"--stream-size={len(transformed)}", f"-{level}", "-c"], transformed, cfg)
            restored, decoding = baselines.measure(["zstd", "-q", "-d", "-c"], compressed, cfg)
            if restore(restored) != data:
                raise ValueError("Training representation failed exact reconstruction")
            results.append({"id": f"{name}-zstd-{level}", "payload_bytes": len(compressed), "transform_bytes": len(transformed),
                            "roundtrip": True, "encode": encoding, "decode": decoding})
    reference_bytes = next(item["payload_bytes"] for item in results if item["id"] == "raw-token-transpose-zstd-19")
    best = min(results, key=lambda item: item["payload_bytes"])
    incumbent_source = registry.resolve(root, "muse-telemetry-v1")["source_path"]
    incumbent = codecs.evaluate(root, cfg, incumbent_source, data, training_samples=[data[:2048]])
    result = {"schema_version": 1, "id": "training-representation-probes-v1", "split": "train",
              "input": {"path": path.relative_to(root).as_posix(), "sha256": digest(data), "bytes": len(data)},
              "results": results, "reference_payload_bytes": reference_bytes, "best_id": best["id"],
              "best_payload_bytes": best["payload_bytes"],
              "incumbent": {"id": "muse-telemetry-v1", "source_sha256": incumbent["source_sha256"],
                            "payload_bytes": incumbent["payload_bytes"], "archive_bytes": incumbent["archive_bytes"]},
              "supports_further_breadth": best["payload_bytes"] < .95 * incumbent["payload_bytes"],
              "scope": "Handwritten Python training hypotheses, all transform metadata included in payload; not a generated codec or official corpus score",
              "reproduce": ["python", "-m", "algomini", "representation-probes"]}
    write_json(root / "reports" / run_id / "representation-probes.json", result)
    return result
