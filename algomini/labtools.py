"""Small registry of deterministic research probes; acceptance scoring is separate."""

from collections import Counter
import csv
from dataclasses import dataclass
import io
import json
import math
from pathlib import Path
import re
import time

from .common import digest, sha256


MAX_INPUT = 2 * 1024**2


@dataclass(frozen=True)
class Probe:
    id: str
    description: str
    families: tuple[str, ...]
    run: object
    version: int = 1


PROBES: dict[str, Probe] = {}


def register(probe):
    """Extensions register trusted Python callables explicitly, never from sample data."""
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", probe.id) or probe.id in PROBES or not callable(probe.run):
        raise ValueError("Invalid or duplicate probe")
    PROBES[probe.id] = probe


def catalog():
    return [{"id": probe.id, "description": probe.description, "families": list(probe.families),
             "version": probe.version, "max_input_bytes": MAX_INPUT}
            for probe in PROBES.values()]


def run(probe_id, data, *, family):
    probe = PROBES[probe_id]
    if len(data) > MAX_INPUT or ("*" not in probe.families and family not in probe.families):
        raise ValueError("Probe input exceeds its bound or applicability")
    started = time.perf_counter()
    facts = json.loads(json.dumps(probe.run(data), allow_nan=False))
    return {"schema_version": 1, "probe_id": probe.id, "version": probe.version, "family": family,
            "implementation_sha256": sha256(Path(__file__)), "input_sha256": digest(data),
            "input_bytes": len(data), "elapsed_seconds": time.perf_counter() - started,
            "facts": facts, "scope": "Research findings; not an acceptance score or compression gain"}


def byte_structure(data):
    counts = Counter(data)
    lines = data.splitlines()
    try:
        data.decode("utf-8")
        utf8 = True
    except UnicodeDecodeError:
        utf8 = False
    return {"utf8": utf8, "distinct_bytes": len(counts),
            "entropy_bits_per_byte": -sum(n / len(data) * math.log2(n / len(data)) for n in counts.values()),
            "lf": data.count(b"\n"), "crlf": data.count(b"\r\n"), "nul": data.count(b"\0"),
            "lines": len(lines), "repeated_lines": len(lines) - len(set(lines)),
            "maximum_line_bytes": max(map(len, lines), default=0)}


def text_repetition(data):
    words = re.findall(rb"[A-Za-z][A-Za-z0-9_-]*", data)
    counts = Counter(words)
    pairs = Counter(zip(words, words[1:]))
    return {"words": len(words), "distinct_words": len(counts),
            "common_words": [(word.decode("ascii"), n) for word, n in counts.most_common(12)],
            "repeated_word_pairs": sum(n - 1 for n in pairs.values()),
            "hypothesis": "Compare dictionary/template savings after counting literals, IDs and exceptions"}


def csv_structure(data):
    try:
        rows = list(csv.reader(io.StringIO(data.decode("utf-8"), newline=""), strict=True))
    except (UnicodeDecodeError, csv.Error) as error:
        return {"supported": False, "reason": str(error)[:200]}
    widths = Counter(map(len, rows))
    return {"supported": len(widths) == 1 and len(rows) > 1, "rows": len(rows), "row_widths": sorted(widths.items()),
            "duplicate_rows": len(rows) - len({tuple(row) for row in rows}),
            "quote_bytes": data.count(b'"'),
            "limitation": "Parsed structure only; retain original bytes for all codec transformations"}


def json_lexemes(data):
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return {"supported": False, "reason": "Non-UTF-8 input"}

    def inspect(records):
        keys, numbers = Counter(), Counter()
        duplicates = objects = 0

        def pairs(items):
            nonlocal duplicates, objects
            objects += 1
            names = [key for key, _ in items]
            duplicates += len(names) - len(set(names))
            keys.update(names)
            return items

        def number(token):
            numbers[token] += 1
            return token

        for record in records:
            json.loads(record, object_pairs_hook=pairs, parse_int=number, parse_float=number,
                       parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Non-JSON constant")))
        return {"supported": True, "records": len(records), "objects": objects, "duplicate_keys": duplicates,
                "common_keys": keys.most_common(12), "distinct_number_spellings": len(numbers),
                "exponent_numbers": sum(n for token, n in numbers.items() if "e" in token.lower()),
                "negative_zero_spellings": sum(n for token, n in numbers.items() if re.fullmatch(r"-0(?:\.0+)?(?:[eE][+-]?0+)?", token)),
                "hypothesis": "Test repeated-key templates while retaining key order, duplicates and exact lexemes"}

    try:
        return inspect([text])
    except (ValueError, RecursionError):
        try:
            records = [line for line in text.splitlines() if line.strip()]
            if not records:
                raise ValueError("Empty JSON input")
            return {**inspect(records), "json_lines": True}
        except (ValueError, RecursionError) as error:
            return {"supported": False, "reason": str(error)[:200], "limitation": "Input may be a bounded fragment"}


for probe in (
    Probe("byte-structure", "Measure byte entropy, UTF-8, line endings and repeated lines", ("*",), byte_structure),
    Probe("text-repetition", "Investigate repeated words, adjacent phrases and dictionary candidates", ("text", "xml", "json", "csv"), text_repetition),
    Probe("csv-structure", "Inspect quoted CSV structure and duplicate records without reserialization", ("csv",), csv_structure),
    Probe("json-lexemes", "Inspect JSON/JSONL keys, duplicate keys and exact numeric spellings", ("json",), json_lexemes),
):
    register(probe)
