import json

import pytest

from algomini.sampling import partition, record_key


def test_json_partition_keeps_duplicate_groups_together_and_lexemes_exact():
    items = [b'{"x":1.00,"x":-0}', b'{"x":2e0}', b'{"x":3}', b'{"x":4}']
    original = b"[\n" + b",\n".join(items * 4) + b"\n]"
    train, validation, facts = partition(original, "json")
    assert json.loads(train) and json.loads(validation)
    assert facts["shared_record_groups"] == 0
    assert facts["distinct_record_groups"] == 4 and facts["records"] == 16
    assert b'1.00,"x":-0' in train and b"2e0" in train
    assert b'"x":3' not in train and b'"x":1.00' not in validation
    assert original.endswith(b"\n]")


def test_grouping_ignores_layout_but_preserves_string_whitespace_and_numeric_spelling():
    assert record_key(b'{ "x" : 1 }') == record_key(b'{"x":1}')
    assert record_key(b'{"x":"a b"}') != record_key(b'{"x":"ab"}')
    assert record_key(b'{"x":1.0}') != record_key(b'{"x":1}')


def test_csv_multiline_records_and_shared_header_are_preserved():
    header = b'a,b\r\n'
    records = [b'"one\nline",1.00\r\n', b'"a,b",-0\r\n', b'four,1e0\r\n']
    train, validation, facts = partition(header + b"".join(records * 3), "csv")
    assert train == header + records[0] * 3
    assert validation == header + b"".join(records[1:] * 3)
    assert facts["shared_record_groups"] == 0 and facts["shared_header_bytes"] == len(header)


def test_json_lines_keep_original_line_endings_and_complete_records():
    train, validation, facts = partition(b'{"x":1}\r\n{"x":2}\n{"x":1}\r\n', "json")
    assert train == b'{"x":1}\r\n' * 2 and validation == b'{"x":2}\n'
    assert facts["strategy"] == "json-lines-record-groups"


def test_csv_unicode_line_separator_is_data_not_a_record_boundary():
    data = 'a,b\n"left\u2028right",1\nother,2\nlast,3\n'.encode()
    training, validation, facts = partition(data, "csv")
    assert training == 'a,b\n"left\u2028right",1\n'.encode()
    assert validation == b"a,b\nother,2\nlast,3\n"
    assert facts["records"] == 3


@pytest.mark.parametrize("data,family", [(b"", "binary"), (b"x", "text"), (bytes(range(256)), "binary"), (b'{"single":1}', "json"), (b'<fragment', "xml")])
def test_opaque_and_tiny_content_has_an_explicit_lossless_byte_partition(data, family):
    train, validation, facts = partition(data, family)
    assert train + validation == data and facts["strategy"] in {"byte-halves", "single-json-document"}
