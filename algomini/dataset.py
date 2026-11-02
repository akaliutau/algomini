"""Pinned CSV acquisition and chronological, byte-preserving preparation."""

import csv
from datetime import date
import json
from pathlib import Path
import zipfile

from .common import digest, download, sha256, write_json


def preparation_hash(cfg):
    return digest(json.dumps({"dataset": cfg["dataset"], "split": cfg["split"]}, sort_keys=True).encode())


def development_files(root, cfg, split):
    if split not in ("train", "validation"):
        raise ValueError("Exploratory tools can access train or validation only")
    manifest = json.loads((root / cfg["dataset"]["manifest"]).read_text())
    if manifest["preparation_sha256"] != preparation_hash(cfg):
        raise ValueError("Dataset preparation does not match run.toml")
    allowed = (root / cfg["dataset"]["development_dir"] / split).resolve()
    files = []
    for entry in manifest["development"][split]:
        path = (root / entry["path"]).resolve()
        if not path.is_relative_to(allowed):
            raise ValueError("Development sample escapes its allowed split directory")
        if path.stat().st_size > cfg["lab"]["max_input_bytes"] or sha256(path) != entry["sha256"]:
            raise ValueError(f"Development sample failed size/checksum verification: {path.name}")
        files.append(path)
    return files


def bounded_sample(path, max_bytes):
    with Path(path).open("rb") as stream:
        data = stream.read(max_bytes + 1)
    # Keep complete CSV records for the selected source, which has one record per line.
    if len(data) > max_bytes:
        data = data[:max_bytes]
        if not data.endswith(b"\n"):
            data = data[:data.rfind(b"\n") + 1]
    if len(data.splitlines()) < 2:
        raise ValueError("Sample must contain a header and at least one complete record")
    return data


def prepare(root, cfg):
    settings = cfg["dataset"]
    manifest_path = root / settings["manifest"]
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        for split in ("train", "validation"):
            development_files(root, cfg, split)
        private_path = root / settings["sealed_dir"] / "manifest.json"
        if not private_path.is_file() or sha256(private_path) != manifest["sealed"]["manifest_sha256"]:
            raise ValueError("Sealed manifest commitment failed verification")
        return manifest
    archive_path = download(settings["url"], root / settings["cache"],
                            settings["archive_sha256"], settings["max_download_bytes"])
    with zipfile.ZipFile(archive_path) as archive:
        member = archive.getinfo(settings["member"])
        if member.file_size > settings["max_source_bytes"]:
            raise ValueError("Source member exceeds its configured size limit")
        source = archive.read(member)
    lines = source.splitlines(keepends=True)
    header, body = lines[0], lines[1:]
    columns = next(csv.reader([header.decode("utf-8")]))
    groups = {}
    previous = None
    for line in body:
        values = next(csv.reader([line.decode("utf-8")]))
        if len(values) != len(columns):
            raise ValueError("CSV record has a different width from the header")
        day = date.fromisoformat(values[0][:10]).isoformat()
        if previous is not None and day < previous:
            raise ValueError("Source records are not chronological")
        previous = day
        groups.setdefault(day, []).append(line)
    days = sorted(groups)
    policy = cfg["split"]
    gap = policy["gap_days"]
    available = len(days) - 2 * gap
    ntrain = int(available * policy["train_fraction"])
    nval = int(available * policy["validation_fraction"])
    if min(ntrain, nval, available - ntrain - nval) < 1:
        raise ValueError("Not enough day groups for the requested splits and gaps")
    roles = {"train": days[:ntrain],
             "validation": days[ntrain + gap:ntrain + gap + nval],
             "test": days[ntrain + 2 * gap + nval:]}
    gaps = days[ntrain:ntrain + gap] + days[ntrain + gap + nval:ntrain + 2 * gap + nval]
    development, private = {}, []
    sealed_dir = root / settings["sealed_dir"]
    sealed_dir.mkdir(parents=True, exist_ok=True)
    sealed_dir.chmod(0o700)
    for split, selected in roles.items():
        directory = sealed_dir if split == "test" else root / settings["development_dir"] / split
        directory.mkdir(parents=True, exist_ok=True)
        chunks, current, current_days = [], bytearray(header), []
        for day in selected:
            records = b"".join(groups[day])
            if len(header) + len(records) > policy["chunk_bytes"]:
                raise ValueError("A complete day exceeds chunk_bytes; increase the bounded chunk size")
            if len(current) + len(records) > policy["chunk_bytes"]:
                chunks.append((bytes(current), current_days))
                current, current_days = bytearray(header), []
            current.extend(records)
            current_days.append(day)
        if current_days:
            chunks.append((bytes(current), current_days))
        entries = []
        for index, (data, chunk_days) in enumerate(chunks):
            path = directory / f"{split}-{index:03d}.csv"
            path.write_bytes(data)
            if split == "test":
                path.chmod(0o400)
            entries.append({"path": path.relative_to(root).as_posix(), "sha256": digest(data),
                            "bytes": len(data), "groups": chunk_days})
        if split == "test":
            private = entries
        else:
            development[split] = entries
    private_path = sealed_dir / "manifest.json"
    write_json(private_path, {"schema_version": 1, "test": private})
    private_path.chmod(0o400)
    manifest = {"schema_version": 1, "preparation_sha256": preparation_hash(cfg),
                "source": {key: settings[key] for key in ("id", "title", "url", "homepage", "doi", "citation", "license", "license_url", "archive_sha256", "member")},
                "source_sha256": digest(source), "source_bytes": len(source),
                "split_policy": {**policy, "discarded_gap_groups": gaps,
                                 "scope": "Later time periods in one house; no claim of generalization to unseen houses"},
                "development": development,
                "sealed": {"manifest_sha256": sha256(private_path),
                           "access": "Excluded from development loader; OS isolation required before candidate execution in Stage 2"}}
    write_json(manifest_path, manifest)
    return manifest
