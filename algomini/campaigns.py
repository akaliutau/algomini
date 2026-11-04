"""Explicit campaign workspaces and a reusable, credential-free preparation API."""

from dataclasses import dataclass
import json
from pathlib import Path
import re

from . import labtools, memory
from .common import digest, sha256, write_json


@dataclass(frozen=True)
class Campaign:
    workspace: Path
    library: Path

    @classmethod
    def create(cls, workspace, library, sample, *, campaign_id, family, probe_bytes=labtools.MAX_INPUT):
        workspace, library, sample = Path(workspace).resolve(), Path(library).resolve(), Path(sample)
        if (not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", campaign_id)
                or family not in memory.FAMILIES
                or not 0 < probe_bytes <= labtools.MAX_INPUT):
            raise ValueError("Invalid campaign identity, family or probe bound")
        if workspace == library or library.is_relative_to(workspace):
            raise ValueError("Campaign workspace cannot contain the shared library")
        with sample.open("rb") as stream:
            data = stream.read(labtools.MAX_INPUT + 1)
        if len(data) > labtools.MAX_INPUT:
            raise ValueError("Campaign sample exceeds 2 MiB; supply a bounded extract")
        manifest = {"schema_version": 1, "id": campaign_id, "family": family, "sample_name": sample.name,
                    "input_bytes": len(data), "input_sha256": digest(data), "probe_bytes": probe_bytes,
                    "split": "train", "scope": "Research/integration sample; not unseen acceptance evidence"}
        path = workspace / "campaign.json"
        if path.exists():
            if json.loads(path.read_text()) != manifest:
                raise ValueError("Campaign already exists with a different input or policy; use another workspace")
        else:
            workspace.mkdir(parents=True, exist_ok=True)
            if any(workspace.iterdir()):
                raise ValueError("New campaign requires an empty workspace")
            (workspace / "sample.bin").write_bytes(data)
            write_json(path, manifest)
        campaign = cls(workspace, library)
        campaign.manifest()
        return campaign

    def manifest(self):
        manifest = json.loads((self.workspace / "campaign.json").read_text())
        if (manifest.get("schema_version") != 1 or manifest.get("family") not in memory.FAMILIES
                or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", manifest.get("id", ""))
                or not 0 <= manifest["input_bytes"] <= labtools.MAX_INPUT
                or not 0 < manifest["probe_bytes"] <= labtools.MAX_INPUT):
            raise ValueError("Invalid campaign manifest")
        sample = self.workspace / "sample.bin"
        if sample.is_symlink() or sample.stat().st_size != manifest["input_bytes"] or sha256(sample) != manifest["input_sha256"]:
            raise ValueError("Campaign sample changed")
        return manifest

    def prepare(self, *, embedder=None):
        manifest = self.manifest()
        output = self.workspace / "reports/preparation.json"
        terminal = output.with_suffix(".sha256")
        if output.exists() or terminal.exists():
            if not output.is_file() or not terminal.is_file() or sha256(output) != terminal.read_text().strip():
                raise ValueError("Campaign preparation cache checksum mismatch")
            result = json.loads(output.read_text())
            if result["campaign"] != manifest:
                raise ValueError("Campaign preparation belongs to another policy/input")
            return result
        with (self.workspace / "sample.bin").open("rb") as stream:
            data = stream.read(manifest["probe_bytes"])
        tools = [tool for tool in labtools.catalog() if "*" in tool["families"] or manifest["family"] in tool["families"]]
        probes = [labtools.run(tool["id"], data, family=manifest["family"]) for tool in tools]
        hints = [row["facts"]["hypothesis"] for row in probes if "hypothesis" in row["facts"]]
        if manifest["family"] == "csv":
            hints.append("column layouts exact numeric spellings quoted tokens")
        query = manifest["family"] + " " + " ".join(hints or ["byte structure repetition"])
        retrieved = memory.search(self.library, query, family=manifest["family"], cache_dir=self.workspace / ".cache/memory", embedder=embedder)
        result = {"schema_version": 1, "campaign": manifest, "probes": probes, "research_query": query, "retrieved": retrieved,
                  "retrieved_characters": sum(len(row["excerpt"]) for row in retrieved),
                  "paid_model_calls": 0, "coder_model_calls": 0, "docker_used": False,
                  "reproduce": ["python", "-m", "algomini", "campaign", "prepare", str(self.workspace)]}
        write_json(output, result)
        terminal.write_text(sha256(output) + "\n")
        memory.append(self.workspace, {"id": manifest["id"] + "/preparation", "tags": [manifest["family"], "laboratory"],
                      "split": "train", "input_sha256": manifest["input_sha256"], "outcome": "profiled",
                      "probe_ids": [row["probe_id"] for row in probes], "evidence": "preparation.json"}, run_id="preparation")
        return result

    def export(self, destination):
        """Export reviewable evidence, never the sample or credentials, into a knowledge repository."""
        result = self.prepare()
        destination = Path(destination) / "reports/campaigns" / result["campaign"]["id"]
        destination.mkdir(parents=True, exist_ok=True)
        for source, name in ((self.workspace / "reports/preparation.json", "preparation.json"),
                             (self.workspace / "reports/preparation/observations.jsonl", "observations.jsonl")):
            target = destination / name
            if target.exists() and sha256(target) != sha256(source):
                raise ValueError("Export would replace existing campaign evidence")
            target.write_bytes(source.read_bytes())
        return {"ok": True, "campaign_id": result["campaign"]["id"], "destination": str(destination)}
