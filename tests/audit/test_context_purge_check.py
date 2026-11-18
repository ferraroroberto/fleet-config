"""Pure-logic tests for .claude/skills/context-purge/check.py, history.py and gate.py.

Standalone (like test_audit_issue / test_ux_surface) so the purge's mechanical
preservation rules — marked-block byte-identity and quoted-trigger survival —
are testable on their own and reachable from the one acceptance gate.

Run: E:/automation/fleet-config/.venv/Scripts/python.exe tests/audit/test_context_purge_check.py
Exit 0 = all pass.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
_spec = importlib.util.spec_from_file_location(
    "context_purge_check", REPO / ".claude" / "skills" / "context-purge" / "check.py"
)
cp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cp)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check


MERMAID = (
    "<!-- example:generated:start -->\n### Generated map\n```mermaid\nflowchart LR\n```\n"
    "<!-- example:generated:end -->"
)
BEFORE_MD = f"# Global\n\nLots of prose here.\n\n{MERMAID}\n\nMore prose.\n"

# ---- marked blocks ----
check(cp.check(BEFORE_MD, f"# Global\n\n{MERMAID}\n") == [],
      "marked block preserved byte-identical -> pass")
check(any("marked block" in f for f in cp.check(BEFORE_MD, BEFORE_MD.replace("flowchart LR", "flowchart TD"))),
      "marked block edited -> fail")
check(any("marked block" in f for f in cp.check(BEFORE_MD, "# Global\n\nshort.\n")),
      "marked block dropped -> fail")
check(cp.check("# plain\ntext\n", "# plain\n") == [],
      "no marked blocks in before -> nothing to check")

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

check(cp.check(SKILL_BEFORE, SKILL_AFTER_OK) == [],
      "all quoted triggers survive (prose reworded) -> pass")
check(any("trigger phrase" in f for f in cp.check(SKILL_BEFORE, SKILL_AFTER_LOST)),
      "a quoted trigger lost -> fail")
check(any("dropped entirely" in f for f in cp.check(SKILL_BEFORE, SKILL_AFTER_NO_DESC)),
      "description dropped entirely -> fail")
check(cp.check("no frontmatter here\n", "still none\n") == [],
      "plain file without frontmatter -> description rule not applied")

# Apostrophes are not quoting. Rewording prose around a possessive must not read
# as a lost trigger, as long as every double-quoted phrase survives.
SKILL_APOSTROPHE_BEFORE = (
    '---\nname: foo\ndescription: Capture each box\'s envelope against last week\'s entry, e.g. "/foo".\n---\n\n# foo\n'
)
SKILL_APOSTROPHE_AFTER = (
    '---\nname: foo\ndescription: Capture the machine\'s envelope, diffed weekly, e.g. "/foo".\n---\n\n# foo\n'
)
check(cp.check(SKILL_APOSTROPHE_BEFORE, SKILL_APOSTROPHE_AFTER) == [],
      "possessives reworded, double-quoted triggers intact -> pass")
check(any("trigger phrase" in f for f in cp.check(
      SKILL_APOSTROPHE_BEFORE,
      '---\nname: foo\ndescription: Capture the machine\'s envelope weekly.\n---\n\n# foo\n')),
      "possessives reworded but a real trigger lost -> still fails")

# A rewrite that keeps every trigger but breaks the YAML (fleet-config#845).
# The real `/context-purge fleet` output for issue-yolo: an unquoted plain
# scalar holding a colon-space no longer parses, so the live skill listing lost
# the description entirely while this check printed PASS.
SKILL_YOLO_BEFORE = (
    '---\nname: issue-yolo\ndescription: One-shot the GitHub-issue workflow end-to-end — file the issue, '
    'build, ship. E.g. "/issue-yolo 34".\n---\n\n# issue-yolo\n'
)
SKILL_YOLO_AFTER_BROKEN = (
    '---\nname: issue-yolo\ndescription: One-shot the GitHub-issue workflow: file the issue, '
    'build, ship. E.g. "/issue-yolo 34".\n---\n\n# issue-yolo\n'
)
_yolo = cp.check(SKILL_YOLO_BEFORE, SKILL_YOLO_AFTER_BROKEN)
check(any("does not parse" in f for f in _yolo),
      "triggers intact but frontmatter no longer parses -> fail, not PASS")
check(any("': '" in f for f in _yolo),
      "the parse failure names its reason (the colon-space), not just 'broken'")
check(any("does not parse" in f for f in cp.check(
      "# a CLAUDE.md, no frontmatter\n",
      '---\nname: foo\ndescription: Two modes: concise is default\n---\n')),
      "an after-file frontmatter is validated even when before had none")
check(cp.check("# plain\n", "# plain, shorter\n") == [],
      "no frontmatter on either side -> parse rule not applied")

# ---- token estimate ----
check(cp.est_tokens("x" * 400) == 100, "est_tokens ~ chars/4")
check(cp.word_count("one two\nthree  four\n") == 4, "word_count matches wc -w")

# ---- --moved-to: a branch-disclosure split moves text verbatim ----
SPLIT_BEFORE = (
    "# skill\n\n### 1. Always\n\nEvery run does this. Then step 2.\n\n"
    "### 2. Tray\n\nIf a tray runs, restart it:\n- **Prefer the restart.** Run `tray.bat --restart`\n"
    "  and nothing else.\n- Read [notes](notes.md) first.\n\n```\ntray.bat --restart\n```\n"
)
SPLIT_MAIN = (
    "# skill\n\n### 1. Always\n\nEvery run does this. Then step 2.\n\n"
    "### 2. Tray\n\nIf a tray runs, restart it: read [`tray.md`](E:/x/skills/s/tray.md).\n"
)
SPLIT_SIBLING = (
    "# Tray (loaded when a tray runs)\n\n- **Prefer the restart.** Run `tray.bat --restart` and nothing else.\n"
    "- Read [`notes`](E:/x/notes.md) first.\n\n```\ntray.bat --restart\n```\n"
)
check(cp.split_lost(SPLIT_BEFORE, [SPLIT_MAIN, SPLIT_SIBLING]) == [],
      "a verbatim move, reflowed and with a re-pointed link, loses nothing")
_lost = cp.split_lost(SPLIT_BEFORE, [SPLIT_MAIN])
check("- **Prefer the restart.**" in _lost and "tray.bat --restart" in _lost,
      f"the moved prose and fenced line are lost without the sibling (got {_lost})")
check(cp.split_lost(SPLIT_BEFORE, [SPLIT_MAIN, SPLIT_SIBLING.replace("nothing else", "little else")])
      == ["Run `tray.bat --restart` and nothing else."],
      "a reworded sentence in the sibling is reported, alone")
check(cp.split_lost(SPLIT_BEFORE, [SPLIT_MAIN.replace("Every run does this. ", ""), SPLIT_SIBLING])
      == ["Every run does this."], "a sentence dropped from the main file is reported")

# ---- --base: every changed instruction file on a branch (fleet-config#833) ----
check(cp.is_instruction_file("CLAUDE.md") and cp.is_instruction_file("a/.claude/skills/x/SKILL.md")
      and cp.is_instruction_file(".claude/rules/style.md") and cp.is_instruction_file("AGENTS.md")
      and not cp.is_instruction_file("README.md") and not cp.is_instruction_file("docs/CLAUDE-notes.txt"),
      "instruction-file classification matches the /prompt-audit surface")

import contextlib  # noqa: E402
import datetime as _dt  # noqa: E402
import io  # noqa: E402
import json  # noqa: E402
import shutil  # noqa: E402
import tempfile  # noqa: E402
from types import SimpleNamespace  # noqa: E402

sys.path.insert(0, str(REPO / "skills" / "_lib"))
import git_run  # noqa: E402

_tmp = Path(tempfile.mkdtemp(prefix="purge-base-"))
try:
    def _git(*a: str) -> None:
        # An empty hooks dir: the synthetic repo must not run the machine's global commit hooks.
        git_run.run_git(["-C", str(_tmp), "-c", "user.name=t", "-c", "user.email=t@t",
                         "-c", f"core.hooksPath={_tmp / '.nohooks'}", "-c", "commit.gpgsign=false", *a], check=True)

    (_tmp / ".claude" / "skills" / "foo").mkdir(parents=True)
    (_tmp / ".claude" / "skills" / "foo" / "SKILL.md").write_text(SKILL_BEFORE, encoding="utf-8")
    (_tmp / "CLAUDE.md").write_text(BEFORE_MD, encoding="utf-8")
    (_tmp / "README.md").write_text("readme\n", encoding="utf-8")
    _git("init", "-q", "-b", "main")
    _git("add", "-A")
    _git("commit", "-q", "-m", "base")
    _git("checkout", "-q", "-b", "fix/1")

    def _run_base() -> tuple:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cp.main(["--base", "main", "--repo", str(_tmp)])
        return code, out.getvalue()

    code, out = _run_base()
    check(code == 0 and "NO_INSTRUCTION_FILES_CHANGED" in out, f"--base with nothing changed passes and says so (got {out!r})")
    (_tmp / "README.md").write_text("readme edited\n", encoding="utf-8")
    (_tmp / "CLAUDE.md").write_text(f"# Global\n\n{MERMAID}\n", encoding="utf-8")
    code, out = _run_base()
    check(code == 0 and "PASS  CLAUDE.md:" in out and "README" not in out,
          f"--base checks a rewritten instruction file (uncommitted) and ignores other files (got {out!r})")
    # Planted regressions: a dropped quoted trigger, then a changed marked block.
    (_tmp / ".claude" / "skills" / "foo" / "SKILL.md").write_text(SKILL_AFTER_LOST, encoding="utf-8")
    _git("commit", "-qam", "drop a trigger")
    code, out = _run_base()
    check(code == 2 and "FAIL  .claude/skills/foo/SKILL.md: quoted trigger phrase lost" in out,
          f"--base fails a committed edit that dropped a quoted trigger (got {out!r})")
    (_tmp / ".claude" / "skills" / "foo" / "SKILL.md").write_text(SKILL_BEFORE, encoding="utf-8")
    (_tmp / "CLAUDE.md").write_text(BEFORE_MD.replace("flowchart LR", "flowchart TD"), encoding="utf-8")
    code, out = _run_base()
    check(code == 2 and "FAIL  CLAUDE.md: marked block not byte-identical" in out and "CHECKED=1|failed=1" in out,
          f"--base fails a changed marked block (got {out!r})")
    (_tmp / "CLAUDE.md").unlink()
    code, out = _run_base()
    check(code == 2 and "FAIL  CLAUDE.md: deleted" in out, "--base fails a deleted instruction file")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = cp.main(["--base", "no-such-ref", "--repo", str(_tmp)])
    check(code == 3 and out.getvalue().startswith("UNKNOWN"), "an undiffable base is unknown (exit 3), never a pass")

    split_dir = _tmp / "split"
    split_dir.mkdir()
    for name, text in (("before.md", SPLIT_BEFORE), ("main.md", SPLIT_MAIN), ("tray.md", SPLIT_SIBLING)):
        (split_dir / name).write_text(text, encoding="utf-8")

    def _run_split(*siblings: str) -> tuple:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cp.main([str(split_dir / "before.md"), str(split_dir / "main.md"),
                            "--moved-to", *[str(split_dir / s) for s in siblings]])
        return code, out.getvalue()

    code, out = _run_split("tray.md")
    check(code == 0 and "lost=0" in out and "PASS  split:" in out and "WORDS: main_before=" in out,
          f"--moved-to passes a complete split and prints the word counts (got {out!r})")
    (split_dir / "empty.md").write_text("# nothing moved here\n", encoding="utf-8")
    code, out = _run_split("empty.md")
    check(code == 2 and "LOST  tray.bat --restart" in out, f"--moved-to fails a split that lost text (got {out!r})")
finally:
    shutil.rmtree(_tmp, ignore_errors=True)


# ======== history pass: detection, comment round-trip and the removed-history verdict ========

_spec = importlib.util.spec_from_file_location(
    "context_purge_history", REPO / ".claude" / "skills" / "context-purge" / "history.py"
)
hp = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = hp  # dataclasses resolve annotations through sys.modules
_spec.loader.exec_module(hp)

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


# ======== skip-unchanged ledger: parse, render and diff core ========

_spec = importlib.util.spec_from_file_location(
    "context_purge_gate", REPO / ".claude" / "skills" / "context-purge" / "gate.py"
)
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)
HASHES = {
    "fleet-config/global-CLAUDE.md": "a1b2c3d4e5f6",
    "life-os/CLAUDE.md": "0123456789ab",
}
block = gate.render_ledger_block(HASHES, "2026-07-07")
check(block.startswith(gate.BLOCK_MARKER), "render: marker first line")
check("last-run-at: 2026-07-07" in block, "render: carries last-run-at")
parsed = gate.parse_ledger_block(f"prose above\n\n{block}")
check(parsed == HASHES, "parse: round-trips every entry")
check(gate.parse_ledger_block("no marker here") == {}, "parse: no block -> empty dict")
check("last-run-at" not in parsed, "parse: last-run-at line is not an entry")
current = dict(HASHES)
to_purge, unchanged = gate.diff_ledger(current, HASHES)
check(to_purge == [] and len(unchanged) == 2, "diff: identical -> nothing to purge")
current["life-os/CLAUDE.md"] = "ffffffffffff"  # modified
current["photo-ocr/CLAUDE.md"] = "eeeeeeeeeeee"  # never assessed
to_purge, unchanged = gate.diff_ledger(current, HASHES)
check("life-os/CLAUDE.md" in to_purge, "diff: modified file re-enters to_purge")
check("photo-ocr/CLAUDE.md" in to_purge, "diff: never-assessed file is to_purge")
check(unchanged == ["fleet-config/global-CLAUDE.md"], "diff: untouched file stays unchanged")
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
check(len(gate.file_hash(b"x")) == 12
      and all(c in "0123456789abcdef" for c in gate.file_hash(b"x")),
      "file_hash: 12 lowercase hex chars")
check(gate.file_hash(b"a") != gate.file_hash(b"b"), "file_hash: content-sensitive")
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
_ledger = {"automation/CLAUDE.md": "aaaaaaaaaaaa"}
_prs = [
    {  # the exact shape of the four abandoned 2026-08-22 PRs
        "repo": "automation", "number": 110, "title": "chore: compress",
        "url": "https://github.com/ferraroroberto/automation/pull/110",
        "created_at": "2026-08-22T06:10:51Z", "state": "CLOSED", "mergedAt": None,
        "files": ["CLAUDE.md"], "head_hashes": {"CLAUDE.md": "aaaaaaaaaaaa"},
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
      "plan_reconcile: a merged PR's file with no ledger entry is filled with its merge-commit hash")
check(len(plan["backlog"]) == 1 and plan["backlog"][0]["repo"] == "photo-ocr",
      "plan_reconcile: an open PR is left untouched and reported in the backlog")
check(plan["backlog"][0]["age_days"] == 4,
      "plan_reconcile: the backlog carries each open PR's age")
check("photo-ocr/CLAUDE.md" not in plan["updates"],
      "plan_reconcile: an open PR's file is not added to updates at all -- neither "
      "confirmed nor deleted, the 'in flight' third state")
_no_entry_plan = gate.plan_reconcile({}, [_prs[0]], _dt.date(2026, 9, 5))
check(_no_entry_plan["updates"] == {},
      "plan_reconcile: closing a PR for a file with no ledger entry changes nothing")
_superseded_ledger = {"fleet-config/CLAUDE.md": "aaaaaaaaaaaa"}
_closed_then_merged = [
    {"repo": "fleet-config", "number": 702, "title": "old attempt",
     "url": "https://x/702", "created_at": "2026-08-22T06:01:15Z",
     "state": "CLOSED", "mergedAt": None, "files": ["CLAUDE.md"],
     "head_hashes": {"CLAUDE.md": "aaaaaaaaaaaa"}},
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
_advanced = {"fleet-config/global-CLAUDE.md": "6d711df4d368"}
_old_merge = [
    {"repo": "fleet-config", "number": 900, "title": "old purge", "url": "https://x/900",
     "created_at": "2026-10-01T00:00:00Z", "state": "MERGED", "mergedAt": "2026-10-02T00:00:00Z",
     "files": ["global-CLAUDE.md"], "main_hashes": {"global-CLAUDE.md": "f6ae38435b84"}},
]
_plan_adv = gate.plan_reconcile(_advanced, _old_merge, _dt.date(2026, 11, 12))
check(_plan_adv["updates"] == {},
      "plan_reconcile: a file advanced after its purge PR merged keeps its advance hash")
_plan_fill = gate.plan_reconcile({}, _old_merge, _dt.date(2026, 11, 12))
check(_plan_fill["updates"] == {"fleet-config/global-CLAUDE.md": "f6ae38435b84"},
      "plan_reconcile: a merged PR still fills a key the ledger has no entry for")
_closed = [
    {"repo": "fleet-config", "number": 901, "title": "abandoned", "url": "https://x/901",
     "created_at": "2026-10-01T00:00:00Z", "state": "CLOSED", "mergedAt": None,
     "files": ["global-CLAUDE.md"], "head_hashes": {"global-CLAUDE.md": "999999999999"}},
]
_plan_closed_new = gate.plan_reconcile(_advanced, _closed, _dt.date(2026, 11, 12))
check(_plan_closed_new["updates"] == {},
      "plan_reconcile: an entry newer than a closed PR's branch hash is not dropped")
_plan_closed_same = gate.plan_reconcile(
    {"fleet-config/global-CLAUDE.md": "999999999999"}, _closed, _dt.date(2026, 11, 12))
check(_plan_closed_same["updates"] == {"fleet-config/global-CLAUDE.md": None},
      "plan_reconcile: an entry equal to a closed PR's branch hash is dropped")
_closed_unknown = [{**_closed[0], "head_hashes": {"global-CLAUDE.md": None}}]
_plan_closed_unknown = gate.plan_reconcile(_advanced, _closed_unknown, _dt.date(2026, 11, 12))
check(_plan_closed_unknown["updates"] == {}
      and _plan_closed_unknown["unverified_closed"] == ["fleet-config/global-CLAUDE.md"],
      "plan_reconcile: an unresolvable closed-PR branch hash drops nothing and is reported")
_plan_closed_merged = gate.plan_reconcile(
    {"fleet-config/global-CLAUDE.md": "999999999999"},
    _closed + [{**_old_merge[0], "number": 902}], _dt.date(2026, 11, 12))
check(_plan_closed_merged["updates"] == {"fleet-config/global-CLAUDE.md": "f6ae38435b84"},
      "plan_reconcile: an abandoned draft's entry is replaced by a merged PR's hash for that file")
with tempfile.TemporaryDirectory() as _tmp:
    _fleet = Path(_tmp)
    for _name in ("fleet-config", "awake", "asleep"):
        (_fleet / _name / ".git").mkdir(parents=True)
        (_fleet / _name / "CLAUDE.md").write_text("# x\n", encoding="utf-8")
    (_fleet / "awake-wt-3").mkdir()
    (_fleet / "awake-wt-3" / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")
    _real = (gate.FLEET_ROOT, gate.REPO_ROOT, gate.ignored_repos)
    gate.FLEET_ROOT, gate.REPO_ROOT, gate.ignored_repos = _fleet, _fleet / "fleet-config", lambda: frozenset({"asleep"})
    try:
        _keys = {k for k in gate.surface_files(True) if "/" in k and k.endswith("CLAUDE.md")}
        _dormant = gate.dormant_repos()
    finally:
        gate.FLEET_ROOT, gate.REPO_ROOT, gate.ignored_repos = _real
check("awake/CLAUDE.md" in _keys and "asleep/CLAUDE.md" not in _keys,
      "surface_files(--fleet): a dormant repo's files leave the surface, a live repo's stay")
check(_dormant == ["asleep"], "dormant_repos names the dormant sister repo and not the worktree or this repo")

_gh_calls: list[list[str]] = []
_real_run_gh = gate.git_run.run_gh
gate.git_run.run_gh = lambda args, **_kw: (
    _gh_calls.append(args)
    or SimpleNamespace(returncode=0, stdout=json.dumps([
        {"number": 1, "headRefName": "chore/context-purge-20261029", "files": []},
        {"number": 2, "headRefName": "feat/unrelated", "files": []},
    ]), stderr="")
)
try:
    _fetched = gate._fetch_purge_prs("demo", Path("."))
finally:
    gate.git_run.run_gh = _real_run_gh
_args = _gh_calls[0]
check(_args[_args.index("--search") + 1] == f"head:{gate.PURGE_BRANCH_PREFIX}",
      "_fetch_purge_prs: purge branches are selected server-side, not by a newest-N window")
check(_args[_args.index("--limit") + 1] == str(gate.PURGE_PR_FETCH_LIMIT),
      "_fetch_purge_prs: the --limit is the named PURGE_PR_FETCH_LIMIT")
check([e["number"] for e in _fetched] == [1],
      "_fetch_purge_prs: a non-purge branch in the response is still dropped")

# A closed-unmerged PR carries its head-commit hashes into the plan; a merged one carries main_hashes.
_real_ec, _real_ch = gate._ensure_commit, gate._commit_hashes
_head_calls: list[str] = []
gate._ensure_commit = lambda repo_dir, sha, number: _head_calls.append(f"ensure:{sha}:{number}")
gate._commit_hashes = lambda repo_dir, sha, paths: (
    _head_calls.append(f"hash:{sha}") or {p: sha[:12] for p in paths})
gate.git_run.run_gh = lambda args, **_kw: SimpleNamespace(returncode=0, stdout=json.dumps([
    {"number": 7, "headRefName": "chore/context-purge-1", "state": "CLOSED", "mergedAt": None,
     "headRefOid": "abcdef1234567890", "files": [{"path": "CLAUDE.md"}]},
    {"number": 8, "headRefName": "chore/context-purge-2", "state": "MERGED",
     "mergedAt": "2026-11-01T00:00:00Z", "headRefOid": "1111111111111111",
     "mergeCommit": {"oid": "2222222222222222"}, "files": [{"path": "CLAUDE.md"}]},
]), stderr="")
try:
    _fetched2 = {e["number"]: e for e in gate._fetch_purge_prs("demo", Path("."))}
finally:
    gate.git_run.run_gh = _real_run_gh
    gate._ensure_commit, gate._commit_hashes = _real_ec, _real_ch
check(_fetched2[7].get("head_hashes") == {"CLAUDE.md": "abcdef123456"} and "main_hashes" not in _fetched2[7],
      "_fetch_purge_prs: a closed PR is hashed at its head commit, after making that commit available")
check(_fetched2[8].get("main_hashes") == {"CLAUDE.md": "222222222222"} and "head_hashes" not in _fetched2[8]
      and "ensure:abcdef1234567890:7" in _head_calls and "ensure:1111111111111111:8" not in _head_calls,
      "_fetch_purge_prs: a merged PR is still hashed at its merge commit only")

_h.report_and_exit("test_context_purge_check")
