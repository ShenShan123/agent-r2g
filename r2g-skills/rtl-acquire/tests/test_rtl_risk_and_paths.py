"""Screening risk flags (not hard rejects) + CWD-proof candidate paths + retry.

Covers the 2026-07-10 robustness fixes:
  * common/rtl_risk.py — tokenized, comment-stripped RAM/macro matching; the
    old whole-text substring reject threw picorv32 away because the formal-only
    RISCV_FORMAL_BLACKBOX_* macro names contain "blackbox".
  * discover_download_candidates.file_is_candidate — RAM keywords no longer
    reject; they ride the candidate notes as risk_flags.
  * classify_failed_candidates.classify — same tokenizer on failure evidence,
    memory tokens only.
  * expand_candidates path normalization — ~/$VAR expansion + relative paths
    bound to the CSV dir / repo root, never the caller's CWD.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(_SCRIPTS))

from common.rtl_risk import ram_macro_risk_tokens, strip_hdl_comments  # noqa: E402


def _load(rel: str, name: str):
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


discover = _load("acquire/discover_download_candidates.py", "discover_dl")
classify_mod = _load("repair/classify_failed_candidates.py", "classify_failed")
expand = _load("execute/expand_candidates.py", "expand_cand")

PICORV32_LIKE = "\n".join(
    ["`ifdef RISCV_FORMAL_BLACKBOX_ALU",
     "`define FORMAL_BLACKBOX",
     "`endif",
     "module picorv32 (input clk, input resetn, output reg trap);"]
    + [f"  reg [31:0] r{i};  // pipeline state" for i in range(20)]
    + ["  always @(posedge clk) trap <= !resetn;", "endmodule", ""])


class RiskTokenTests(unittest.TestCase):
    def test_picorv32_formal_macros_flag_but_dont_dominate(self) -> None:
        self.assertEqual(ram_macro_risk_tokens(PICORV32_LIKE), ["blackbox"])

    def test_comment_mentions_do_not_flag(self) -> None:
        text = "// interfaces an external SRAM chip\n/* dual_port_ram notes */\nmodule m; endmodule"
        self.assertEqual(ram_macro_risk_tokens(text), [])

    def test_real_macro_instance_flags(self) -> None:
        self.assertEqual(ram_macro_risk_tokens("sky130_sram_2kbyte u0 (.clk(clk));"),
                         ["sram"])

    def test_multiword_token_within_identifier(self) -> None:
        self.assertEqual(ram_macro_risk_tokens("my_single_port_ram_wrapper u1();"),
                         ["single_port_ram"])

    def test_substring_without_token_boundary_does_not_flag(self) -> None:
        # "transram" contains "sram" as a raw substring — the old bug class.
        self.assertEqual(ram_macro_risk_tokens("module transram_x; endmodule"), [])

    def test_token_subset_param(self) -> None:
        text = "blackbox sram"
        self.assertEqual(ram_macro_risk_tokens(text, tokens=("sram",)), ["sram"])

    def test_vhdl_comment_stripping(self) -> None:
        self.assertEqual(strip_hdl_comments("signal a; -- sram here\n").strip(),
                         "signal a;")


class ScreeningTests(unittest.TestCase):
    def _check(self, text: str) -> tuple[bool, str]:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "core.v"
            p.write_text(text, encoding="utf-8")
            return discover.file_is_candidate(p)

    def test_ram_keywords_no_longer_reject(self) -> None:
        ok, reason = self._check(PICORV32_LIKE)
        self.assertTrue(ok, f"picorv32-like RTL must survive screening (got {reason!r})")

    def test_explicit_sram_rtl_also_survives_as_risk_flagged(self) -> None:
        body = "\n".join(["module wrap (input clk);",
                          "  sky130_sram_2kbyte u0 (.clk(clk));"]
                         + [f"  wire w{i};" for i in range(20)] + ["endmodule"])
        ok, _ = self._check(body)
        self.assertTrue(ok)
        self.assertEqual(ram_macro_risk_tokens(body), ["sram"])

    def test_other_rejects_still_hold(self) -> None:
        ok, reason = self._check("module tiny; endmodule\n")
        self.assertFalse(ok)
        self.assertEqual(reason, "too_small")


class ClassifyTests(unittest.TestCase):
    def test_memory_failure_evidence_still_excludes(self) -> None:
        bucket, reason = classify_mod.classify(
            "/x/a.v", "ERROR: module `sky130_sram_macro' not found")
        self.assertEqual((bucket, reason), ("exclude", "ram_or_macro_dependency"))

    def test_benign_blackbox_diagnostic_is_not_a_ram_dependency(self) -> None:
        bucket, reason = classify_mod.classify(
            "/x/a.v", "Warning: marking module as blackbox; syntax error later")
        self.assertNotEqual(reason, "ram_or_macro_dependency")


class PathNormalizationTests(unittest.TestCase):
    def test_absolute_path_untouched(self) -> None:
        p = expand._normalize_candidate_path("/abs/x.v", [Path("/base")])
        self.assertEqual(p, Path("/abs/x.v"))

    def test_home_and_env_expansion(self) -> None:
        os.environ["R2G_TEST_ROOT"] = "/proj/somewhere"
        try:
            p = expand._normalize_candidate_path("$R2G_TEST_ROOT/a.v", [])
            self.assertEqual(p, Path("/proj/somewhere/a.v"))
            q = expand._normalize_candidate_path("~/b.v", [])
            self.assertTrue(q.is_absolute() and "~" not in str(q))
        finally:
            del os.environ["R2G_TEST_ROOT"]

    def test_relative_binds_to_first_existing_base(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            csv_dir = Path(tmp) / "csvdir"
            repo = Path(tmp) / "repo"
            (repo / "rtl").mkdir(parents=True)
            csv_dir.mkdir()
            (repo / "rtl" / "x.v").write_text("module x; endmodule", encoding="utf-8")
            p = expand._normalize_candidate_path("rtl/x.v", [csv_dir, repo])
            self.assertEqual(p, repo / "rtl" / "x.v")

    def test_relative_defaults_to_csv_dir_when_nowhere_exists(self) -> None:
        p = expand._normalize_candidate_path("rtl/x.v", [Path("/csvdir"), Path("/repo")])
        self.assertEqual(p, Path("/csvdir/rtl/x.v"))  # deterministic, CWD-free

    def test_parse_source_paths_dedups_after_normalization(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "a.v").write_text("module a; endmodule", encoding="utf-8")
            cand = {"source_path": str(base / "a.v"), "rtl_files": "a.v;b.v"}
            paths = expand.parse_candidate_source_paths(cand, [base])
            self.assertEqual(paths, [base / "a.v", base / "b.v"])


class RetryFlagTests(unittest.TestCase):
    def test_expand_has_force_flag(self) -> None:
        src = (_SCRIPTS / "execute" / "expand_candidates.py").read_text(encoding="utf-8")
        self.assertIn('"--force"', src)
        self.assertIn("args.force", src)

    def test_discovery_has_retry_excluded_flag(self) -> None:
        src = (_SCRIPTS / "acquire" / "discover_download_candidates.py").read_text(
            encoding="utf-8")
        self.assertIn('"--retry-excluded"', src)
        self.assertIn("args.retry_excluded", src)


if __name__ == "__main__":
    unittest.main()
