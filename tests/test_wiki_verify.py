import json

import pytest

from algomini import wiki_verify
from algomini.common import sha256, write_json


def test_claim_is_exclusive_and_interruption_cannot_retry(tmp_path):
    wiki_verify.claim(tmp_path, {"freeze_id": "one"})
    with pytest.raises(FileExistsError):
        wiki_verify.claim(tmp_path, {"freeze_id": "two"})
    with pytest.raises(ValueError, match="never rerun"):
        wiki_verify.replay(tmp_path, "one")
    receipt = tmp_path / wiki_verify.OUTPUT / "consumed.json"
    assert json.loads(receipt.read_text())["freeze_id"] == "one"


def test_cached_final_outcome_never_opens_data_or_benchmarks(tmp_path, monkeypatch):
    directory = tmp_path / wiki_verify.OUTPUT
    wiki_verify.claim(tmp_path, {"freeze_id": "one"})
    result = {"size_first_win": False, "failure": "failed exactly once"}
    write_json(directory / "result.json", result)
    write_json(directory / "terminal.json", {"freeze_id": "one", "receipt_sha256": sha256(directory / "consumed.json"),
               "result_sha256": sha256(directory / "result.json")})
    monkeypatch.setattr(wiki_verify, "verify", lambda *_: ({}, "one"))
    def deny(*_, **__):
        raise AssertionError("Cached replay tried to open data or measure again")
    monkeypatch.setattr(wiki_verify.wiki_benchmark, "window", deny)
    monkeypatch.setattr(wiki_verify.wiki_benchmark, "measure", deny)
    assert wiki_verify.run(tmp_path, {}) == result
    write_json(directory / "result.json", {"size_first_win": True})
    with pytest.raises(ValueError, match="checksum mismatch"):
        wiki_verify.run(tmp_path, {})
