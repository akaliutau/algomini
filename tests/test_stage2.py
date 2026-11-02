import copy
import json
from pathlib import Path
import shutil
import struct
import sys

import pytest

from algomini import archive, codecs, evaluator, freeze, registry
from algomini.common import load_config
from algomini.execution import run_isolated


ROOT = Path(__file__).parents[1]


@pytest.fixture
def cfg():
    return copy.deepcopy(load_config(ROOT / "run.toml")[1])


def test_collection_counts_shared_model_exactly_once_and_rejects_bad_lengths():
    blocks = [("a.csv", b"one"), ("b.csv", b"two")]
    model = b"shared-model"
    encoded = archive.pack("candidate", blocks, model)
    expected = 16 + len("candidate") + len(model) + sum(2 + len(label) + 4 + len(data) for label, data in blocks)
    assert len(encoded) == expected
    assert archive.unpack(encoded) == ("candidate", model, blocks)
    for end in range(len(encoded)):
        with pytest.raises(ValueError):
            archive.unpack(encoded[:end])
    with pytest.raises(ValueError, match="Trailing"):
        archive.unpack(encoded + b"extra")
    with pytest.raises(ValueError):
        archive.pack("candidate", [("../escape", b"x")])
    with pytest.raises(ValueError):
        archive.pack("candidate", [("same", b"x"), ("same", b"y")])
    bad = bytearray(encoded)
    struct.pack_into("<I", bad, 8, 129)
    with pytest.raises(ValueError, match="dimensions"):
        archive.unpack(bytes(bad))


def test_parent_timing_and_rss_cannot_be_spoofed_on_stdout(tmp_path):
    script = "import sys,time; sys.stdin.buffer.read(); time.sleep(.08); print('execution_seconds=0;peak_rss_kib=0')"
    result = run_isolated([sys.executable, "-I", "-c", script], cwd=tmp_path, ro=[sys.prefix], data=b"request", timeout=10)
    assert result["returncode"] == 0, result["stderr"]
    assert result["execution_seconds"] >= .08
    assert result["wall_seconds"] >= result["execution_seconds"]
    assert result["setup_seconds"] > 0 and result["peak_rss_kib"] > 0


def test_worker_cannot_open_originals_source_archive_or_env(tmp_path):
    outside = tmp_path / "private"
    outside.mkdir()
    for name in ("original.csv", "dataset.zip", ".env"):
        (outside / name).write_text("private bytes")
    work = tmp_path / "decoder"
    work.mkdir()
    script = """
import sys
from pathlib import Path
for path in Path(sys.argv[1]).joinpath('original.csv'), Path(sys.argv[1]).joinpath('dataset.zip'), Path(sys.argv[1]).joinpath('.env'):
    try: path.read_bytes(); raise AssertionError('original bytes readable')
    except PermissionError: pass
try: list(Path(sys.argv[1]).iterdir()); raise AssertionError('directory readable')
except PermissionError: pass
print('denied')
"""
    result = run_isolated([sys.executable, "-I", "-c", script, str(outside)], cwd=work, ro=[sys.prefix], timeout=10)
    assert result["returncode"] == 0 and result["stdout"].strip() == b"denied", result["stderr"]


def test_worker_address_space_limit_is_enforced(tmp_path):
    script = "try:\n x=bytearray(128*1024**2)\nexcept MemoryError:\n print('limited')"
    result = run_isolated([sys.executable, "-I", "-c", script], cwd=tmp_path, ro=[sys.prefix], memory_mib=64, timeout=10)
    assert result["returncode"] == 0 and result["stdout"].strip() == b"limited", result["stderr"]


def test_trusted_zstd_cli_can_create_required_io_threads(cfg):
    from algomini.baselines import measure
    data = b"a,b\n1.00,-0\n" * 32
    compressed, _ = measure(["zstd", "-q", "-T1", f"--stream-size={len(data)}", "-19", "-c"], data, cfg)
    restored, _ = measure(["zstd", "-q", "-d", "-c"], compressed, cfg)
    assert restored == data


def test_conda_xz_uses_matching_native_library(cfg):
    from algomini.baselines import measure
    data = b"a,b\n1.00,-0\n" * 32
    compressed, _ = measure(["xz", "-9e", "--threads=1", "-c"], data, cfg)
    restored, _ = measure(["xz", "-d", "-c"], compressed, cfg)
    assert restored == data


def test_manifest_registration_source_checks_and_second_codec(tmp_path, cfg):
    registry.register(tmp_path, "identity-v1", ROOT / "rust/codec-worker/src/codec.rs", status="reference")
    registry.register(tmp_path, "zstd-v1", ROOT / "rust/fixtures/zstd-reference.rs", status="reference")
    assert set(registry.discover(tmp_path)) == {"identity-v1", "zstd-v1"}
    plugin = registry.resolve(tmp_path, "zstd-v1")
    before = codecs.core_hashes(ROOT)
    binary, _ = codecs.build(ROOT, cfg, plugin["source_path"])
    data = b"a,b\r\n1.00,-0\r\n" * 128
    encoded, _ = codecs.invoke(ROOT, cfg, binary, 6, data, source_sha256=plugin["source_sha256"])
    decoded, _ = codecs.invoke(ROOT, cfg, binary, 2, encoded, source_sha256=plugin["source_sha256"])
    assert decoded == data and before == codecs.core_hashes(ROOT)
    with pytest.raises(ValueError, match="codec id"):
        codecs.frame_metadata(encoded, source_sha256="f" * 64)
    (tmp_path / "plugins/zstd-v1/codec.rs").write_text("changed")
    with pytest.raises(ValueError, match="checksum"):
        registry.discover(tmp_path)


def test_manifest_source_escape_and_duplicate_modules_are_rejected(tmp_path):
    registry.register(tmp_path, "identity-v1", ROOT / "rust/codec-worker/src/codec.rs")
    with pytest.raises(ValueError, match="Duplicate"):
        registry.register(tmp_path, "duplicate-v1", ROOT / "rust/codec-worker/src/codec.rs")
    path = tmp_path / "plugins/identity-v1/manifest.json"
    record = json.loads(path.read_text())
    record["source"] = "../../outside.rs"
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="contract"):
        registry.discover(tmp_path)


def test_trained_candidate_collection_uses_archived_model_once(tmp_path, cfg, monkeypatch):
    registry.register(tmp_path, "trained-v1", ROOT / "rust/fixtures/trained-zstd.rs")
    # Build uses the unchanged trusted workspace while registry/data are tiny fixtures.
    original_build = codecs.build
    monkeypatch.setattr(codecs, "build", lambda root, cfg, source: original_build(ROOT, cfg, source))
    monkeypatch.setattr(evaluator, "training_samples", lambda root, cfg: [b"training-model"])
    samples = [("a.csv", b"a" * 2048), ("b.csv", b"a" * 1024)]
    result, packed = evaluator.candidate_corpus(tmp_path, cfg, "trained-v1", samples)
    method, model, blocks = archive.unpack(packed)
    assert method == "trained-v1" and model == b"training-model"
    assert result["model_bytes"] == len(model)
    assert all(payload[4] == 2 and struct.unpack_from("<I", payload, 12)[0] == 0 for _, payload in blocks)
    plugin = registry.resolve(tmp_path, method)
    binary, _ = original_build(ROOT, cfg, plugin["source_path"])
    with pytest.raises(ValueError, match="model"):
        codecs.invoke(ROOT, cfg, binary, 2, blocks[0][1])
    with pytest.raises(ValueError, match="checksum"):
        codecs.invoke(ROOT, cfg, binary, 2, blocks[0][1], model=b"wrong-model")


@pytest.fixture
def frozen_copy(tmp_path, cfg, monkeypatch):
    for name in freeze.PYTHON:
        path = tmp_path / f"algomini/{name}.py"
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / f"algomini/{name}.py", path)
    shutil.copytree(ROOT / "rust/codec-worker", tmp_path / "rust/codec-worker", ignore=shutil.ignore_patterns("target"))
    path = tmp_path / cfg["dataset"]["manifest"]
    path.parent.mkdir(parents=True)
    path.write_text('{"development": "fixture"}')
    monkeypatch.setattr(freeze, "runtime", lambda: {"fixture": "same-runtime"})
    created = freeze.create(tmp_path, cfg)
    return tmp_path, cfg, created


def test_freeze_allows_new_plugin_and_research_changes(frozen_copy):
    root, cfg, created = frozen_copy
    registry.register(root, "identity-v1", ROOT / "rust/codec-worker/src/codec.rs")
    (root / "algomini/new_search.py").write_text("# editable search")
    cfg["budget"]["generation_enabled"] = False
    cfg["coder"]["model"] = "other-model"
    assert freeze.verify(root, cfg)["freeze_id"] == created["freeze_id"]
    with pytest.raises(ValueError, match="already frozen"):
        freeze.create(root, cfg)
    with pytest.raises(ValueError, match="checksum"):
        freeze.verify(root, cfg, expected_id="0" * 64)


@pytest.mark.parametrize("change", ["source", "new_build_script", "config", "manifest"])
def test_freeze_detects_trusted_changes(frozen_copy, change):
    root, cfg, _ = frozen_copy
    if change == "source":
        (root / "algomini/evaluator.py").write_text("changed")
    elif change == "new_build_script":
        (root / "rust/codec-worker/build.rs").write_text("fn main() {}")
    elif change == "config":
        cfg["baselines"][0]["encode"] = ["gzip", "-1", "-c"]
    else:
        (root / cfg["dataset"]["manifest"]).write_text("changed")
    with pytest.raises(ValueError, match="Frozen evaluator changed"):
        freeze.verify(root, cfg)


def test_identity_loses_on_compressible_fixture(cfg):
    from algomini.baselines import measure
    data = b"a,b\n1.00,-0\n" * 128
    binary, _ = codecs.build(ROOT, cfg, ROOT / "rust/codec-worker/src/codec.rs")
    candidate, _ = codecs.invoke(ROOT, cfg, binary, 6, data)
    baseline, _ = measure(["gzip", "-n", "-9", "-c"], data, cfg)
    assert len(archive.pack("identity-v1", [("probe.csv", candidate)])) > len(archive.pack("gzip-9", [("probe.csv", baseline)]))


def test_best_baseline_is_complete_corpus_not_per_file_oracle(tmp_path, cfg, monkeypatch):
    dictionary = tmp_path / "model.dict"
    dictionary.write_bytes(b"dict")
    monkeypatch.setattr(evaluator.baselines, "train_dictionary", lambda *args: (dictionary, {"bytes": 4}))
    cfg["baselines"] = [{"id": "first", "encode": ["first", "encode"], "decode": ["first", "decode"]},
                        {"id": "second", "dictionary": True, "encode": ["second", "encode"], "decode": ["second", "decode"]}]
    payloads = {}

    def measure(argv, data, cfg, **kwargs):
        if argv[1] == "encode":
            size = {("first", b"a"): 2, ("first", b"b"): 20, ("second", b"a"): 30, ("second", b"b"): 2}[(argv[0], data)]
            output = (argv[0][0].encode() + data) * (size // 2)
            payloads[output] = data
        else:
            output = payloads[data]
        return output, {"execution_seconds": .001, "peak_rss_kib": 100}

    monkeypatch.setattr(evaluator.baselines, "measure", measure)
    result = evaluator.baseline_corpus(tmp_path, cfg, [("a", b"a"), ("b", b"b")])
    assert result["best_id"] == "first"
    assert result["best_bytes"] == len(archive.pack("first", [("a", b"x" * 2), ("b", b"x" * 20)]))
    assert result["results"][1]["model_bytes"] == 4
    assert result["results"][1]["archive_bytes"] == len(archive.pack("second", [("a", b"x" * 30), ("b", b"x" * 2)], b"dict"))
