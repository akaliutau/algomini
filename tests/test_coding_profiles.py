import copy
import io
import json

import pytest

from algomini import coder, environment
from algomini.common import load_config
from test_cloud_contracts import ROOT


def test_profiles_use_distinct_reasoning_and_correct_model_prices_without_mutating_defaults():
    _, cfg = load_config(ROOT / "run.toml")
    original = copy.deepcopy(cfg)
    initial = coder.session_configuration(cfg, "initial")
    evolved = coder.session_configuration(cfg, "evolved")
    repair = coder.session_configuration(cfg, "repair")
    assert (initial["coder"]["model"], initial["coder"]["reasoning_effort"]) == ("muse-spark-1.3", "high")
    assert (evolved["coder"]["model"], evolved["coder"]["reasoning_effort"]) == ("muse-wonder-1.0", "max")
    assert (repair["coder"]["model"], repair["coder"]["reasoning_effort"]) == ("muse-wonder-1.0", "high")
    for selected, expected in ((initial, .00166), (evolved, .00648)):
        event = {"payload_type": "run.usage", "payload": {"models": [{"model": selected["coder"]["model"],
                 "input_tokens": 1000, "cached_tokens": 400, "output_tokens": 200}]}}
        assert coder.usage_from_events([event], selected)["estimated_usd"] == pytest.approx(expected)
    assert cfg == original
    cfg["coder"]["model"] = "muse-wonder-1.0"
    assert coder.session_configuration(cfg, "initial")["pricing"]["output_usd_per_million"] == 20
    event = {"payload_type": "run.usage", "payload": {"models": [{"model": "muse-wonder-1.0",
             "input_tokens": 1000, "cached_tokens": 400, "output_tokens": 200}]}}
    assert coder.usage_from_events([event], cfg)["estimated_usd"] == pytest.approx(.00648)
    del cfg["pricing"]["models"]["muse-wonder-1.0"]
    with pytest.raises(ValueError, match="explicit token pricing"):
        coder.session_configuration(cfg, "evolved")


def test_wonder_reservations_and_usage_keep_the_existing_budget_boundary(tmp_path):
    _, cfg = load_config(ROOT / "run.toml")
    cfg = coder.session_configuration(cfg, "evolved")
    cfg["budget"].update(total_usd=5, discovery_usd=5, reserve_usd=0)
    coder.budget_entry(tmp_path, cfg, "first", phase="discovery", kind="evolved")
    with pytest.raises(coder.BudgetExhausted):
        coder.budget_entry(tmp_path, cfg, "second", phase="discovery", kind="evolved")
    coder.budget_entry(tmp_path, cfg, "first", {"charged_usd": .00648, "state": "completed"}, phase="discovery", kind="evolved")
    cfg["budget"]["total_usd"] = 5.1
    cfg["budget"]["discovery_usd"] = 5.1
    coder.budget_entry(tmp_path, cfg, "second", phase="discovery", kind="evolved")
    entries = json.loads((tmp_path / "reports/budget/ledger.json").read_text())
    assert entries[0]["charged_usd"] == .00648
    assert entries[1]["model"] == "muse-wonder-1.0" and entries[1]["charged_usd"] == 5


def test_preflight_checks_wonder_before_any_coding_session(monkeypatch):
    _, cfg = load_config(ROOT / "run.toml")
    monkeypatch.setenv("META_API_KEY", "fake-test-key")
    identifiers = ["muse-spark-1.3"]
    class Catalog:
        def open(self, _request, timeout):
            return io.BytesIO(json.dumps({"data": [{"id": value} for value in identifiers]}).encode())
    monkeypatch.setattr(environment, "build_opener", lambda *_: Catalog())
    result = environment.check_api(ROOT, cfg)
    assert not result["ok"] and result["configured_models"]["muse-wonder-1.0"] is False
    identifiers.append("muse-wonder-1.0")
    result = environment.check_api(ROOT, cfg)
    assert result["ok"] and result["inference_calls"] == 0


def test_reasoning_effort_changes_the_generation_identity(tmp_path):
    _, cfg = load_config(ROOT / "run.toml")
    cfg["coder"].update(executable="/usr/bin/python3", api_source=str(ROOT / "crates/algomini-cli/src/api.rs"))
    first = coder.generation_request(tmp_path, cfg, "test", b"sample")[0]
    cfg["coder"]["reasoning_effort"] = "max"
    second = coder.generation_request(tmp_path, cfg, "test", b"sample")[0]
    assert first != second
