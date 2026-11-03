"""Fetch only the exact local embedding files pinned in repro/memory-model.json."""

import hashlib
import json
from pathlib import Path
from urllib.request import urlopen


def bootstrap(root):
    spec = json.loads((root / "repro/memory-model.json").read_text())
    directory = root / spec["directory"]
    directory.mkdir(parents=True, exist_ok=True)
    for name, checksum in spec["files"].items():
        path = directory / name
        if path.exists():
            if hashlib.sha256(path.read_bytes()).hexdigest() != checksum:
                raise ValueError("Cached embedding file checksum mismatch: " + name)
            continue
        url = f"https://huggingface.co/{spec['repository']}/resolve/{spec['revision']}/{name}"
        with urlopen(url, timeout=60) as response:
            data = response.read(10 * 1024**2 + 1)
        if len(data) > 10 * 1024**2 or hashlib.sha256(data).hexdigest() != checksum:
            raise ValueError("Embedding download checksum mismatch: " + name)
        path.write_bytes(data)
    print(json.dumps({"ok": True, "model": spec["repository"], "revision": spec["revision"]}))


if __name__ == "__main__":
    bootstrap(Path(__file__).resolve().parents[1])
