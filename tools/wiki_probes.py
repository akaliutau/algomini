"""Bounded reversible text prototypes; use the optional text-lab interpreter."""

import argparse
from collections import Counter
from importlib.metadata import version
import os
from pathlib import Path
import re
import struct
import sys
import time

os.environ.setdefault("OMP_NUM_THREADS", "1")
import numpy as np
import pydivsufsort

from algomini import baselines
from algomini.common import digest, load_config, sha256, write_json


def number(value):
    output = bytearray()
    while value >= 128:
        output.append((value & 127) | 128)
        value >>= 7
    output.append(value)
    return output


def read_number(data, position):
    value = shift = 0
    while True:
        byte = data[position]
        position += 1
        value |= (byte & 127) << shift
        if byte < 128:
            return value, position
        shift += 7
        if shift > 28:
            raise ValueError("Invalid integer")


def bwt(data):
    if not data:
        return struct.pack("<I", 0)
    primary, transformed = pydivsufsort.bw_transform(data)
    return struct.pack("<I", primary) + transformed.tobytes()


def unbwt(data):
    if len(data) <= 5:
        return data[4:]
    primary = struct.unpack_from("<I", data)[0]
    return pydivsufsort.inverse_bw_transform(primary, np.frombuffer(data[4:], dtype=np.uint8).copy()).tobytes()


def mtf(data, inverse=False):
    order = list(range(256))
    output = bytearray()
    for value in data:
        rank = value if inverse else order.index(value)
        symbol = order[rank]
        output.append(symbol if inverse else rank)
        order.pop(rank)
        order.insert(0, symbol)
    return bytes(output)


def zero_runs(data, inverse=False):
    output, position = bytearray(), 0
    while position < len(data):
        value = data[position]
        position += 1
        if inverse and value == 0:
            count, position = read_number(data, position)
            output.extend(bytes(count))
        elif not inverse and value == 0:
            end = position
            while end < len(data) and data[end] == 0:
                end += 1
            output.append(0)
            output.extend(number(end - position + 1))
            position = end
        else:
            output.append(value)
    return bytes(output)


def word_dictionary(data, maximum):
    counts = Counter(match[0] for match in re.finditer(rb"[A-Za-z]{3,255}", data))
    words = sorted((word for word, count in counts.items() if count > 2), key=lambda word: (-counts[word] * (len(word) - 2), word))[:maximum]
    table = {word: index + 1 for index, word in enumerate(words)}
    output = bytearray(struct.pack("<I", len(words)))
    for word in words:
        output.extend(number(len(word)))
        output.extend(word)
    previous = 0
    for match in re.finditer(rb"[A-Za-z]{3,255}", data):
        output.extend(data[previous:match.start()].replace(b"\0", b"\0\0"))
        if match[0] in table:
            output.append(0)
            output.extend(number(table[match[0]]))
        else:
            output.extend(match[0])
        previous = match.end()
    output.extend(data[previous:].replace(b"\0", b"\0\0"))
    return bytes(output)


def restore_words(data):
    count = struct.unpack_from("<I", data)[0]
    words, position, output = [], 4, bytearray()
    for _ in range(count):
        size, position = read_number(data, position)
        words.append(data[position:position + size])
        position += size
    while position < len(data):
        value = data[position]
        position += 1
        if value:
            output.append(value)
        else:
            index, position = read_number(data, position)
            output.extend(words[index - 1] if index else b"\0")
    return bytes(output)


def transforms(data):
    transformed = bwt(data)
    moved = transformed[:4] + mtf(transformed[4:])
    return [("raw", data, lambda value: value),
            ("bwt", transformed, unbwt),
            ("bwt-mtf", moved, lambda value: unbwt(value[:4] + mtf(value[4:], True))),
            ("bwt-mtf-zero-runs", moved[:4] + zero_runs(moved[4:]),
             lambda value: unbwt(value[:4] + mtf(zero_runs(value[4:], True), True))),
            *[(f"words-{size}", word_dictionary(data, size), restore_words) for size in (256, 1024, 4096, 8192)]]


def run(root, cfg, size):
    for tiny in (b"", b"a", b"banana", bytes(range(256)), b'<a title="test">one two one\0ONE\r\n</a>'):
        for _, encoded, inverse in transforms(tiny):
            assert inverse(encoded) == tiny
    data = (root / ".cache/datasets/enwik8-first-1000000.xml").read_bytes()[:size]
    output = root / f"reports/wikipedia-search/probes-{size}.json"
    if output.exists():
        import json
        previous = json.loads(output.read_text())
        if previous["source_sha256"] != sha256(__file__) or previous["input_sha256"] != digest(data):
            raise ValueError("Probe definition changed; retain old results under another path")
        return previous
    rows = []
    for name, transformed, inverse in transforms(data):
        if len(transformed) > cfg["lab"]["max_input_bytes"]:
            raise ValueError("Transformed compression input exceeds 2 MiB")
        started = time.perf_counter()
        assert inverse(transformed) == data
        inversion = time.perf_counter() - started
        for level in (19, 22):
            encoded, measurement = baselines.measure(["zstd", "-q", "-T1", f"--stream-size={len(transformed)}", "--ultra", f"-{level}", "-c"], transformed, cfg)
            restored, _ = baselines.measure(["zstd", "-q", "-d", "-c"], encoded, cfg)
            assert inverse(restored) == data
            rows.append({"id": name, "level": level, "transform_bytes": len(transformed), "payload_bytes": len(encoded),
                         "inverse_python_seconds": inversion, "encode": measurement})
            print(name, level, len(encoded), flush=True)
    result = {"schema_version": 1, "input_bytes": len(data), "input_sha256": digest(data), "source_sha256": sha256(__file__),
              "pydivsufsort": version("pydivsufsort"), "results": rows,
              "scope": "Reversible Python research prototypes; no generated-code or decoder-speed claim"}
    write_json(output, result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--bytes", type=int, default=65536)
    args = parser.parse_args()
    if not 0 < args.bytes <= 1000000:
        raise ValueError("Probe must stay within the development prefix")
    root, cfg = load_config("run.toml")
    run(root, cfg, args.bytes)
