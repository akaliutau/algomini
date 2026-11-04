"""Optional local embedding process; the core CLI needs only the standard library."""

import json
import math
from pathlib import Path
import subprocess

from .common import digest, sha256, worker_environment


class Embedder:
    def __init__(self, root):
        self.root = Path(root)
        self.python = self.root / ".tools/memory-env/bin/python"
        manifest = self.root / "repro/memory-model.json"
        self.spec = json.loads(manifest.read_text())
        self.id = digest(manifest.read_bytes() + Path(__file__).with_name("_embed.py").read_bytes())
        self.model = self.root / self.spec["directory"]
        for name, checksum in self.spec["files"].items():
            if sha256(self.model / name) != checksum:
                raise ValueError("Local embedding model checksum mismatch")
        if not self.python.is_file():
            raise FileNotFoundError("Optional embedding environment is missing")

    def encode(self, texts):
        payload = json.dumps(texts).encode()
        if len(payload) > 2 * 1024**2:
            raise ValueError("Embedding batch exceeds 2 MiB")
        script = Path(__file__).with_name("_embed.py")
        result = subprocess.run([str(self.python), "-I", str(script), str(self.model), self.spec["library"].split("==")[1]], input=payload,
                                capture_output=True, timeout=120, check=True, env={**worker_environment(), "HF_HUB_OFFLINE": "1"})
        vectors = json.loads(result.stdout)
        if (len(vectors) != len(texts) or any(len(vector) != self.spec["dimensions"]
                or any(not isinstance(x, (int, float)) or not math.isfinite(x) for x in vector) for vector in vectors)):
            raise ValueError("Invalid embedding output dimensions/values")
        return vectors


def load(root, *, required=False):
    root = Path(root)
    available = (root / "repro/memory-model.json").is_file() and (root / ".tools/memory-env/bin/python").is_file()
    if available:
        return Embedder(root)
    if required:
        raise ValueError("Install the optional memory environment and pinned model; see docs/laboratory.md")
    return None
