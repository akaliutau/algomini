import json

import pytest

from algomini import campaign_coder
from algomini.common import write_json


def test_shared_budget_counts_legacy_reservations_and_preserves_reserve(tmp_path):
    write_json(tmp_path / "reports/budget/ledger.json", [{"charged_usd": 124.}])
    campaign_coder.account(tmp_path, "one", "wiki", 5)
    with pytest.raises(ValueError, match="reserve would be exceeded"):
        campaign_coder.account(tmp_path, "two", "wiki", 5)
    campaign_coder.account(tmp_path, "one", "wiki", 0, settlement={"state": "completed", "charged_usd": .25})
    campaign_coder.account(tmp_path, "two", "wiki", 5)
    entries = json.loads((tmp_path / "reports/research-budget/ledger.json").read_text())
    assert sum(row["charged_usd"] for row in entries) == 5.25


def test_interrupted_request_reservation_is_not_repeated(tmp_path):
    write_json(tmp_path / "reports/budget/ledger.json", [])
    campaign_coder.account(tmp_path, "request", "wiki", 5)
    with pytest.raises(ValueError, match="already reserved"):
        campaign_coder.account(tmp_path, "request", "wiki", 5)
    entries = json.loads((tmp_path / "reports/research-budget/ledger.json").read_text())
    assert len(entries) == 1 and entries[0]["charged_usd"] == 5
