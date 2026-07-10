"""Shared RAM/hard-macro risk detection for RTL candidates.

History (failure_knowledge_base.md "ram_or_macro_keyword"): screening used a
raw SUBSTRING scan of the whole file text against a denylist and HARD-REJECTED
on any hit. That rejected pure synthesizable RTL on incidental collisions —
the landmark false positive is picorv32, thrown out because the formal-only
`RISCV_FORMAL_BLACKBOX_*` ifdef macro names contain "blackbox". The same
substring test was duplicated on the repair side (classify_failed_candidates).

Now the signal is a RISK MARKER, not a reject: discovery keeps the candidate,
records the matched tokens (candidate CSV `notes` risk_flags=...), and the
synth attempt itself is the arbiter — a candidate that truly depends on a hard
memory macro fails synthesis with evidence, and the repair-side classifier
(which matches these same tokens against the FAILURE notes) excludes it then.

Matching is tokenized, not substring: identifiers are split on `_` and a
token must appear as an exact part (or, for multi-part tokens like
single_port_ram, a consecutive run of parts). Comments are stripped first so
documentation mentioning "SRAM" doesn't flag code that never touches one.
"""
from __future__ import annotations

import re

RAM_MACRO_RISK_TOKENS = (
    "single_port_ram",
    "dual_port_ram",
    "fakeram",
    "sram",
    "hard_mem",
    "blackbox",
)

_LINE_COMMENT_RE = re.compile(r"//.*?$|--.*?$", re.MULTILINE)  # verilog + vhdl
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_WORD_RE = re.compile(r"[a-z0-9_]+")


def strip_hdl_comments(text: str) -> str:
    return _LINE_COMMENT_RE.sub(" ", _BLOCK_COMMENT_RE.sub(" ", text))


def ram_macro_risk_tokens(text: str, *, strip_comments: bool = True,
                          tokens: tuple[str, ...] = RAM_MACRO_RISK_TOKENS) -> list[str]:
    """Sorted risk tokens found as identifier parts in `text`.

    strip_comments=False is for matching against failure NOTES / log tails
    (already plain text, and a path like foo/sram_test.v in an error line IS
    evidence there). `tokens` narrows the set — the repair-side classifier
    matches only the memory tokens, since e.g. "blackbox" legitimately appears
    in yosys diagnostics that have nothing to do with hard macros.
    """
    if strip_comments:
        text = strip_hdl_comments(text)
    found: set[str] = set()
    single = {t for t in tokens if "_" not in t}
    multi = [t.split("_") for t in tokens if "_" in t]
    for word in _WORD_RE.findall(text.lower()):
        parts = [p for p in word.split("_") if p]
        found.update(single.intersection(parts))
        for tparts in multi:
            n = len(tparts)
            if any(parts[i:i + n] == tparts for i in range(len(parts) - n + 1)):
                found.add("_".join(tparts))
    return sorted(found)
