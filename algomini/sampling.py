"""Bounded development partitions with exact record lexemes and duplicate groups."""

from collections import Counter
import csv
import io
import json
import re

from .common import digest


LEXICAL = re.compile(rb'"(?:[^"\\]|\\.)*"|\s+')


def record_key(data):
    # Ignore layout outside strings for grouping only; codec inputs stay exact.
    return digest(LEXICAL.sub(lambda m: m.group() if m.group().startswith(b'"') else b"", data))


def json_records(data):
    text = data.decode("utf-8")
    parsed = json.loads(text)
    if not isinstance(parsed, list):
        raise ValueError("Single JSON document has no top-level record array")
    decoder, position, records = json.JSONDecoder(), text.index("[") + 1, []
    while True:
        while text[position].isspace():
            position += 1
        if text[position] == "]":
            break
        start = position
        _, position = decoder.raw_decode(text, position)
        records.append(text[start:position].encode("utf-8"))
        while text[position].isspace():
            position += 1
        if text[position] == ",":
            position += 1
    return records


def records(data, family):
    if family == "json":
        try:
            values = json_records(data)
            return values, b"", lambda items: b"[" + b",".join(items) + b"]", "json-array-record-groups"
        except (ValueError, UnicodeError, RecursionError):
            values = [line for line in data.splitlines(keepends=True) if line.strip()]
            if len(values) < 2:
                raise ValueError("No complete JSON records")
            for line in values:
                json.loads(line)
            return values, b"", b"".join, "json-lines-record-groups"
    if family == "csv":
        text = data.decode("utf-8")
        lines, values, previous = io.StringIO(text, newline="").readlines(), [], 0
        reader = csv.reader(io.StringIO(text, newline=""), strict=True)
        widths = set()
        for row in reader:
            widths.add(len(row))
            values.append("".join(lines[previous:reader.line_num]).encode())
            previous = reader.line_num
        if len(values) < 3 or len(widths) != 1:
            raise ValueError("No consistent CSV records")
        header = values.pop(0)
        return values, header, lambda items: header + b"".join(items), "csv-record-groups"
    raise ValueError("Opaque byte content")


def partition(data, family):
    notices = []
    try:
        values, header, assemble, strategy = records(data, family)
        # Include CSV lexical spellings in the grouping key: whitespace may be data.
        key = digest if family == "csv" else record_key
        keys = [key(value) for value in values]
        distinct = list(dict.fromkeys(keys))
        if len(distinct) < 2:
            training = assemble([])
            metadata = {"strategy": strategy, "records": len(values), "distinct_record_groups": len(distinct),
                        "training_groups": 0, "validation_groups": len(distinct), "shared_record_groups": 0,
                        "training_bytes": len(training), "validation_bytes": len(data), "training_sha256": digest(training),
                        "validation_sha256": digest(data), "notices": ["Fewer than two distinct record groups; no independent development holdout. Validation uses the complete document."]}
            return training, data, metadata
        training_keys = set(distinct[:max(1, len(distinct) // 2)])
        training = assemble([value for value, identifier in zip(values, keys) if identifier in training_keys])
        validation = assemble([value for value, identifier in zip(values, keys) if identifier not in training_keys])
        if len(distinct) < len(values):
            notices.append(f"{len(values)} records contain {len(distinct)} distinct lexical groups; duplicate groups stay in one partition.")
        metadata = {"strategy": strategy, "records": len(values), "distinct_record_groups": len(distinct),
                    "training_groups": len(training_keys), "validation_groups": len(distinct) - len(training_keys),
                    "shared_record_groups": 0, "shared_header_bytes": len(header),
                    "scope": "Development views retain record lexemes; JSON array delimiters are assembled, not acceptance inputs."}
    except (ValueError, UnicodeError, csv.Error, RecursionError):
        if family == "json":
            try:
                json.loads(data)
            except (ValueError, UnicodeError, RecursionError):
                pass
            else:
                return b"", data, {"strategy": "single-json-document", "training_bytes": 0, "validation_bytes": len(data),
                    "training_sha256": digest(b""), "validation_sha256": digest(data),
                    "notices": ["One JSON document without separable record groups; validation uses the complete document and model training is empty."]}
        midpoint = len(data) // 2
        training, validation = data[:midpoint], data[midpoint:]
        metadata = {"strategy": "byte-halves", "scope": "Opaque bytes or a single document; partitions may be fragments."}
        if family in {"json", "csv", "xml"}:
            notices.append("No independent complete record partition; candidates must handle fragments through a reversible byte path.")
    metadata.update(notices=notices, training_bytes=len(training), validation_bytes=len(validation),
                    training_sha256=digest(training), validation_sha256=digest(validation))
    return training, validation, metadata


def redundancy(data):
    result = {"lines": len(data.splitlines()), "distinct_lines": len(set(data.splitlines())),
              "scope": "Empirical repetition, not a theoretical compression bound"}
    try:
        values = json_records(data)
        counts = Counter(record_key(value) for value in values)
        result.update(json_array_records=len(values), distinct_record_groups=len(counts),
                      repeated_records=len(values) - len(counts))
    except (ValueError, UnicodeError, RecursionError):
        pass
    return result


def fingerprints(data, family):
    try:
        values, _, _, strategy = records(data, family)
    except (ValueError, UnicodeError, csv.Error, RecursionError):
        return {"strategy": "unavailable", "reason": "No complete record partition"}
    key = digest if family == "csv" else record_key
    hashes = set()
    for value in values:
        hashes.add(key(value))
        if len(hashes) > 4096:
            return {"strategy": "unavailable", "reason": "More than 4096 distinct record groups; exact overlap exceeds this probe's bound"}
    return {"strategy": strategy, "record_hashes": sorted(hashes), "scope": "Record identity diagnostics; not model training data"}


def overlap(development, sample, family):
    other = fingerprints(sample, family)
    if "record_hashes" not in development or "record_hashes" not in other:
        return {"supported": False, "reason": other.get("reason", "Development record signatures unavailable")}
    left, right = set(development["record_hashes"]), set(other["record_hashes"])
    return {"supported": True, "distinct_sample_groups": len(right), "shared_with_development": len(left & right),
            "no_exact_record_overlap": not bool(left & right), "scope": "Exact lexical groups only; absence of overlap does not establish statistical independence"}
