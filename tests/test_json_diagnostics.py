"""The research representation must retain exact bytes, including fragments."""

import json

import pytest

from tools.json_campaign_diagnostics import array_records, conditional_entropy, diagnose, restore_lexemes, transpose_lexemes


@pytest.mark.parametrize("data", [
    b"", b"arbitrary text 123\xff", b"[\n", b'{"truncated": "tail',
    b'[{"x":1.00,"x":-0.0,"escaped":"a\\\"b","n":1e+03},\r\n{"x":null}]',
    b'\n  {"field":1},\n  {"field":2}\n]',
])
def test_lexical_layout_reconstructs_exact_bytes(data):
    assert restore_lexemes(transpose_lexemes(data)) == data


def test_reference_rejects_ambiguous_nul_and_corrupt_dimensions():
    with pytest.raises(ValueError, match="NUL"):
        transpose_lexemes(b'"\0"')
    encoded = transpose_lexemes(b'{"x":1}')
    for invalid in (b"", encoded[:-1], encoded + b"x"):
        with pytest.raises(ValueError):
            restore_lexemes(invalid)


def test_repeated_bodies_and_entropy_expose_structure_without_a_floor_claim():
    result = array_records(b'[{"x":1.0}, {"x":1.0}, {"x":1}]')
    assert result["array_items"] == 3 and result["distinct_exact_item_bodies"] == 2
    assert not array_records(b'[{"x":1},')["valid_json"]
    assert conditional_entropy(b"ab" * 100, 0)["bits_per_observation"] == pytest.approx(1)
    assert conditional_entropy(b"ab" * 100, 1)["bits_per_observation"] == pytest.approx(0)


def test_campaign_guard_rejects_final_file_before_any_compression(tmp_path):
    (tmp_path / "request.json").write_text(json.dumps({"dataset": {"train": {"sha256": "0" * 64}}}))
    with pytest.raises(ValueError, match="declared development"):
        diagnose(b"unseen final input", tmp_path)
