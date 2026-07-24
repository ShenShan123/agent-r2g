"""install_platform_rules.sh: fail-closed strict-platform installation
(RMD2-P1-01, three-platform pilot 2026-07-24).

The installer used to run every platform-rule installer best-effort and convert
failures into hints, so a fresh-host setup could "complete" while requested
strict-signoff collateral was missing — caught only later by the runtime ENV
gate. Now platforms named in R2G_STRICT_PLATFORMS (or `--platforms`) are
FAIL-CLOSED: missing installer, non-zero installer, or a failed post-install
`platform_capability.py --strict` probe fails setup, and the capability verdict
+ collateral digests land in references/install_manifest.json. Unselected
platforms keep the old best-effort behavior.

Harness: hermetic copy of eda-install/scripts (no references/env.local.sh so the
fake ORFS_ROOT wins), the REAL platform_capability.py staged at the sibling-
skill path, stub nangate45 rule installers in R2G_TOOLS_DIR, and a minimal
nangate45 platform dir that genuinely satisfies (or violates) every strict
capability the probe checks.
"""
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

EDA_ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = EDA_ROOT.parent
CAP_PY = SKILLS_ROOT / "signoff-loop" / "scripts" / "flow" / "platform_capability.py"


def _make_exec(path: Path, text: str):
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _mk_nangate45(flow: Path, *, lvs=True, diode_area="0.585"):
    pdir = flow / "platforms" / "nangate45"
    (pdir / "drc").mkdir(parents=True, exist_ok=True)
    (pdir / "lvs").mkdir(exist_ok=True)
    (pdir / "lib").mkdir(exist_ok=True)
    (pdir / "config.mk").write_text(
        "export TECH_LEF = $(PLATFORM_DIR)/tech.lef\n"
        "export SC_LEF = $(PLATFORM_DIR)/cells.lef\n"
        "export RCX_RULES = $(PLATFORM_DIR)/rcx.rules\n"
        "export LIB_FILES = $(PLATFORM_DIR)/lib/typ.lib\n"
        "export KLAYOUT_DRC_FILE = $(PLATFORM_DIR)/drc/FreePDK45.lydrc\n")
    (pdir / "drc" / "FreePDK45.lydrc").write_text("FEOL = true\n")
    if lvs:
        (pdir / "lvs" / "FreePDK45.lylvs").write_text("# lvs deck\n")
    else:
        (pdir / "lvs" / "FreePDK45.lylvs").unlink(missing_ok=True)
    (pdir / "tech.lef").write_text(
        "LAYER metal1\n  ANTENNAAREARATIO 400 ;\nEND metal1\n")
    (pdir / "cells.lef").write_text(
        "MACRO ANTENNA_X1\n  CLASS CORE ANTENNACELL ;\n"
        f"  ANTENNADIFFAREA {diode_area} ;\nEND ANTENNA_X1\n")
    (pdir / "rcx.rules").write_text("# rcx\n")
    (pdir / "lib" / "typ.lib").write_text("library (typ) {}\n")
    return pdir


def _setup(tmp_path, *, stub_rules=("install_nangate45_lvs.sh",
                                    "install_nangate45_drc.sh",
                                    "install_nangate45_antenna.sh"),
           stub_rc=0, lvs=True, diode_area="0.585"):
    # Hermetic skill copy (references/ deliberately absent → env wins).
    skill = tmp_path / "eda-install"
    shutil.copytree(EDA_ROOT / "scripts", skill / "scripts")
    (skill / "references").mkdir()
    # The REAL capability probe at the sibling-skill path the installer uses.
    sib = tmp_path / "signoff-loop" / "scripts" / "flow"
    sib.mkdir(parents=True)
    shutil.copy(CAP_PY, sib / "platform_capability.py")

    orfs = tmp_path / "orfs"
    (orfs / "flow" / "platforms").mkdir(parents=True)
    # _env.sh only accepts an ORFS_ROOT carrying flow/Makefile — without it the
    # fake checkout is rejected and detection walks to the REAL toolchain.
    (orfs / "flow" / "Makefile").write_text("# fake ORFS Makefile\n")
    _mk_nangate45(orfs / "flow", lvs=lvs, diode_area=diode_area)

    tools = tmp_path / "tools"
    tools.mkdir()
    for name in stub_rules:
        _make_exec(tools / name, f"#!/usr/bin/env bash\nexit {stub_rc}\n")
    return skill, orfs, tools


def _run(skill, orfs, tools, *args, env_extra=None):
    env = dict(os.environ,
               ORFS_ROOT=str(orfs),
               FLOW_DIR=str(orfs / "flow"),
               R2G_TOOLS_DIR=str(tools))
    env.pop("R2G_ENV_FILE", None)
    env.pop("R2G_STRICT_PLATFORMS", None)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        ["bash", str(skill / "scripts" / "setup" / "install_platform_rules.sh"), *args],
        capture_output=True, text=True, env=env, timeout=120)


# ---- selected + green environment passes and records the manifest ------------

def test_selected_green_passes_and_writes_manifest(tmp_path):
    skill, orfs, tools = _setup(tmp_path)
    r = _run(skill, orfs, tools, "--platforms", "nangate45")
    assert r.returncode == 0, r.stderr
    doc = json.loads((skill / "references" / "install_manifest.json").read_text())
    assert doc["strict_ready"] is True
    assert doc["strict_platforms"] == ["nangate45"]
    caps = doc["capability"]["platforms"]["nangate45"]
    assert caps["strict_signoff_ready"] is True
    # Collateral digests recorded (deck + lvs at minimum).
    coll = doc["collateral_sha256"]["nangate45"]
    assert any(k.endswith("FreePDK45.lydrc") for k in coll)
    assert any(k.endswith("FreePDK45.lylvs") for k in coll)


# ---- acceptance 1: removing the LVS deck fails a selected installation -------

def test_missing_lvs_deck_fails_selected(tmp_path):
    skill, orfs, tools = _setup(tmp_path, lvs=False)
    r = _run(skill, orfs, tools, "--platforms", "nangate45")
    assert r.returncode != 0, r.stdout + r.stderr
    assert "strict capability check FAILED" in r.stderr
    doc = json.loads((skill / "references" / "install_manifest.json").read_text())
    assert doc["strict_ready"] is False


# ---- acceptance 2: an unusable antenna model fails a selected installation ---

def test_zero_area_diode_fails_selected(tmp_path):
    skill, orfs, tools = _setup(tmp_path, diode_area="0.0")
    r = _run(skill, orfs, tools, "--platforms", "nangate45")
    assert r.returncode != 0
    assert "strict capability check FAILED" in r.stderr


# ---- acceptance 3: repaired collateral passes again, idempotently ------------

def test_repair_then_rerun_is_idempotent(tmp_path):
    skill, orfs, tools = _setup(tmp_path, lvs=False)
    assert _run(skill, orfs, tools, "--platforms", "nangate45").returncode != 0
    _mk_nangate45(orfs / "flow", lvs=True)          # reintroduce the deck
    assert _run(skill, orfs, tools, "--platforms", "nangate45").returncode == 0
    assert _run(skill, orfs, tools, "--platforms", "nangate45").returncode == 0
    doc = json.loads((skill / "references" / "install_manifest.json").read_text())
    assert doc["strict_ready"] is True


# ---- acceptance 4: a legacy sky130hs .lyt fails a selected installation ------

def test_legacy_sky130hs_lyt_fails_selected(tmp_path):
    skill, orfs, tools = _setup(tmp_path)
    hs = orfs / "flow" / "platforms" / "sky130hs"
    hs.mkdir(parents=True)
    (hs / "config.mk").write_text("# sky130hs\n")
    (hs / "sky130hs.lyt").write_text("<layout><lefdef-legacy-options/></layout>\n")
    r = _run(skill, orfs, tools, "--platforms", "sky130hs")
    assert r.returncode != 0
    assert "strict capability check FAILED" in r.stderr


# ---- missing / failing installers are fatal ONLY when selected ---------------

def test_missing_installer_fails_selected(tmp_path):
    skill, orfs, tools = _setup(
        tmp_path, stub_rules=("install_nangate45_lvs.sh",))  # 2 of 3 missing
    r = _run(skill, orfs, tools, "--platforms", "nangate45")
    assert r.returncode != 0
    assert "install_nangate45_drc.sh" in r.stderr or "not found/executed" in r.stderr


def test_no_installers_at_all_fails_selected(tmp_path):
    skill, orfs, tools = _setup(tmp_path, stub_rules=())
    r = _run(skill, orfs, tools, "--platforms", "nangate45")
    assert r.returncode != 0
    assert "fail closed" in r.stderr


def test_failing_installer_fails_selected(tmp_path):
    skill, orfs, tools = _setup(tmp_path, stub_rc=1)
    r = _run(skill, orfs, tools, "--platforms", "nangate45")
    assert r.returncode != 0
    assert "returned non-zero" in r.stderr


def test_unselected_stays_best_effort(tmp_path):
    """No strict selection → the pre-RMD2 behavior: hints, exit 0."""
    skill, orfs, tools = _setup(tmp_path, stub_rules=(), stub_rc=0)
    r = _run(skill, orfs, tools)
    assert r.returncode == 0, r.stderr
    assert "HINT" in r.stderr
    assert not (skill / "references" / "install_manifest.json").exists()


def test_env_selection_equivalent_to_flag(tmp_path):
    skill, orfs, tools = _setup(tmp_path, lvs=False)
    r = _run(skill, orfs, tools, env_extra={"R2G_STRICT_PLATFORMS": "nangate45"})
    assert r.returncode != 0
    assert "strict capability check FAILED" in r.stderr


def test_dry_run_installs_nothing_and_never_fails(tmp_path):
    skill, orfs, tools = _setup(tmp_path, lvs=False)
    r = _run(skill, orfs, tools, "--dry-run", "--platforms", "nangate45")
    assert r.returncode == 0, r.stderr
    assert not (skill / "references" / "install_manifest.json").exists()
