import copy
import random
import subprocess

from algomini import archive
from algomini.cloud import native
from algomini.common import load_config
from test_cloud_native import ROOT, release_client


def test_binary_chunk_boundary_matches_independent_worker_frames(release_client, tmp_path):
    _, registry, binary, _ = release_client
    _, cfg = load_config(ROOT / "run.toml")
    item = next(row for row in registry["codecs"] if row["id"] == registry["aliases"]["general"])
    # A UTF-8 character crosses the fixed byte boundary; no text normalization occurs.
    data = random.Random(83).randbytes(archive.CHUNK_BYTES-1) + "漢é".encode() + bytes(range(256))*100
    packed, _ = native.file_cli(binary, cfg, data, identifier=item["id"])
    info = archive.unpack_chunked(packed)
    assert len(info["frames"]) == 2
    frames = [native.invoke(binary, item["id"], cfg, 6, data[index:index+archive.CHUNK_BYTES],
              source_sha256=item["source_sha256"])[0] for index in range(0, len(data), archive.CHUNK_BYTES)]
    assert archive.pack_chunked(item["id"], "sample.bin", frames, data) == packed
    assert native.file_cli(binary, cfg, packed)[0] == data
    for malformed in (packed[:-1], packed+b"extra", packed[:24]+bytes(4)+packed[28:]):
        source, output = tmp_path / "corrupt.alc", tmp_path / "restored.bin"
        source.write_bytes(malformed)
        result = subprocess.run([binary, "decompress", source, output], capture_output=True, timeout=30)
        assert result.returncode == 1 and not output.exists()


def test_file_cap_and_existing_output_remain_protected(release_client, tmp_path):
    _, _, binary, _ = release_client
    source, output = tmp_path / "too-large.bin", tmp_path / "out.alc"
    with source.open("wb") as stream:
        stream.truncate(archive.MAX_FILE+1)
    result = subprocess.run([binary, "compress", "--plugin", "general", source, output], capture_output=True, timeout=30)
    assert result.returncode == 1 and b"bounded file limit" in result.stderr and not output.exists()
    source.write_bytes(b"small input")
    output.write_bytes(b"keep existing output")
    result = subprocess.run([binary, "compress", "--plugin", "general", source, output], capture_output=True, timeout=30)
    assert result.returncode == 1 and output.read_bytes() == b"keep existing output"


def test_large_baseline_uses_one_continuous_compression_stream(monkeypatch, tmp_path):
    _, cfg = load_config(ROOT / "run.toml")
    cfg = copy.deepcopy(cfg)
    cfg["baselines"] = [cfg["baselines"][0]]  # One actual gzip stream is enough to exercise the large container.
    cfg["evaluation"]["decode_repetitions"] = 1
    data = random.Random(89).randbytes(archive.MAX_BLOCK+100)
    dictionary = tmp_path / "dictionary.bin"
    dictionary.write_bytes(b"")
    calls = []
    original = native.metrics.measure
    def tracked(argv, data, cfg, **kwargs):
        calls.append(("-d" in argv, len(data)))
        return original(argv, data, cfg, **kwargs)
    monkeypatch.setattr(native.metrics, "measure", tracked)
    result = native.references(cfg, data, dictionary)
    assert calls[0] == (False, len(data))
    assert len(calls) == 2 and calls[1][0] and calls[1][1] > archive.MAX_BLOCK
    assert result["results"][0]["roundtrip"] and result["best_bytes"] > len(data)
