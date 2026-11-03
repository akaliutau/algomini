"""Historical Stage 0 workflow, kept outside CLI routing."""

from datetime import datetime, timezone
import json

from . import dataset, environment, lab
from .common import sha256, write_json


def write_report(root, cfg, checks, profile, baselines):
    rows = ["# Stage 0: local experiment foundation", "",
            "Prepared locally from the pinned UCI Appliances Energy Prediction archive (CC BY 4.0).",
            "Original CSV bytes are preserved. Chronological day groups use a one-day gap at each split boundary.", "",
            f"Training profile: {profile['input']['bytes']:,} bytes, {profile['input']['rows']} records, {profile['input']['columns']} columns.",
            f"Baseline probe: {baselines['input']['bytes']:,} training bytes; all compression inputs stay below 2 MiB.", "",
            "| Baseline | Payload bytes | Model bytes | Total bytes | Exact reconstruction |",
            "|---|---:|---:|---:|---|"]
    for item in baselines["results"]:
        rows.append(f"| {item['id']} | {item['payload_bytes']} | {item['model_bytes']} | {item['archive_bytes']} | yes |")
    reference = baselines["reference"]
    rows += ["", f"The strongest baseline on this probe is **{baselines['best_baseline']}**.",
             f"The trusted raw-token transpose + zstd-19 reference uses **{reference['archive_bytes']} bytes**, "
             f"a **{reference['gain_vs_best_baseline']:.2%}** reduction on this training probe. Its framing is included.",
             "This is a known representation reference implemented in Python, not a Muse-generated codec or sealed-test result.",
             "No incremental decoder package saving has been established. Dictionary bytes are counted once for this one-probe workload; "
             "the final evaluator must account for sharing once across the declared corpus.", "",
             "Measured structure: " + "; ".join(profile["hypotheses"]), "",
             "API preflight: " + ("authenticated model catalog succeeded" if checks.get("api", {}).get("ok") else "offline presence/CLI checks only") + ". No model inference was performed.",
             "Candidate-code containment belongs to Stage 2. The current tools execute installed, trusted binaries only.",
             "Before paid research, verify the provider spending ceiling and explicitly enable generation in run.toml.", "",
             "Reproduce: `python -m algomini stage0 --check-api`. Check the captured environment with `python -m algomini versions --check`.",
             "See `repro/README.md`, `summary.json`, `profile-train.json`, `baselines-train.json`, and `observations.jsonl`."]
    (root / cfg["report_dir"] / "report.md").write_text("\n".join(rows) + "\n")



def run(root, cfg, check_api=False):
    checks = environment.doctor(root, cfg, check_api)
    if not checks["ok"]:
        print(json.dumps(checks, indent=2))
        return 1
    if (root / "repro/environment.json").exists():
        version_check = environment.verify_versions(root, cfg)
        if not version_check["ok"]:
            print(json.dumps(version_check, indent=2))
            return 1
    else:
        environment.snapshot(root, cfg)
    manifest = dataset.prepare(root, cfg)
    profile = lab.profile(root, cfg)
    baselines = lab.baselines(root, cfg)
    report_dir = root / cfg["report_dir"]
    run_time = datetime.now(timezone.utc).isoformat()
    observations = [{"id": run_time + "/structure", "split": "train", "input": profile["input"],
                     "tags": ["telemetry", "csv", "timestamp", "cross-field"],
                     "hypotheses": profile["hypotheses"],
                     "measurements": {key: profile[key] for key in ("identical_lexeme_columns", "timestamp", "strongest_numeric_pairs")},
                     "evidence": "profile-train.json", "reproduce": profile["reproduce"]},
                    {"id": run_time + "/baselines", "split": "train", "input": baselines["input"],
                     "tags": ["compression", "column-transpose", "baselines"],
                     "measurements": baselines["results"], "reference": baselines["reference"],
                     "dictionary": baselines["dictionary"], "best_baseline": baselines["best_baseline"],
                     "evidence": "baselines-train.json", "reproduce": baselines["reproduce"]}]
    with (report_dir / "observations.jsonl").open("a") as stream:
        for record in observations:
            record["environment_sha256"] = sha256(root / "repro/environment.json")
            stream.write(json.dumps(record, sort_keys=True, allow_nan=False) + "\n")
    write_report(root, cfg, checks, profile, baselines)
    result = {"schema_version": 1, "local_foundation_ready": True,
              "dataset_manifest": cfg["dataset"]["manifest"],
              "sealed_manifest_sha256": manifest["sealed"]["manifest_sha256"],
              "profile_input_bytes": profile["input"]["bytes"],
              "baseline_input_bytes": baselines["input"]["bytes"],
              "baseline_roundtrips": len(baselines["results"]),
              "best_baseline": baselines["best_baseline"],
              "reference_gain": baselines["reference"]["gain_vs_best_baseline"],
              "inference_calls": 0,
              "next_stage_requirements": ["First generated codec and fixed Rust wrapper in Stage 1",
                                          "Candidate-code isolation and final evaluator in Stage 2",
                                          "Verify provider spending cap before enabling paid research"],
              "artifacts": {str(path.relative_to(root)): sha256(path)
                            for path in [report_dir / "preflight.json", report_dir / "profile-train.json",
                                         report_dir / "baselines-train.json", report_dir / "report.md",
                                         root / "repro/environment.json"]}}
    write_json(report_dir / "summary.json", result)
    return result
