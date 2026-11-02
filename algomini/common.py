"""Small shared helpers; secrets are never serialized into run artifacts."""

import hashlib
import json
import os
from pathlib import Path
import tempfile
import tomllib
from urllib.request import urlopen


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False) as stream:
        temp = Path(stream.name)
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
    temp.replace(path)


def load_config(path="run.toml"):
    path = Path(path).resolve()
    with path.open("rb") as stream:
        cfg = tomllib.load(stream)
    if cfg["schema_version"] != 1:
        raise ValueError("Unsupported run configuration version")
    cap = cfg["lab"]["max_input_bytes"]
    if not 0 < cfg["lab"]["baseline_probe_bytes"] <= cfg["lab"]["profile_bytes"] <= cap <= 2 * 1024**2:
        raise ValueError("Probe sizes must be bounded by a maximum of 2 MiB")
    if not 0 < cfg["split"]["chunk_bytes"] <= cap:
        raise ValueError("Chunk size exceeds the input cap")
    parts = cfg["split"]
    if not 0 < parts["train_fraction"] < 1 or not 0 < parts["validation_fraction"] < 1 - parts["train_fraction"]:
        raise ValueError("Training/validation fractions must leave a nonempty test split")
    if parts["gap_days"] < 0:
        raise ValueError("Gap days must be nonnegative")
    budget = cfg["budget"]
    if sum(budget[f"{phase}_usd"] for phase in ("framework", "discovery", "refinement", "reserve")) > budget["total_usd"]:
        raise ValueError("Budget allocations exceed the total spending limit")
    return path.parent, cfg


def download(url, destination, expected_sha256, max_bytes):
    destination = Path(destination)
    if destination.exists():
        if destination.stat().st_size > max_bytes or sha256(destination) != expected_sha256:
            raise ValueError(f"Cached download failed size/checksum verification: {destination.name}")
        return destination
    if not url.startswith("https://"):
        raise ValueError("Downloads require HTTPS")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    try:
        with urlopen(url, timeout=60) as response, temporary.open("wb") as stream:
            total = 0
            while block := response.read(1024**2):
                total += len(block)
                if total > max_bytes:
                    raise ValueError("Download exceeds the configured size limit")
                stream.write(block)
        if sha256(temporary) != expected_sha256:
            raise ValueError("Downloaded artifact failed SHA-256 verification")
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def worker_environment():
    """Trusted baseline subprocesses receive no parent credentials."""
    return {"PATH": os.environ.get("PATH", os.defpath), "LANG": "C", "LC_ALL": "C",
            "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1"}
