"""Bounded ZIP intake; stage development and final bytes separately."""

from collections import defaultdict
import io
from pathlib import PurePosixPath
import stat
import zipfile
import zlib

from ..common import digest


def member_name(name):
    if (not isinstance(name, str) or not 0 < len(name) <= 160 or "\\" in name
            or any(ord(c) < 32 or ord(c) == 127 for c in name)
            or name.startswith("/") or ":" in name or any(p in {"", ".", "..", ".git"} for p in name.split("/"))):
        raise ValueError("ZIP members require ordinary relative paths, at most 160 characters")
    return name


def archive_spec(value, size, rules):
    from .contracts import HASH
    if (not isinstance(value, dict) or set(value) != {"sha256", "members"}
            or not isinstance(value["sha256"], str) or not HASH.fullmatch(value["sha256"])
            or not isinstance(value["members"], list) or not 1 <= len(value["members"]) <= rules["maximum_archive_members"]):
        raise ValueError("Invalid ZIP source descriptor")
    seen, total = set(), 0
    for row in value["members"]:
        if (not isinstance(row, dict) or set(row) != {"name", "bytes", "sha256"}
                or type(row["bytes"]) is not int or not 0 <= row["bytes"] <= rules["maximum_input_bytes"]
                or not isinstance(row["sha256"], str) or not HASH.fullmatch(row["sha256"])):
            raise ValueError("Invalid ZIP member descriptor")
        name = member_name(row["name"])
        if name in seen:
            raise ValueError("Duplicate ZIP member")
        seen.add(name)
        total += row["bytes"]
    if total != size:
        raise ValueError("ZIP member sizes differ from the dataset size")
    return value


def members(raw, rules):
    """Read regular data members only; never extract archive-provided paths."""
    if len(raw) > rules["maximum_archive_bytes"]:
        raise ValueError("ZIP exceeds the configured download limit")
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries = archive.infolist()
            if len(entries) > 128:
                raise ValueError("ZIP contains more than 128 entries")
            selected, seen, total = [], set(), 0
            for item in entries:
                name = member_name(item.filename.rstrip("/") if item.is_dir() else item.filename)
                mode = stat.S_IFMT(item.external_attr >> 16)
                if (item.orig_filename != item.filename or name in seen or item.flag_bits & 1
                        or mode not in {0, stat.S_IFREG, stat.S_IFDIR}
                        or mode == stat.S_IFDIR and not item.is_dir()
                        or item.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}):
                    raise ValueError("ZIP contains duplicate paths, links, encrypted or unsupported entries")
                seen.add(name)
                path = PurePosixPath(name)
                if item.is_dir() or any(p.startswith(".") or p == "__MACOSX" for p in path.parts):
                    continue
                if path.name.lower().startswith(("readme", "license", "licence", "copying")):
                    continue
                total += item.file_size
                if item.file_size > rules["maximum_input_bytes"] or total > 2 * rules["maximum_input_bytes"]:
                    raise ValueError("ZIP data exceeds the configured file or total expansion limit")
                selected.append(item)
            if not 2 <= len(selected) <= rules["maximum_archive_members"]:
                raise ValueError("ZIP needs 2–64 data files; README/license/hidden entries are ignored")
            result = []
            for item in selected:
                with archive.open(item) as stream:
                    data = stream.read(item.file_size + 1)
                if len(data) != item.file_size:
                    raise ValueError("ZIP member size mismatch")
                result.append(({"name": item.filename, "bytes": len(data), "sha256": digest(data)}, data))
            return result
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError, zlib.error) as error:
        raise ValueError("Cannot read the ZIP dataset: " + str(error)[:200]) from None


def family(rows):
    formats = {".json": "json", ".jsonl": "json", ".ndjson": "json", ".csv": "csv", ".xml": "xml",
               ".txt": "text", ".md": "text", ".log": "text", ".tsv": "text"}
    values = {formats.get(PurePosixPath(row["name"]).suffix.lower(), "binary") for row in rows}
    return values.pop() if len(values) == 1 else "binary"


def prepare(raw, url, rules):
    files = members(raw, rules)
    groups = defaultdict(list)
    for row in files:
        groups[row[0]["sha256"]].append(row)
    if len(groups) < 2:
        raise ValueError("ZIP needs at least two distinct data files")
    if len(files) == 2:
        ordered = sorted(files, key=lambda row: (-row[0]["bytes"], row[0]["name"]))
        training, testing = ordered[:1], ordered[1:]
    else:
        # Group exact duplicates; hash order is deterministic and independent of ZIP order.
        ordered = sorted(groups.items())
        training, testing = [], []
        target = max(1, len(files) * 7 // 10)
        for index, (_, group) in enumerate(ordered):
            (training if len(training) < target and index < len(ordered) - 1 else testing).extend(group)
    dataset = {"schema_version": 1, "family": family([row for row, _ in files]),
               "provenance": {"source": url, "license": "Not supplied; review the upstream dataset license",
                              "split": "Two files: larger for development. Otherwise approximately 70:30 by file count, keeping exact duplicate files together.",
                              "assembly": "Members sorted by path and concatenated byte-for-byte; member sizes preserve boundaries."}}
    payloads = {}
    for part, rows in (("train", training), ("test", testing)):
        rows = sorted(rows, key=lambda row: row[0]["name"])
        data = b"".join(data for _, data in rows)
        if len(data) > rules["maximum_input_bytes"]:
            raise ValueError(f"ZIP {part} partition exceeds {rules['maximum_input_bytes']//1024**2} MiB; use smaller sample files")
        dataset[part] = {"url": url, "bytes": len(data), "sha256": digest(data),
                         "archive": {"sha256": digest(raw), "members": [row for row, _ in rows]}}
        payloads[part] = data
    return dataset, payloads


def refetch_development(item, rules):
    """Release rebuilds read only the declared development members."""
    from .contracts import download
    raw = download(item.url, rules, rules["maximum_archive_bytes"], item.archive["sha256"])
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            chunks = []
            for row in item.archive["members"]:
                with archive.open(row["name"]) as stream:
                    data = stream.read(row["bytes"] + 1)
                if len(data) != row["bytes"] or digest(data) != row["sha256"]:
                    raise ValueError("Release development ZIP member changed")
                chunks.append(data)
    except (zipfile.BadZipFile, KeyError, RuntimeError, zlib.error) as error:
        raise ValueError("Cannot reconstruct release development data") from error
    data = b"".join(chunks)
    if len(data) != item.bytes or digest(data) != item.sha256:
        raise ValueError("Release development data changed")
    return data
