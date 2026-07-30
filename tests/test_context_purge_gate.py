"""Pure-logic tests for .claude/skills/context-purge/gate.py.

Covers the skip-unchanged ledger's mechanical core — block parse/render
round-trip and the hash diff — without touching gh or the filesystem surface.

Run: E:/automation/fleet-config/.venv/Scripts/python.exe tests/test_context_purge_gate.py
Exit 0 = all pass.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "context_purge_gate", REPO / ".claude" / "skills" / "context-purge" / "gate.py"
)
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check


HASHES = {
    "fleet-config/global-CLAUDE.md": "a1b2c3d4e5f6",
    "life-os/CLAUDE.md": "0123456789ab",
}

# ---- render / parse round-trip ----
block = gate.render_ledger_block(HASHES, "2026-07-07")
check(block.startswith(gate.BLOCK_MARKER), "render: marker first line")
check("last-run-at: 2026-07-07" in block, "render: carries last-run-at")
parsed = gate.parse_ledger_block(f"prose above\n\n{block}")
check(parsed == HASHES, "parse: round-trips every entry")
check(gate.parse_ledger_block("no marker here") == {}, "parse: no block -> empty dict")
check("last-run-at" not in parsed, "parse: last-run-at line is not an entry")

# ---- diff ----
current = dict(HASHES)
to_purge, unchanged = gate.diff_ledger(current, HASHES)
check(to_purge == [] and len(unchanged) == 2, "diff: identical -> nothing to purge")

current["life-os/CLAUDE.md"] = "ffffffffffff"  # modified
current["photo-ocr/CLAUDE.md"] = "eeeeeeeeeeee"  # never assessed
to_purge, unchanged = gate.diff_ledger(current, HASHES)
check("life-os/CLAUDE.md" in to_purge, "diff: modified file re-enters to_purge")
check("photo-ocr/CLAUDE.md" in to_purge, "diff: never-assessed file is to_purge")
check(unchanged == ["fleet-config/global-CLAUDE.md"], "diff: untouched file stays unchanged")

# ---- select_assessed (advance --only) ----
# A fleet run is normally partial, so recording the whole surface would mark
# never-read files as assessed and hide them from every future run.
SURFACE = {"a/CLAUDE.md": "aaaaaaaaaaaa", "b/CLAUDE.md": "bbbbbbbbbbbb",
           "c/CLAUDE.md": "cccccccccccc"}
check(gate.select_assessed(SURFACE, None) == SURFACE, "select: no --only -> whole surface")
check(gate.select_assessed(SURFACE, ["a/CLAUDE.md", "c/CLAUDE.md"])
      == {"a/CLAUDE.md": "aaaaaaaaaaaa", "c/CLAUDE.md": "cccccccccccc"},
      "select: --only narrows to the assessed files")
check("b/CLAUDE.md" not in gate.select_assessed(SURFACE, ["a/CLAUDE.md"]),
      "select: unassessed file is left out (stays in next run's to_purge)")
check(gate.select_assessed(SURFACE, []) == {}, "select: empty --only records nothing")
try:
    gate.select_assessed(SURFACE, ["a/CLAUDE.md", "typo/CLAUDE.md"])
    check(False, "select: unknown key raises")
except KeyError as exc:
    check("typo/CLAUDE.md" in str(exc), "select: unknown key raises, naming it")

# ---- hash shape ----
check(len(gate.file_hash(b"x")) == 12
      and all(c in "0123456789abcdef" for c in gate.file_hash(b"x")),
      "file_hash: 12 lowercase hex chars")
check(gate.file_hash(b"a") != gate.file_hash(b"b"), "file_hash: content-sensitive")

_h.report_and_exit("test_context_purge_gate")
