from algomini.cloud import presentation
from test_cloud_contracts import snapshot
from test_research_flow import losing_research


def test_report_uses_short_labels_and_describes_the_measured_algorithm(monkeypatch):
    request = snapshot(monkeypatch)
    research = losing_research()
    identifier = "cx-" + "a" * 32 + "-" + "b" * 16
    research["selected"].update(id=identifier, source=f"plugins/{identifier}/codec.rs")
    research["development"]["candidate"].update(id=identifier, encoding_mode="json-lexical")
    research["trials"][0]["item"]["id"] = identifier
    text = presentation.report(request, research)
    assert "cx-aaaaaaaa-bbbb" in text
    assert "| Source SHA-256 | aaaaaaaa |" in text
    assert "| Model SHA-256 | bbbbbbbb |" in text
    assert "JSON lexical channels grouped by key" in text
    assert "no new winning algorithm was generated" in text
    assert "Not opened; final claim not consumed" in text
    assert request["image"] not in text
    assert research["selected"]["source_sha256"] not in text
    assert research["selected"]["id"] == identifier  # Presentation never alters identities.


def test_unrecognized_algorithm_is_not_invented_and_source_notes_are_escaped():
    assert presentation.algorithm({}, {}) == "Custom lossless Rust codec. See the reviewed source for its mechanism."
    assert "raw-byte fallback" in presentation.algorithm({}, {"encoding_mode": "raw"})
    value = presentation.rows_table([("Note", "a|b\n<script>`text`")])
    assert "<script>" not in value and "&#124;" in value and "&#96;" in value


def test_pr_status_is_readable_and_keeps_action_links():
    row = {"status": "pr-open", "snapshot": {"id": "issue-3", "plugin": "json-v1"},
           "run_url": "https://github.com/run", "pr_url": "https://github.com/pr", "charged_usd": 15}
    text = presentation.status_comment(row)
    assert "| Status | Ready for review |" in text
    assert "| Model estimate / held reserve | $15.0000 |" in text
    assert "[Actions run](https://github.com/run)" in text and "[Review PR](https://github.com/pr)" in text
