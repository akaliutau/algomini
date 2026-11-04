"""Common counted corpus container for native baselines and generated codecs."""

import struct
import zlib


MAX_BLOCK = 4 * 1024**2 + 65536 + 40
MAX_BLOCKS = 128
MAX_ARCHIVE = MAX_BLOCKS * (MAX_BLOCK + 262)
CHUNK_BYTES = 2 * 1024**2
MAX_FILE = 64 * 1024**2
MAX_CHUNKED_ARCHIVE = MAX_FILE + 65536 + 8192


def pack_chunked(method, label, frames, original, model=b""):
    name, label_bytes = method.encode("ascii"), label.encode("utf-8")
    if (not 0 < len(name) <= 64 or not valid_label(label) or len(label_bytes) > 256 or len(model) > 65536
            or not CHUNK_BYTES < len(original) <= MAX_FILE or len(frames) != (len(original) + CHUNK_BYTES - 1) // CHUNK_BYTES):
        raise ValueError("Invalid chunked file dimensions")
    header = struct.pack("<4sHHIQII", b"ALC2", len(name), len(label_bytes), len(model), len(original), len(frames), zlib.crc32(original))
    packed = header + name + label_bytes + model + b"".join(struct.pack("<I", len(frame)) + frame for frame in frames)
    unpack_chunked(packed)
    return packed


def unpack_chunked(data):
    if len(data) < 28 or len(data) > MAX_CHUNKED_ARCHIVE:
        raise ValueError("Invalid chunked file size")
    magic, name_len, label_len, model_len, size, count, checksum = struct.unpack_from("<4sHHIQII", data)
    if (magic != b"ALC2" or not 0 < name_len <= 64 or not 0 < label_len <= 256 or model_len > 65536
            or not CHUNK_BYTES < size <= MAX_FILE or count != (size + CHUNK_BYTES - 1) // CHUNK_BYTES):
        raise ValueError("Invalid chunked file dimensions")
    cursor = 28
    def take(length):
        nonlocal cursor
        end = cursor + length
        if end > len(data):
            raise ValueError("Truncated chunked file")
        result = data[cursor:end]
        cursor = end
        return result
    try:
        method = take(name_len).decode("ascii")
        label = take(label_len).decode("utf-8")
    except UnicodeError:
        raise ValueError("Invalid chunked file text") from None
    if not valid_label(label):
        raise ValueError("Invalid chunked file label")
    model = take(model_len)
    frames = []
    for index in range(count):
        length, = struct.unpack("<I", take(4))
        if not 40 <= length <= MAX_BLOCK:
            raise ValueError("Invalid chunk length")
        frame = take(length)
        if frame[:4] not in (b"ALG1", b"ALG2") or struct.unpack_from("<I", frame, 8)[0] != min(CHUNK_BYTES, size-index*CHUNK_BYTES):
            raise ValueError("Invalid decoded chunk length")
        frames.append(frame)
    if cursor != len(data):
        raise ValueError("Trailing chunked file bytes")
    return {"method": method, "label": label, "model": model, "original_bytes": size, "checksum": checksum, "frames": frames}


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
