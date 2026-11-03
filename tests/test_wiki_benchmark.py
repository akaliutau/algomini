from pathlib import Path

import pytest

from algomini import wiki_benchmark


def test_wikipedia_speed_is_secondary_but_correctness_and_memory_are_required():
    score = {"gates": {"exact_reconstruction": True, "decode_rss": True, "decode_speed": False, "archive_gain": False}}
    assert wiki_benchmark.eligible(score)
    score["gates"]["exact_reconstruction"] = False
    assert not wiki_benchmark.eligible(score)
    score["gates"].update(exact_reconstruction=True, decode_rss=False)
    assert not wiki_benchmark.eligible(score)


def test_reserved_window_cannot_be_opened_before_final_claim(tmp_path):
    policy = Path(__file__).parents[1] / "wiki-search.toml"
    (tmp_path / "wiki-search.toml").write_bytes(policy.read_bytes())
    with pytest.raises(ValueError, match="Reserved later window"):
        wiki_benchmark.window(tmp_path, 10000000, 1000000)
    with pytest.raises(ValueError, match="Reserved later window"):
        wiki_benchmark.window(tmp_path, 10000000, 1000000, final=True)
    with pytest.raises(ValueError, match="bounded"):
        wiki_benchmark.window(tmp_path, 0, 2 * 1024**2 + 1)
