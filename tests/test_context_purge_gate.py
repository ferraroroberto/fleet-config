"""Pure-logic tests for .claude/skills/context-purge/gate.py.

Covers the skip-unchanged ledger's mechanical core — block parse/render
round-trip and the hash diff — without touching gh or the filesystem surface.

Run: C:/Users/rober/AppData/Local/Python/bin/python.exe tests/test_context_purge_gate.py
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

failures = 0


def ok(case: str, cond: bool) -> None:
    global failures
    print(f"{'OK   ' if cond else 'FAIL '} {case}")
    if not cond:
        failures += 1


HASHES = {
    "fleet-config/global-CLAUDE.md": "a1b2c3d4e5f6",
    "life-os/CLAUDE.md": "0123456789ab",
}

# ---- render / parse round-trip ----
block = gate.render_ledger_block(HASHES, "2026-07-07")
ok("render: marker first line", block.startswith(gate.BLOCK_MARKER))
ok("render: carries last-run-at", "last-run-at: 2026-07-07" in block)
parsed = gate.parse_ledger_block(f"prose above\n\n{block}")
ok("parse: round-trips every entry", parsed == HASHES)
ok("parse: no block -> empty dict", gate.parse_ledger_block("no marker here") == {})
ok("parse: last-run-at line is not an entry", "last-run-at" not in parsed)

# ---- diff ----
current = dict(HASHES)
to_purge, unchanged = gate.diff_ledger(current, HASHES)
ok("diff: identical -> nothing to purge", to_purge == [] and len(unchanged) == 2)

current["life-os/CLAUDE.md"] = "ffffffffffff"  # modified
current["photo-ocr/CLAUDE.md"] = "eeeeeeeeeeee"  # never assessed
to_purge, unchanged = gate.diff_ledger(current, HASHES)
ok("diff: modified file re-enters to_purge", "life-os/CLAUDE.md" in to_purge)
ok("diff: never-assessed file is to_purge", "photo-ocr/CLAUDE.md" in to_purge)
ok("diff: untouched file stays unchanged", unchanged == ["fleet-config/global-CLAUDE.md"])

# ---- hash shape ----
ok("file_hash: 12 lowercase hex chars", len(gate.file_hash(b"x")) == 12
   and all(c in "0123456789abcdef" for c in gate.file_hash(b"x")))
ok("file_hash: content-sensitive", gate.file_hash(b"a") != gate.file_hash(b"b"))

print()
print(f"test_context_purge_gate: {'ALL PASS' if failures == 0 else f'{failures} FAILURE(S)'}")
raise SystemExit(0 if failures == 0 else 1)
