"""Pure-logic tests for .claude/skills/context-purge/gate.py.

Covers the skip-unchanged ledger's mechanical core — block parse/render
round-trip and the hash diff — without touching gh or the filesystem surface.

Run: E:/automation/fleet-config/.venv/Scripts/python.exe tests/test_context_purge_gate.py
Exit 0 = all pass.
"""

from __future__ import annotations

import datetime as _dt
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

# ---- PR reconciliation (fleet-config#757) ----------------------------------

check(gate.is_purge_branch("chore/context-purge-20260822"),
      "is_purge_branch: matches the branch /context-purge actually cuts")
check(not gate.is_purge_branch("chore/other-thing"),
      "is_purge_branch: unrelated chore branches don't match")
check(not gate.is_purge_branch(""), "is_purge_branch: empty head ref is not a match")

check(gate.classify_pr({"state": "MERGED", "mergedAt": "2026-08-01T00:00:00Z"}) == "merged",
      "classify_pr: MERGED state is merged")
check(gate.classify_pr({"state": "OPEN", "mergedAt": "2026-08-01T00:00:00Z"}) == "merged",
      "classify_pr: a non-null mergedAt is merged regardless of state (belt and suspenders)")
check(gate.classify_pr({"state": "CLOSED", "mergedAt": None}) == "closed",
      "classify_pr: CLOSED with no mergedAt is closed-unmerged -- the abandoned case")
check(gate.classify_pr({"state": "OPEN", "mergedAt": None}) == "open",
      "classify_pr: OPEN with no mergedAt is still in flight")

_today = _dt.date(2026, 9, 5)
check(gate.pr_age_days("2026-08-22T06:10:51Z", _today) == 14,
      "pr_age_days: whole days since an ISO-8601 UTC createdAt")
check(gate.pr_age_days("", _today) is None, "pr_age_days: empty createdAt is unknown, not 0")
check(gate.pr_age_days("not-a-date", _today) is None,
      "pr_age_days: unparseable createdAt is unknown, never a crash or a silent 0")

# plan_reconcile: the actual bug (fleet-config#757) -- a closed-unmerged PR's
# ledger entry must be dropped so the file re-enters to_purge, while a merged
# PR's entry is refreshed to what main now holds, and an open PR is left
# alone and reported in the backlog instead.
_ledger = {"automation/CLAUDE.md": "aaaaaaaaaaaa", "life-os/CLAUDE.md": "bbbbbbbbbbbb"}
_prs = [
    {  # the exact shape of the four abandoned 2026-08-22 PRs
        "repo": "automation", "number": 110, "title": "chore: compress",
        "url": "https://github.com/ferraroroberto/automation/pull/110",
        "created_at": "2026-08-22T06:10:51Z", "state": "CLOSED", "mergedAt": None,
        "files": ["CLAUDE.md"],
    },
    {
        "repo": "life-os", "number": 200, "title": "chore: compress",
        "url": "https://github.com/ferraroroberto/life-os/pull/200",
        "created_at": "2026-08-01T00:00:00Z", "state": "MERGED",
        "mergedAt": "2026-08-01T01:00:00Z",
        "files": ["CLAUDE.md"], "main_hashes": {"CLAUDE.md": "ffffffffffff"},
    },
    {
        "repo": "photo-ocr", "number": 5, "title": "chore: compress",
        "url": "https://github.com/ferraroroberto/photo-ocr/pull/5",
        "created_at": "2026-09-01T00:00:00Z", "state": "OPEN", "mergedAt": None,
        "files": ["CLAUDE.md"],
    },
]
plan = gate.plan_reconcile(_ledger, _prs, _dt.date(2026, 9, 5))
check(plan["updates"]["automation/CLAUDE.md"] is None,
      "plan_reconcile: a closed-unmerged PR's file is marked for deletion, not left suppressing")
check(plan["updates"]["life-os/CLAUDE.md"] == "ffffffffffff",
      "plan_reconcile: a merged PR's file is refreshed to its current-on-main hash")
check(len(plan["backlog"]) == 1 and plan["backlog"][0]["repo"] == "photo-ocr",
      "plan_reconcile: an open PR is left untouched and reported in the backlog")
check(plan["backlog"][0]["age_days"] == 4,
      "plan_reconcile: the backlog carries each open PR's age")
check("photo-ocr/CLAUDE.md" not in plan["updates"],
      "plan_reconcile: an open PR's file is not added to updates at all -- neither "
      "confirmed nor deleted, the 'in flight' third state")

# A closed-unmerged PR touching a file the ledger never had an entry for is a
# no-op, not a KeyError -- nothing to clear.
_no_entry_plan = gate.plan_reconcile({}, [_prs[0]], _dt.date(2026, 9, 5))
check(_no_entry_plan["updates"] == {},
      "plan_reconcile: closing a PR for a file with no ledger entry changes nothing")

# A merged PR must always win over a closed one touching the same file,
# regardless of fetch order -- caught live against fleet-config#702 (closed,
# unmerged) vs #754 (merged), both touching CLAUDE.md. Without this guarantee
# a stale abandoned PR could wipe out a hash a later real merge just set,
# purely depending on which PR gh happened to list first.
_superseded_ledger = {"fleet-config/CLAUDE.md": "aaaaaaaaaaaa"}
_closed_then_merged = [
    {"repo": "fleet-config", "number": 702, "title": "old attempt",
     "url": "https://x/702", "created_at": "2026-08-22T06:01:15Z",
     "state": "CLOSED", "mergedAt": None, "files": ["CLAUDE.md"]},
    {"repo": "fleet-config", "number": 754, "title": "landed",
     "url": "https://x/754", "created_at": "2026-09-04T23:48:24Z",
     "state": "MERGED", "mergedAt": "2026-09-05T07:56:20Z",
     "files": ["CLAUDE.md"], "main_hashes": {"CLAUDE.md": "111111111111"}},
]
_plan_closed_first = gate.plan_reconcile(_superseded_ledger, _closed_then_merged, _dt.date(2026, 9, 5))
check(_plan_closed_first["updates"]["fleet-config/CLAUDE.md"] == "111111111111",
      "plan_reconcile: a merged PR's hash wins even when the closed PR for the "
      "same file is processed first")
_plan_merged_first = gate.plan_reconcile(
    _superseded_ledger, list(reversed(_closed_then_merged)), _dt.date(2026, 9, 5))
check(_plan_merged_first["updates"]["fleet-config/CLAUDE.md"] == "111111111111",
      "plan_reconcile: order-independent -- merged wins whichever PR is fetched first")

# Two MERGED PRs touching the same file: the chronologically LATER merge must
# win, not whichever gh happened to list first -- caught live in three sister
# repos, each with an old (2026-08-15) merged purge and a newer (2026-08-22)
# abandoned one for the same CLAUDE.md. Without commit-anchored hashes and
# chronological ordering, an older merge's hash could stomp a newer one's.
_two_merges = [
    {"repo": "whatsapp-radar", "number": 257, "title": "old merge",
     "url": "https://x/257", "created_at": "2026-08-14T00:00:00Z",
     "state": "MERGED", "mergedAt": "2026-08-15T05:41:13Z",
     "files": ["CLAUDE.md"], "main_hashes": {"CLAUDE.md": "aaaaaaaaaaaa"}},
    {"repo": "whatsapp-radar", "number": 300, "title": "newer merge",
     "url": "https://x/300", "created_at": "2026-08-29T00:00:00Z",
     "state": "MERGED", "mergedAt": "2026-08-30T00:00:00Z",
     "files": ["CLAUDE.md"], "main_hashes": {"CLAUDE.md": "bbbbbbbbbbbb"}},
]
_plan_two_merges_a = gate.plan_reconcile({}, _two_merges, _dt.date(2026, 9, 5))
check(_plan_two_merges_a["updates"]["whatsapp-radar/CLAUDE.md"] == "bbbbbbbbbbbb",
      "plan_reconcile: the chronologically later merge wins (in-order fetch)")
_plan_two_merges_b = gate.plan_reconcile({}, list(reversed(_two_merges)), _dt.date(2026, 9, 5))
check(_plan_two_merges_b["updates"]["whatsapp-radar/CLAUDE.md"] == "bbbbbbbbbbbb",
      "plan_reconcile: the chronologically later merge wins even fetched first "
      "(reverse-order fetch) -- proves the sort, not fetch order, decides it")

# `surface` must restrict every file-level update to what gate.py actually
# tracks -- a purge PR's diff can include out-of-surface files (e.g.
# project-scaffolding PR #251 also touched docs/agents/CLAUDE.master.md,
# never a CLAUDE.md/SKILL.md the gate scans), and reconcile must not invent
# a ledger entry `advance --only` would have refused outright.
_out_of_surface_prs = [
    {"repo": "project-scaffolding", "number": 251, "title": "purge",
     "url": "https://x/251", "created_at": "2026-09-04T00:00:00Z",
     "state": "MERGED", "mergedAt": "2026-09-05T00:00:00Z",
     "files": ["CLAUDE.md", "docs/agents/CLAUDE.master.md"],
     "main_hashes": {"CLAUDE.md": "cccccccccccc", "docs/agents/CLAUDE.master.md": "dddddddddddd"}},
]
_surface = {"project-scaffolding/CLAUDE.md"}  # deliberately excludes the master doc
_plan_surface = gate.plan_reconcile({}, _out_of_surface_prs, _dt.date(2026, 9, 5), surface=_surface)
check(_plan_surface["updates"] == {"project-scaffolding/CLAUDE.md": "cccccccccccc"},
      "plan_reconcile: an out-of-surface file in a merged PR's diff is silently "
      "skipped, not turned into a new ledger entry")
_plan_no_surface = gate.plan_reconcile({}, _out_of_surface_prs, _dt.date(2026, 9, 5))
check("project-scaffolding/docs/agents/CLAUDE.master.md" in _plan_no_surface["updates"],
      "plan_reconcile: with no surface given (surface=None), nothing is filtered "
      "-- the restriction is opt-in for callers that know the current surface")

_h.report_and_exit("test_context_purge_gate")
