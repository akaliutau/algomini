import copy
import json
from pathlib import Path
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from algomini import coder
import legacy_worker as codecs
from algomini.common import load_config
from algomini.execution import run_isolated


ROOT = Path(__file__).parents[1]


@pytest.fixture
def configuration():
    cfg = copy.deepcopy(load_config(ROOT / "run.toml")[1])
    cfg["coder"]["api_source"] = str(ROOT / "crates/algomini-cli/src/api.rs")
    return cfg


@pytest.fixture(scope="module")
def identity():
    root, cfg = load_config(ROOT / "run.toml")
    binary, _ = codecs.build(root, cfg, root / "rust/codec-worker/src/codec.rs")
    return root, cfg, binary


def test_identity_framing_checksum_and_training(identity):
    root, cfg, binary = identity
    model, _ = codecs.invoke(root, cfg, binary, 3, b"")
    assert model == b""
    data = b'"a",b\r\n1.00,-0\r\n\x00\xff'
    archive, _ = codecs.invoke(root, cfg, binary, 1, data, original_len=len(data))
    restored, _ = codecs.invoke(root, cfg, binary, 2, archive)
    assert restored == data
    assert len(archive) == len(data) + 40
    assert archive[4] == 1
    corrupted = archive[:-1] + bytes([archive[-1] ^ 1])
    with pytest.raises(ValueError, match="checksum"):
        codecs.invoke(root, cfg, binary, 2, corrupted)


def test_worker_rejects_declared_oversize_without_allocating_it(identity):
    root, cfg, binary = identity
    with pytest.raises(ValueError, match="dimensions"):
        codecs.invoke(root, cfg, binary, 4, b"", original_len=cfg["lab"]["max_input_bytes"] + 1)


def test_broken_algorithm_cannot_hide_behind_raw_escape(configuration):
    binary, _ = codecs.build(ROOT, configuration, ROOT / "rust/fixtures/broken.rs")
    sample = b"a,b\n1,2\n"
    archive, _ = codecs.invoke(ROOT, configuration, binary, 1, sample, original_len=len(sample))
    restored, _ = codecs.invoke(ROOT, configuration, binary, 2, archive)
    assert archive[4] == 1 and restored == sample
    with pytest.raises(ValueError, match="Algorithm roundtrip failed"):
        codecs.evaluate(ROOT, configuration, ROOT / "rust/fixtures/broken.rs", sample)


def test_boundary_denies_file_contents_network_fork_and_parent_credentials(tmp_path, monkeypatch):
    secret = tmp_path / "private.txt"
    secret.write_text("outside-workspace")
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setenv("META_API_KEY", "private-parent-credential")
    script = """
import json,os,socket,sys
from pathlib import Path
result={"credential":os.environ.get("META_API_KEY")}
for label, operation in [("read",lambda:Path(sys.argv[1]).read_text()),
                         ("write",lambda:Path(sys.argv[1]).write_text("changed")),
                         ("network",lambda:socket.socket()),("fork",lambda:os.fork())]:
    try: operation();result[label]="allowed"
    except PermissionError: result[label]="denied"
print(json.dumps(result))
"""
    result = run_isolated([sys.executable, "-I", "-c", script, str(secret)], cwd=work,
                          ro=[sys.prefix], timeout=10)
    assert result["returncode"] == 0, result["stderr"]
    report = json.loads(result["stdout"])
    assert report == {"credential": None, "read": "denied", "write": "denied", "network": "denied", "fork": "denied"}
    assert secret.read_text() == "outside-workspace"


def test_boundary_has_no_unrestricted_fallback(tmp_path):
    with pytest.raises(ValueError, match="Invalid isolation"):
        run_isolated(["true"], cwd=tmp_path, network=True, mode="worker")


def test_worker_output_and_wall_limits(tmp_path):
    flood = run_isolated([sys.executable, "-I", "-c", "import os; os.write(1,b'x'*8192)"],
                         cwd=tmp_path, ro=[sys.prefix], output_bytes=1024, timeout=10)
    assert len(flood["stdout"]) <= 1024
    sleeping = run_isolated([sys.executable, "-I", "-c", "import time; time.sleep(5)"],
                            cwd=tmp_path, ro=[sys.prefix], timeout=0.2)
    assert sleeping["timed_out"]


def test_generation_budget_gates_and_duplicate_reservations(tmp_path, configuration):
    configuration["budget"]["provider_spending_cap_verified"] = False
    with pytest.raises(ValueError, match="verified provider cap"):
        coder.budget_entry(tmp_path, configuration, "first")
    configuration["budget"]["provider_spending_cap_verified"] = True
    coder.budget_entry(tmp_path, configuration, "first")
    with pytest.raises(ValueError, match="already been reserved"):
        coder.budget_entry(tmp_path, configuration, "first")
    coder.budget_entry(tmp_path, configuration, "first", {"state": "completed", "charged_usd": 0.25})
    entries = json.loads((tmp_path / "reports/budget/ledger.json").read_text())
    assert len(entries) == 1 and entries[0]["charged_usd"] == 0.25


def test_attempt_cap_is_checked_before_paid_process(tmp_path, configuration):
    configuration["budget"]["stage1_max_attempts"] = 1
    coder.budget_entry(tmp_path, configuration, "first")
    with pytest.raises(ValueError, match="attempt limit"):
        coder.budget_entry(tmp_path, configuration, "second")


def test_usage_uses_last_aggregate_and_does_not_double_count(configuration):
    summary = {"calls": 2, "models": [{"model": configuration["coder"]["model"],
                                      "input_tokens": 1000, "cached_tokens": 400, "output_tokens": 200}]}
    usage = coder.usage_from_events([{"payload_type": "run.usage", "payload": summary}] * 2, configuration)
    assert usage["input_tokens"] == 1000
    assert usage["estimated_usd"] == pytest.approx((600 * 1.25 + 400 * 0.15 + 200 * 4.25) / 1e6)
    assert coder.usage_from_events([], configuration)["estimated_usd"] is None


@pytest.mark.parametrize("tokens", [{"input_tokens": -1}, {"input_tokens": 1, "cached_tokens": 2}, {"output_tokens": True}])
def test_invalid_usage_never_reduces_the_reservation(configuration, tokens):
    events = [{"payload_type": "run.usage", "payload": {"models": [{"model": configuration["coder"]["model"], **tokens}]}}]
    assert coder.usage_from_events(events, configuration)["available"] is False


@pytest.fixture
def fake_coder(tmp_path, configuration, monkeypatch):
    # This is an actual file-producing CLI, not a model/provider call.
    configuration["coder"]["executable"] = "/usr/bin/python3"
    source = tmp_path / "rust/codec-worker/src/api.rs"
    source.parent.mkdir(parents=True)
    source.write_text((ROOT / "rust/codec-worker/src/api.rs").read_text())
    monkeypatch.setenv("META_API_KEY", "fake-test-key")
    return tmp_path, configuration


def test_cli_success_without_artifact_is_rejected(fake_coder):
    root, cfg = fake_coder
    cfg["coder"]["command"] = ["{executable}", "-I", "-c", "pass"]
    with pytest.raises(ValueError, match="valid, independent source artifact"):
        coder.generate(root, cfg, "write a codec", b"a,b\n1,2\n")


def test_cli_artifact_is_cached_without_another_model_call(fake_coder, monkeypatch):
    root, cfg = fake_coder
    cfg["coder"]["command"] = ["{executable}", "-I", "-c", "from pathlib import Path; Path('codec.rs').write_text('pub struct Candidate;')"]
    source, record = coder.generate(root, cfg, "write a codec", b"a,b\n1,2\n")
    assert source.read_text() == "pub struct Candidate;"
    assert record["artifact_valid"]
    cfg["budget"]["generation_enabled"] = False
    monkeypatch.setattr(coder, "run_isolated", lambda *args, **kwargs: pytest.fail("cached run launched another process"))
    cached, same = coder.generate(root, cfg, "write a codec", b"a,b\n1,2\n")
    assert cached == source and same == record


def test_partial_cli_turn_still_requires_independent_artifact_validation(fake_coder):
    root, cfg = fake_coder
    cfg["coder"]["command"] = ["{executable}", "-I", "-c", "from pathlib import Path; Path('codec.rs').write_text('pub struct Candidate;'); raise SystemExit(1)"]
    _, record = coder.generate(root, cfg, "write a codec", b"a,b\n1,2\n")
    assert record["artifact_valid"] and not record["cli_turn_completed"]


def test_coder_cannot_modify_protected_evidence(fake_coder):
    root, cfg = fake_coder
    cfg["coder"]["command"] = ["{executable}", "-I", "-c", "from pathlib import Path; Path('sample.bin').write_text('changed'); Path('codec.rs').write_text('pub struct Candidate;')"]
    with pytest.raises(ValueError, match="Coder generation failed"):
        coder.generate(root, cfg, "write a codec", b"a,b\n1,2\n")


def test_concurrent_builds_share_one_verified_artifact(tmp_path, configuration):
    source = tmp_path / "codec.rs"
    source.write_text((ROOT / "rust/codec-worker/src/codec.rs").read_text() + "\n// " + uuid.uuid4().hex + "\n")
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(codecs.build, ROOT, configuration, source) for _ in range(2)]
        first, second = [future.result() for future in futures]
    assert first[0] == second[0]
    assert first[1]["binary_sha256"] == second[1]["binary_sha256"]


def test_consumed_evaluation_still_blocks_new_generation_before_spending(fake_coder):
    root, cfg = fake_coder
    marker = root / "reports/stage5/consumed.json"
    marker.parent.mkdir(parents=True)
    marker.write_text('{"state":"consumed"}')
    with pytest.raises(ValueError, match="new candidate generation is disabled"):
        coder.generate(root, cfg, "new source", b"train", phase="refinement", kind="evolved")
    assert not (root / "reports/budget/ledger.json").exists()


def test_discovery_budget_is_separate_and_preserves_global_reserve(tmp_path, configuration):
    configuration["budget"]["stage1_max_attempts"] = 1
    coder.budget_entry(tmp_path, configuration, "framework")
    with pytest.raises(ValueError, match="attempt limit"):
        coder.budget_entry(tmp_path, configuration, "more-framework")
    coder.budget_entry(tmp_path, configuration, "research", phase="discovery")
    entries = json.loads((tmp_path / "reports/budget/ledger.json").read_text())
    assert [entry["phase"] for entry in entries] == ["framework", "discovery"]
    configuration["budget"]["total_usd"] = 30
    configuration["budget"]["reserve_usd"] = 20
    with pytest.raises(ValueError, match="reserve"):
        coder.budget_entry(tmp_path, configuration, "excess", phase="discovery")

def test_candidate_and_repair_caps_are_independent_and_global(tmp_path, configuration):
    configuration["budget"]["initial_candidates"] = 1
    configuration["budget"]["repair_sessions"] = 1
    coder.budget_entry(tmp_path, configuration, "initial", phase="discovery")
    with pytest.raises(ValueError, match="candidate-attempt"):
        coder.budget_entry(tmp_path, configuration, "excess", phase="discovery")
    coder.budget_entry(tmp_path, configuration, "repair", phase="discovery", kind="repair")
    with pytest.raises(ValueError, match="repair-session"):
        coder.budget_entry(tmp_path, configuration, "another-repair", phase="refinement", kind="repair")
