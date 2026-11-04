import copy
import json
from pathlib import Path
import struct
import sys
import pytest
from algomini import archive
from algomini.common import load_config,worker_environment
from algomini.environment import check_api
from algomini.execution import run_isolated
ROOT=Path(__file__).parents[1]
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
    from algomini.measurement import measure
    data = b"a,b\n1.00,-0\n" * 32
    compressed, _ = measure(["zstd", "-q", "-T1", f"--stream-size={len(data)}", "-19", "-c"], data, cfg)
    restored, _ = measure(["zstd", "-q", "-d", "-c"], compressed, cfg)
    assert restored == data

def test_conda_xz_uses_matching_native_library(cfg):
    from algomini.measurement import measure
    data = b"a,b\n1.00,-0\n" * 32
    compressed, _ = measure(["xz", "-9e", "--threads=1", "-c"], data, cfg)
    restored, _ = measure(["xz", "-d", "-c"], compressed, cfg)
    assert restored == data


def test_worker_environment_omits_credentials(monkeypatch):
    monkeypatch.setenv("META_API_KEY", "test-secret")
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    assert "META_API_KEY" not in worker_environment() and "GITHUB_TOKEN" not in worker_environment()


def test_catalog_failure_does_not_serialize_secret(tmp_path, cfg, monkeypatch):
    from urllib.error import HTTPError
    from algomini import environment
    secret = "deliberately-private-test-key"
    monkeypatch.setenv("META_API_KEY", secret)
    class FailedRequest:
        def open(self, request, timeout):
            raise HTTPError(request.full_url, 401, secret, {"Authorization": secret}, None)
    monkeypatch.setattr(environment, "build_opener", lambda *args: FailedRequest())
    result = check_api(tmp_path, cfg)
    assert result["ok"] is False and result["http_status"] == 401
    assert secret not in json.dumps(result)
