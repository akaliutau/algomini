"""Local Stage 2 acceptance orchestration; trusted scoring is in evaluator.py."""

import json
from pathlib import Path

from . import archive, codecs, environment, evaluator, freeze, registry
from .common import sha256, write_json


def byte_signature(result):
    methods = [*result["baselines"]["results"], result["reference"], result["candidate"]]
    return {item["id"]: {"bytes": item["archive_bytes"], "sha256": item["archive_sha256"]} for item in methods}


def acceptance(root, cfg):
    directory = root / "reports/stage2"
    directory.mkdir(parents=True, exist_ok=True)
    ledger = root / "reports/budget/ledger.json"
    budget_before = sha256(ledger)
    public = json.loads((root / cfg["dataset"]["manifest"]).read_text())
    sealed_commitment = public["sealed"]["manifest_sha256"]
    if not (root / freeze.RECORD).exists():
        # Preserve historical Stage 1 build evidence; current binaries always
        # rebuild from the source with the frozen version-2 frame writer.
        path = root / "plugins/muse-telemetry-v1/manifest.json"
        manifest = json.loads(path.read_text())
        if manifest["format_version"] == 1:
            manifest["historical_stage1_build"] = {key: manifest.pop(key) for key in
                ("binary_bytes", "binary_sha256", "build_fingerprint") if key in manifest}
            manifest["format_version"] = 2
            write_json(path, manifest)
        if "identity-v1" not in registry.discover(root):
            registry.register(root, "identity-v1", root / "rust/codec-worker/src/codec.rs", status="reference",
                              provenance={"purpose": "identity must lose on compressible input"})
        environment.snapshot(root, cfg, destination="repro/stage2-environment.json", write_locks=False)
        freeze.create(root, cfg)
    pinned = freeze.verify(root, cfg)

    # This module is installed AFTER the freeze, without changing trusted files.
    code_before = freeze.code_hashes(root)
    if "zstd-reference-v1" not in registry.discover(root):
        registry.register(root, "zstd-reference-v1", root / "rust/fixtures/zstd-reference.rs", status="reference",
                          provenance={"purpose": "manifest discovery after evaluator freeze", "freeze_id": pinned["freeze_id"],
                                      "author": "handwritten reference; not a Muse research result"})
    tiny = b'a,b\r\n1.00,-0\r\n' * 128
    zstd = registry.resolve(root, "zstd-reference-v1")
    binary, _ = codecs.build(root, cfg, zstd["source_path"])
    checks = codecs.correctness(root, cfg, binary, tiny)
    encoded, _ = codecs.invoke(root, cfg, binary, 6, tiny, source_sha256=zstd["source_sha256"])
    restored, _ = codecs.invoke(root, cfg, binary, 2, encoded, source_sha256=zstd["source_sha256"])
    if restored != tiny or code_before != freeze.code_hashes(root):
        raise ValueError("Post-freeze manifest installation failed")
    malformed = []
    for offset in (4, 5, 8, 12, 16, 20, 24):
        bad = bytearray(encoded)
        bad[offset] ^= 0xff
        malformed.append(bytes(bad))
    malformed += [encoded[:end] for end in (0, 4, 20, len(encoded) - 1)]
    for bad in malformed:
        try:
            codecs.invoke(root, cfg, binary, 2, bad, source_sha256=zstd["source_sha256"])
        except ValueError:
            continue
        raise ValueError("Malformed frame was accepted")
    identity = registry.resolve(root, "identity-v1")
    identity_binary, _ = codecs.build(root, cfg, identity["source_path"])
    identity_frame, _ = codecs.invoke(root, cfg, identity_binary, 6, tiny)
    identity_size = len(archive.pack("identity-v1", [("probe.csv", identity_frame)]))
    reference_size = len(archive.pack("zstd-reference-v1", [("probe.csv", encoded)]))
    if identity_size <= reference_size:
        raise ValueError("Identity failed the compressible-input rejection gate")

    first = evaluator.evaluate(root, cfg, "muse-telemetry-v1", expected_freeze=pinned["freeze_id"])
    write_json(directory / "evaluation-first.json", first)
    second = evaluator.evaluate(root, cfg, "muse-telemetry-v1", expected_freeze=pinned["freeze_id"])
    write_json(directory / "evaluation-repeat.json", second)
    repeatable = byte_signature(first) == byte_signature(second)
    if not repeatable:
        raise ValueError("Repeated corpus measurements disagree on bytes/hashes")

    # Decode a saved archive through the manifest CLI path, with only counted
    # model/payloads passed into fresh contained workers.
    import tempfile
    with tempfile.TemporaryDirectory(prefix="algomini-stage2-decode-") as temporary:
        decoded = evaluator.local_decode(root, cfg, root / "reports/evaluations/muse-telemetry-v1/validation/archive.alc", temporary)
        for label, data in evaluator.probes(root, cfg, "validation"):
            if (Path(temporary) / label).read_bytes() != data:
                raise ValueError("Saved archive failed local installation decode")

    result = {"schema_version": 1, "ok": True, "freeze": freeze.verify(root, cfg),
              "acceptance": {"identity_loses": True, "identity_bytes": identity_size, "reference_bytes": reference_size,
                             "malformed_frames_rejected": len(malformed), "second_plugin_after_freeze": True,
                             "tiny_correctness": checks, "saved_archive_decode": decoded,
                             "repeated_bytes_and_hashes_match": repeatable},
              "byte_signature": byte_signature(first), "score": first["score"],
              "sealed_manifest_commitment": sealed_commitment, "sealed_test_opened": False,
              "model_inference_calls": 0, "budget_ledger_sha256": budget_before,
              "budget_ledger_unchanged": sha256(ledger) == budget_before,
              "execution": "local Landlock/libseccomp; no Docker", "split": "validation",
              "reproduce": ["python", "-m", "algomini", "stage2"]}
    if not result["budget_ledger_unchanged"]:
        raise ValueError("Stage 2 unexpectedly modified the generation ledger")
    write_json(directory / "summary.json", result)
    candidate, baseline = first["candidate"], first["baselines"]
    rows = ["# Stage 2: frozen local evaluator", "",
            f"Freeze: `{pinned['freeze_id']}`. Exact code/config/runtime pins are in `repro/evaluator-freeze.json`.", "",
            f"Three fixed validation probes total {first['input_bytes']:,} bytes. Each block is at most {cfg['evaluation']['probe_bytes']:,} bytes.",
            "Training models use training chunks only. The sealed test was not opened. No inference calls or Docker were used.", "",
            "| Configuration | Complete corpus bytes | Shared model bytes |", "|---|---:|---:|"]
    for item in [*baseline["results"], first["reference"], candidate]:
        rows.append(f"| {item['id']} | {item['archive_bytes']} | {item['model_bytes']} |")
    rows += ["", f"The strongest declared configuration is **{baseline['best_id']}**. Muse's Stage 1 module has **{first['score']['archive_gain']:.2%}** archive gain on these validation probes.",
             f"Minimum per-probe decode speed is **{first['score']['minimum_decode_mib_per_second']:.2f} MiB/s**; peak decoder job RSS is **{first['score']['peak_decode_rss_kib']:,} KiB**.",
             f"The incremental executable is {candidate['decoder_package_bytes']:,} bytes; deployment saving is **{first['score']['deployment_saving_bytes']:,} bytes**, assuming the common runtime/libzstd is available.",
             "These are development measurements. They do not establish an unseen-data win, scientific novelty, or approval to install a winning module.", "",
             "Two independent runs agree on every complete archive size and SHA-256, including all six baselines, the structured reference, and the candidate.",
             "Identity loses on compressible input; bad flags, dimensions, codec ids, checksums and truncations are rejected. A second handwritten module was registered after the freeze and round-tripped without core edits.",
             "The test suite additionally exercises original-file/credential/network/process denial, timeout, output and address-space limits, trained-model sharing, manifest tampering and freeze mismatches.", "",
             "Timing starts at the trusted supervisor's readiness marker before input transfer and ends at child exit. It includes native startup and teardown; Python isolation setup is separate. Three fresh processes are measured per block. RSS includes the launcher and is conservative. Timings vary; only byte counts/hashes are required to repeat.",
             "The common ALC1 container counts the method id, filenames, lengths and shared model; generated ALG2 frames additionally count their full header, checksum, raw escapes and payloads. B is the smallest complete baseline corpus, never a per-file oracle.", "",
             "See `evaluation-first.json`, `evaluation-repeat.json`, `summary.json`, `docs/evaluator.md`, and `repro/README.md`."]
    (directory / "report.md").write_text("\n".join(rows) + "\n")
    return result
