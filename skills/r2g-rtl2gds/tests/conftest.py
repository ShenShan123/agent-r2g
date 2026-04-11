"""Shared pytest fixtures for r2g-rtl2gds knowledge-store tests."""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

# Make scripts/ importable as plain modules.
SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = SKILL_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


@pytest.fixture
def tmp_knowledge_dir(tmp_path: Path) -> Path:
    """A throw-away knowledge/ directory with the real schema + families seed."""
    kdir = tmp_path / "knowledge"
    kdir.mkdir()
    shutil.copy(SKILL_ROOT / "knowledge" / "schema.sql", kdir / "schema.sql")
    shutil.copy(SKILL_ROOT / "knowledge" / "families.json", kdir / "families.json")
    return kdir


@pytest.fixture
def fixtures_dir() -> Path:
    return Path(__file__).resolve().parent / "fixtures"
