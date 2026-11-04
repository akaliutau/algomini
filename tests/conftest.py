"""Retired codecs belong to regression fixtures, never the production catalog."""

import shutil
from pathlib import Path
import json

import pytest


ROOT = Path(__file__).parents[1]
FIXTURES = ROOT / "tests/fixtures"


def library(tmp_path_factory, name):
    library = tmp_path_factory.mktemp(name)
    for name in ("algomini", "crates", "rust", "cloud", ".agents", "laboratory"):
        shutil.copytree(ROOT / name, library / name, ignore=shutil.ignore_patterns("target", "__pycache__"))
    shutil.copyfile(ROOT / "run.toml", library / "run.toml")
    return library


@pytest.fixture(scope="session")
def codec_library(tmp_path_factory):
    result = library(tmp_path_factory, "reviewed-codec-library")
    shutil.copytree(FIXTURES / "plugins", result / "plugins")
    shutil.copytree(FIXTURES / "release", result / "release")
    return result


@pytest.fixture(scope="session")
def empty_library(tmp_path_factory):
    result = library(tmp_path_factory, "empty-codec-library")
    (result / "release").mkdir()
    (result / "release/registry.json").write_text(json.dumps({"schema_version": 1, "aliases": {}, "codecs": [], "requests": []}))
    return result
