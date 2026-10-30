"""Pure-logic tests for .claude/skills/context-purge/history.py.

The history pass may delete markdown only when the text is proven to live on
an issue, so the detection, the comment round-trip and the removed-history
verdict are tested here without gh (comment fetches are injected).

Run: E:/automation/fleet-config/.venv/Scripts/python.exe tests/audit/test_context_purge_history.py
Exit 0 = all pass.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
_spec = importlib.util.spec_from_file_location(
    "context_purge_history", REPO / ".claude" / "skills" / "context-purge" / "history.py"
)
hp = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = hp  # dataclasses resolve annotations through sys.modules
_spec.loader.exec_module(hp)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check
DEF = "owner/fleet-config"

# ---- references ----
check(hp.refs("see #12 and app-launcher#3, owner/x#4", DEF)
      == {"owner/fleet-config#12", "owner/app-launcher#3", "owner/x#4"}, "bare, repo and owner/repo refs resolve")
check(hp.refs("colour &#123; and url/#5 and abc#def", DEF) == set(), "entities, anchors and non-numeric refs ignored")

# ---- scan ----
TEXT = (
    "# Title\n\nKeep this rule. Measured on the 2026-09-12 round: 17 to 19.\n\n"
    "The lock was stranded for days (#667). Use `run_git` (#677).\n\n"
    "```\nrun 2026-01-01\n```\n"
)
found = hp.scan_text("doc.md", TEXT, DEF)
check([(c.kind, c.line) for c in found] == [("dated", 3), ("narrative", 5)],
      f"scan finds the dated and the narrative sentence, skips pointers and fences (got {found})")
check(found[1].refs == ["owner/fleet-config#667"], "narrative candidate carries its cited issue")

# ---- comment round-trip ----
span = "Measured on the 2026-09-12 round: 17 to 19."
body = hp.comment_body("docs/x.md", span)
quotes = hp.relocated_quotes([body, "> an unmarked quote\n"])
check(quotes == [span], "only marked comments count, quote recovered verbatim")
check(hp.is_relocated(span, quotes), "a relocated span is recognised")
check(not hp.is_relocated("Something else entirely.", quotes), "an absent span is not")
multi = hp.relocated_quotes([hp.comment_body("a.md", "line one\nline two")])
check(multi == ["line one line two"], "a multi-line span round-trips whitespace-normalised")

# ---- removed history + verify ----
BEFORE = "Rule one. Measured on the 2026-09-12 round: 17 to 19.\nThe lock was stranded (#667). Use `run_git` (#677).\n"
AFTER_POINTER = "Rule one. See #900.\nThe lock was stranded (#667). Use `run_git` (#677).\n"
removed = hp.removed_history({"doc.md": (BEFORE, AFTER_POINTER)}, DEF)
check([r.sentence for r in removed] == [span], f"only the dated sentence is removed history (got {removed})")
check("owner/fleet-config#900" in removed[0].candidates, "the pointer left in its place is a candidate issue")

after_refs = hp.refs(AFTER_POINTER, DEF)
ok, missing = hp.verify_removed(removed, DEF, after_refs, lambda k: [span] if k.endswith("#900") else [])
check(len(ok) == 1 and not missing, "history relocated to the pointed-at issue passes")
ok, missing = hp.verify_removed(removed, DEF, after_refs, lambda k: [])
check(len(missing) == 1, "history with no receiving comment fails")

dropped_ref = "The lock rule. Use `run_git`.\n"
rem = hp.removed_history({"doc.md": ("The lock was stranded (#667). Use `run_git`.\n", dropped_ref)}, DEF)
check(len(rem) == 1 and rem[0].candidates == {"owner/fleet-config#667"},
      "dropping a narrative sentence whose reference vanished is removed history")

reworded = hp.removed_history({"doc.md": ("Old wording here (#677).\n", "New wording (#677).\n")}, DEF)
check(reworded == [], "a reworded sentence that keeps its pointer is not history")

moved = hp.removed_history({"a.md": ("Text from 2026-09-12 here.\n", None), "b.md": ("", "Text from 2026-09-12 here.\n")}, DEF)
check(moved == [], "a sentence moved to another changed file is kept, not removed")

clause_before = "Pick the helper (on 2026-08-16 the primary sat behind) every time.\n"
clause_after = "Pick the helper every time (#5).\n"
rem = hp.removed_history({"d.md": (clause_before, clause_after)}, DEF)
clause = "(on 2026-08-16 the primary sat behind)"
ok, missing = hp.verify_removed(rem, DEF, hp.refs(clause_after, DEF), lambda k: [clause])
check(len(ok) == 1 and not missing, "relocating only the dated clause of a sentence passes")

item = hp.removed_history({"n.md": ('  - 2026-10-02 (#9, "x"): superseded. Pages moved. Keep this rule.\n',
                                      "  - Keep this rule (#9).\n")}, DEF)
span_quote = hp.norm('2026-10-02 (#9, "x"): superseded. Pages moved.')
ok, missing = hp.verify_removed(item, DEF, {"owner/fleet-config#9"}, lambda k: [span_quote])
check(len(item) == 1 and len(ok) == 1 and not missing,
      "a multi-sentence span relocated without its list bullet still covers the removed sentence")

_h.report_and_exit("test_context_purge_history")
