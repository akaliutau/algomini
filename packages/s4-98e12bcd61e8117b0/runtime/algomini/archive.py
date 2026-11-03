"""Common counted corpus container for native baselines and generated codecs."""

import struct


MAX_BLOCK = 4 * 1024**2 + 65536 + 40
MAX_BLOCKS = 128
MAX_ARCHIVE = MAX_BLOCKS * (MAX_BLOCK + 262)


def pack(method, blocks, model=b""):
    name = method.encode("ascii")
    if not 0 < len(name) <= 64 or len(model) > 65536 or not 0 < len(blocks) <= MAX_BLOCKS:
        raise ValueError("Collection dimensions exceed bounds")
    out = bytearray(b"ALC1" + struct.pack("<HHII", len(name), 0, len(blocks), len(model)) + name + model)
    labels = set()
    for label, payload in blocks:
        encoded = label.encode("utf-8")
        if not valid_label(label) or label in labels or len(encoded) > 256 or len(payload) > MAX_BLOCK:
            raise ValueError("Invalid collection block")
        labels.add(label)
        out.extend(struct.pack("<H", len(encoded)) + encoded + struct.pack("<I", len(payload)) + payload)
    return bytes(out)


def valid_label(label):
    return bool(label) and label not in (".", "..") and "/" not in label and "\\" not in label and "\0" not in label


def unpack(data):
    if len(data) < 16 or len(data) > MAX_ARCHIVE or data[:4] != b"ALC1":
        raise ValueError("Invalid collection header")
    name_len, reserved, count, model_len = struct.unpack_from("<HHII", data, 4)
    if reserved or not 0 < name_len <= 64 or not 0 < count <= MAX_BLOCKS or model_len > 65536:
        raise ValueError("Invalid collection dimensions")
    cursor = 16

    def take(size):
        nonlocal cursor
        end = cursor + size
        if end > len(data):
            raise ValueError("Truncated collection")
        value = data[cursor:end]
        cursor = end
        return value

    try:
        method = take(name_len).decode("ascii")
        model = take(model_len)
        blocks, labels = [], set()
        for _ in range(count):
            label_len = struct.unpack("<H", take(2))[0]
            if not 0 < label_len <= 256:
                raise ValueError("Invalid collection label length")
            label = take(label_len).decode("utf-8")
            payload_len = struct.unpack("<I", take(4))[0]
            if not valid_label(label) or label in labels or payload_len > MAX_BLOCK:
                raise ValueError("Invalid collection block")
            labels.add(label)
            blocks.append((label, take(payload_len)))
    except UnicodeError as error:
        raise ValueError("Invalid collection text") from error
    if cursor != len(data):
        raise ValueError("Trailing collection data")
    return method, model, blocks
