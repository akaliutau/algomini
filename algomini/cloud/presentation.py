"""Human-facing labels only; stored codec identities and receipts stay unchanged."""

import html
import re


def cell(value):
    return html.escape(str(value), quote=False).replace("|", "&#124;").replace("`", "&#96;").replace("\r", " ").replace("\n", " ")


def short_id(value):
    match = re.fullmatch(r"cx-([a-f0-9]{32})-([a-f0-9]{16})", value)
    if match:
        return f"cx-{match[1][:8]}-{match[2][:4]}"
    return re.sub(r"(?<=-)[a-f0-9]{16,64}$", lambda match: match[0][:8], value)


def rows_table(rows, headings=("Detail", "Value")):
    return "\n".join(["| " + " | ".join(headings) + " |", "| " + " | ".join("---" for _ in headings) + " |",
                      *("| " + " | ".join(cell(value) for value in row) + " |" for row in rows)])


def algorithm(selected, candidate):
    mode = candidate.get("encoding_mode")
    if mode == "raw":
        return "Exact raw-byte fallback; no structured transform selected."
    if mode == "json-lexical":
        return "JSON lexical channels grouped by key, preserving spelling, whitespace and order; compressed with zstd."
    if mode == "zstd":
        return "Plain zstd compression; the JSON layout was not selected."
    if selected.get("id", "").startswith("wk-"):
        return "Text codec comparing adaptive PPM/arithmetic coding with zstd fallback."
    if selected.get("id", "").startswith("s4-"):
        return "CSV codec comparing column streams, byte-position layouts and zstd fallback."
    note = selected.get("description")
    return "Custom lossless Rust codec." + (" Source note: " + note[:240] if note else " See the reviewed source for its mechanism.")


def status_comment(row):
    request = row["snapshot"]
    labels = {"reserved": "Queued", "researching": "Researching", "frozen": "Candidate frozen",
              "test-consumed": "Final benchmark", "pr-open": "Ready for review", "released": "Published",
              "no-development-gain": "No development improvement", "failed-benchmark": "No final improvement",
              "failed-research": "Research stopped", "interrupted": "Interrupted; review required"}
    text = f"**Algomini · {cell(request['plugin'])}**\n\n" + rows_table([
        ("Status", labels.get(row["status"], row["status"])), ("Request", request["id"]),
        ("Model estimate / held reserve", f"${row['charged_usd']:.4f}"), ("Ranking", "Archive size first; speed secondary")])
    text += f"\n\n[Actions run]({row['run_url']})"
    for key, label in (("pr_url", "Review PR"), ("release_url", "Download release")):
        if row.get(key):
            text += f" · [{label}]({row[key]})"
    if row["status"] == "interrupted":
        text += "\n\nReservations and final-test claims are retained. A new paid attempt requires review."
    if row.get("failure"):
        text += "\n\n" + rows_table([("Failure", row["failure"][:1000])])
    if row.get("report"):
        text += "\n\n" + row["report"]
    return text


def table(result, scope):
    if not result.get("candidate"):
        return rows_table([(scope, result.get("failure", "Not evaluated"))])
    baseline = result["baselines"]
    rows = []
    for row in [result["candidate"], *baseline["results"]]:
        metrics = row["files"][0]
        rows.append((scope, short_id(row["id"]), f"{row['archive_bytes']:,}", f"{1-row['archive_bytes']/baseline['best_bytes']:.2%}",
                     f"{metrics['encode']['execution_seconds']:.3f}", f"{metrics['decode']['decode_mib_per_second']:.2f}",
                     f"{metrics['decode']['peak_rss_kib']/1024:.2f}", "yes"))
    return rows_table(rows, ("Scope", "Method", "Archive bytes", "Saving", "Encode s", "Decode MiB/s", "RSS MiB", "Exact"))


def report(request, research, final=None):
    final = final or {}
    selected = research["selected"]
    candidate = (final if final.get("candidate") else research["development"])["candidate"]
    origin = research["origin"]
    reused = origin.startswith("reused")
    target = request["policy"]["archive_gain_target"]
    outcome = "Qualified for a release PR" if final.get("size_first_win") else "No final improvement" if final else "Stopped at development"
    if final.get("failure"):
        outcome = "Final benchmark failed"
    text = f"# Plugin: {cell(request['plugin'])}\n\n" + rows_table([
        ("Outcome", outcome), ("Algorithm", algorithm(selected, candidate)),
        ("Selected candidate", short_id(selected.get("id", candidate["id"]))),
        ("Origin", "Existing reviewed codec reused" if reused else origin),
        ("Request mode", request.get("mode", "generate")),
        ("Generation failures", len(research.get("failures", []))),
        ("Target", f"{target:.0%} saving; strictly positive saving qualifies"),
        ("Target reached", "Not measured" if not final.get("score") else str(final["score"]["archive_gain"] >= target)),
        ("Model estimate / held reserve", f"${research['charged_usd']:.4f}"),
        ("Client executable", f"{research['development']['candidate']['decoder_package_bytes']:,} bytes; separate from archive size")])
    source = selected.get("source")
    if source:
        text += f"\n\n[Review Rust source](https://github.com/{request['repository']}/blob/codec/{request['id']}/{source})"
    text += f"\n\n[Dataset manifest and file split](https://github.com/{request['repository']}/blob/codec/{request['id']}/reports/campaigns/{request['id']}/dataset-manifest.json)"
    text += ("\n\nThis request qualifies an existing implementation; no new winning algorithm was generated." if reused
             else "\n\nThe selected implementation is included in this request's review evidence.")
    text += "\n\nArchive size ranks first. Exact reconstruction and memory/time limits are mandatory.\n\n"
    for scope, result in (("Validation", research.get("validation")), ("Development", research["development"]), ("Final", final)):
        if result or scope == "Final":
            text += table(result, scope) + "\n\n"
    unopened = "Not evaluated; final claim not consumed" if request["dataset"]["train"].get("archive") else "Not opened; final claim not consumed"
    checks = [("Final access", "One frozen final benchmark; no subsequent tuning" if final else unopened)]
    if final:
        checks.append(("Previously used test hash", str(bool(final.get("test_previously_used")))))
    checks += [("Sampling", notice) for notice in research.get("sampling", {}).get("notices", [])]
    overlap = final.get("record_overlap")
    if overlap:
        checks.append(("Final record overlap", f"{overlap['shared_with_development']} of {overlap['distinct_sample_groups']} groups also occur in development" if overlap["supported"] else overlap["reason"]))
        checks.append(("Independence", "This overlap check alone cannot establish statistical independence"))
    dataset = request["dataset"]
    for part, label in (("train", "Development files"), ("test", "Final files")):
        item = dataset[part]
        if item.get("archive"):
            checks.append((label, f"{len(item['archive']['members'])} files; {item['bytes']:,} bytes"))
    text += rows_table(checks) + "\n\n"
    trials = []
    for trial in research.get("trials", []):
        value = trial["result"]["candidate"]
        trials.append((short_id(trial["item"]["id"]), trial["origin"], f"{value['archive_bytes']:,}",
                       value.get("payload_bytes", "unreported"), value["model_bytes"], value.get("encoding_mode", "unreported")))
    if trials:
        text += "<details>\n<summary>Candidate trials and build details</summary>\n\n"
        text += rows_table(trials, ("Trial", "Origin", "Archive bytes", "Payload bytes", "Model bytes", "Mode")) + "\n\n"
    environment = final.get("environment", research.get("environment", {}))
    text += rows_table([("Cloud image", "lab@" + request["image"].split("sha256:")[-1][:8]),
                       ("Source SHA-256", selected["source_sha256"][:8]), ("Model SHA-256", selected["model_sha256"][:8]),
                       ("Host", environment.get("cpu", "See Actions log")), ("Kernel", environment.get("kernel", "unknown")),
                       ("Generation failures", len(research.get("failures", [])))])
    if research.get("failures"):
        text += "\n\n" + rows_table([(row["origin"], row["failure"][:240]) for row in research["failures"]], ("Attempt", "Failure"))
    text += "\n\nFull identities and detailed evidence are retained in the campaign JSON files. Unknown provider usage remains reserved."
    if trials:
        text += "\n\n</details>"
    return text + "\n"
