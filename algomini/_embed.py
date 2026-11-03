"""Private JSON-in/JSON-out bridge, run by the optional embedding interpreter."""

import json
from importlib.metadata import version
import sys

from model2vec import StaticModel


if __name__ == "__main__":
    if version("model2vec") != sys.argv[2]:
        raise ValueError("Optional embedding library version differs from its pin")
    raw = sys.stdin.buffer.read(2 * 1024**2 + 1)
    if len(raw) > 2 * 1024**2:
        raise ValueError("Embedding request exceeds its bound")
    model = StaticModel.from_pretrained(sys.argv[1], normalize=True, force_download=False)
    print(json.dumps(model.encode(json.loads(raw)).tolist(), allow_nan=False))
