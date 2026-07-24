"""Pure-logic tests for .claude/skills/context-purge/check.py.

Standalone (like test_audit_issue / test_ux_surface) so the purge's mechanical
preservation rules — marked-block byte-identity and quoted-trigger survival —
are testable on their own and reachable from the one acceptance gate.

Run: E:/automation/fleet-config/.venv/Scripts/python.exe tests/test_context_purge_check.py
Exit 0 = all pass.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "context_purge_check", REPO / ".claude" / "skills" / "context-purge" / "check.py"
)
cp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cp)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

failures = 0


def ok(case: str, cond: bool) -> None:
    global failures
    print(f"{'OK   ' if cond else 'FAIL '} {case}")
    if not cond:
        failures += 1


MERMAID = (
    "<!-- system-map:mermaid:start -->\n### Fleet map\n```mermaid\nflowchart LR\n```\n"
    "<!-- system-map:mermaid:end -->"
)
BEFORE_MD = f"# Global\n\nLots of prose here.\n\n{MERMAID}\n\nMore prose.\n"

# ---- marked blocks ----
ok("marked block preserved byte-identical -> pass",
   cp.check(BEFORE_MD, f"# Global\n\n{MERMAID}\n") == [])
ok("marked block edited -> fail",
   any("marked block" in f for f in cp.check(BEFORE_MD, BEFORE_MD.replace("flowchart LR", "flowchart TD"))))
ok("marked block dropped -> fail",
   any("marked block" in f for f in cp.check(BEFORE_MD, "# Global\n\nshort.\n")))
ok("no marked blocks in before -> nothing to check",
   cp.check("# plain\ntext\n", "# plain\n") == [])

# ---- quoted triggers in frontmatter description ----
SKILL_BEFORE = (
    '---\nname: foo\ndescription: Does a thing. Use when asked, e.g. "/foo", "run foo now".\n---\n\n# foo\nBody prose.\n'
)
SKILL_AFTER_OK = (
    '---\nname: foo\ndescription: Does a thing — e.g. "/foo", "run foo now".\n---\n\n# foo\n'
)
SKILL_AFTER_LOST = (
    '---\nname: foo\ndescription: Does a thing — e.g. "/foo".\n---\n\n# foo\n'
)
SKILL_AFTER_NO_DESC = "---\nname: foo\n---\n\n# foo\n"

ok("all quoted triggers survive (prose reworded) -> pass",
   cp.check(SKILL_BEFORE, SKILL_AFTER_OK) == [])
ok("a quoted trigger lost -> fail",
   any("trigger phrase" in f for f in cp.check(SKILL_BEFORE, SKILL_AFTER_LOST)))
ok("description dropped entirely -> fail",
   any("dropped entirely" in f for f in cp.check(SKILL_BEFORE, SKILL_AFTER_NO_DESC)))
ok("plain file without frontmatter -> description rule not applied",
   cp.check("no frontmatter here\n", "still none\n") == [])

# Apostrophes are not quoting. Rewording prose around a possessive must not read
# as a lost trigger, as long as every double-quoted phrase survives.
SKILL_APOSTROPHE_BEFORE = (
    '---\nname: foo\ndescription: Capture each box\'s envelope against last week\'s entry, e.g. "/foo".\n---\n\n# foo\n'
)
SKILL_APOSTROPHE_AFTER = (
    '---\nname: foo\ndescription: Capture the machine\'s envelope, diffed weekly, e.g. "/foo".\n---\n\n# foo\n'
)
ok("possessives reworded, double-quoted triggers intact -> pass",
   cp.check(SKILL_APOSTROPHE_BEFORE, SKILL_APOSTROPHE_AFTER) == [])
ok("possessives reworded but a real trigger lost -> still fails",
   any("trigger phrase" in f for f in cp.check(
       SKILL_APOSTROPHE_BEFORE,
       '---\nname: foo\ndescription: Capture the machine\'s envelope weekly.\n---\n\n# foo\n')))

# ---- token estimate ----
ok("est_tokens ~ chars/4", cp.est_tokens("x" * 400) == 100)

print()
print(f"test_context_purge_check: {'ALL PASS' if failures == 0 else f'{failures} FAILURE(S)'}")
raise SystemExit(0 if failures == 0 else 1)
