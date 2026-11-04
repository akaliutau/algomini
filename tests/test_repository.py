import importlib
import json
from pathlib import Path
import re
import subprocess
import sys

import yaml

from algomini import labtools, memory


ROOT = Path(__file__).parents[1]


def test_committed_skills_have_valid_routing_and_available_tools():
    tools = {row["id"] for row in labtools.catalog()}
    for path in (ROOT / ".agents/skills").glob("*/SKILL.md"):
        text = path.read_text()
        assert text.startswith("---\n"), path
        frontmatter = yaml.safe_load(text.split("---", 2)[1])
        assert frontmatter["name"] == path.parent.name
        assert isinstance(frontmatter["description"], str) and frontmatter["description"].strip()
        metadata = json.loads(path.with_name("metadata.json").read_text())
        assert metadata["families"] and set(metadata["families"]) <= memory.FAMILIES | {"*"}
        assert set(metadata["tools"]) <= tools


def test_current_documentation_links_resolve():
    paths = [ROOT / "README.md", ROOT / "repro/README.md", ROOT / "crates/algomini-cli/README.md", ROOT / "datasets/sample/README.md"]
    paths += list((ROOT / "docs").glob("*.md"))
    for path in paths:
        for target in re.findall(r"\[[^\]]*\]\(([^\s)]+)\)", path.read_text()):
            if "://" in target or target.startswith(("#", "mailto:")):
                continue
            local = target.split("#", 1)[0]
            assert not local or (path.parent / local).exists(), (path, target)


def test_public_modules_import_without_optional_scientific_packages():
    script = '''import importlib,pkgutil,algomini
for item in pkgutil.walk_packages(algomini.__path__, algomini.__name__ + "."):
    if not item.name.rsplit(".",1)[-1].startswith("_"):
        importlib.import_module(item.name)
'''
    subprocess.run([sys.executable, "-S", "-c", script], cwd=ROOT, check=True, capture_output=True)
