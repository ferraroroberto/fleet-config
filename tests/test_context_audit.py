"""Pure-logic tests for /context-audit's skill-description cap gate (fleet-config#626).

The gate had never worked, in two independent ways, and reported `over_cap=0`
across a fleet where 21 of 49 descriptions were over:

  1. `audit.py`'s `["'].*?["']` "quoted phrase" regex matched apostrophes, so a
     possessive pair opened a span that swallowed the prose between them —
     `chief` measured 29 words against a real 58.
  2. `SKILLS_DIRS` named only fleet-config's own two tiers, so every sister
     repo's `.claude/skills/*/SKILL.md` was never looked at at all.

Neither subsumes the other, so both are pinned here: the regex against `chief`'s
real pre-fix description text (the exact case the sweep measured), and the scan
against a synthetic multi-repo tree. Plus the requirement the symptoms don't
state — a description that *cannot* be measured is its own state, never folded
into the compliant count.

Standalone (like test_context_purge_check) so the gate is testable without the
live fleet on disk, and reachable from the one acceptance gate.

Run: E:/automation/fleet-config/.venv/Scripts/python.exe tests/test_context_audit.py
Exit 0 = all pass.
"""

from __future__ import annotations

import importlib.util
import re
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(REPO / "skills" / "_lib"))
import skill_description as sd  # noqa: E402


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


audit = _load("context_audit", ".claude/skills/context-audit/audit.py")
purge_check = _load("context_purge_check", ".claude/skills/context-purge/check.py")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check


# ---------------------------------------------------------------- defect 1 ----
# `chief`'s description exactly as it stood before the 2026-08-15 purge — the
# text the sweep measured. Two possessives (`Board's`, `launcher's`) plus one
# apostrophe-free stretch between them is precisely what the old character class
# ate. Pinned verbatim: this is a regression fixture, not an example.
CHIEF_PRE_FIX = (
    "Standing conversational fleet chief — the brain of the app-launcher Board's chat mode "
    "(app-launcher#245). Invoked as the injected first prompt of the launcher-spawned chief "
    "session; answers questions about the fleet from live Board/gh data and dispatches issue "
    "work through the launcher's own HTTP API, under strict safety rails. Not for ad-hoc human "
    "invocation in a normal coding session."
)
_OLD_BROKEN = re.compile(r"[\"'].*?[\"']")  # the shipped bug, kept to prove the delta

check(sd.word_count(CHIEF_PRE_FIX) == 58, "chief pre-fix description is 58 words total")
check(sd.prose_words(CHIEF_PRE_FIX) == 58,
      f"chief pre-fix prose count is 58 (got {sd.prose_words(CHIEF_PRE_FIX)}) — no quoted triggers, so prose == total")
check(sd.word_count(_OLD_BROKEN.sub(" ", CHIEF_PRE_FIX)) == 29,
      "the old ['\"] class really did report chief as 29 — the delta this fixes is 29 vs 58")
check(sd.prose_words(CHIEF_PRE_FIX) > 50,
      "chief pre-fix is over the 50-word cap — the gate should have flagged it and did not")

# The exemption itself must survive the fix: double-quoted triggers are still
# subtracted, because they are the routing surface and must stay verbatim.
_TRIGGERS = 'Do a thing. Use when asked, e.g. "/foo", "run foo now".'
check(sd.word_count(_TRIGGERS) == 11 and sd.prose_words(_TRIGGERS) == 9,
      'double-quoted trigger phrases are still exempt from the prose count')
check(sd.quoted_phrases('a "one" b "two three" c') == ['"one"', '"two three"'],
      "quoted_phrases returns each double-quoted span, in order")
check(sd.quoted_phrases("it's the box's own") == [],
      "apostrophes never open a quoted span")

# ---- frontmatter parsing: an unparseable description returns '', not garbage ----
check(sd.frontmatter_description("---\nname: x\ndescription: hello there\n---\nbody\n") == "hello there",
      "frontmatter_description reads the description: value")
check(sd.frontmatter_description("# no frontmatter\n") == "",
      "no frontmatter -> ''")
check(sd.frontmatter_description("---\nname: x\n---\n") == "",
      "frontmatter without description: -> ''")
check(sd.frontmatter_description("---\nname: x\ndescription: unterminated\n") == "unterminated",
      "unterminated frontmatter still yields the description, not a crash")

# ---- one implementation, not three (the fix that was never carried back) ----
check(audit.prose_words is sd.prose_words and audit.frontmatter_description is sd.frontmatter_description,
      "audit.py measures through skills/_lib/skill_description")
check(purge_check.quoted_phrases is sd.quoted_phrases
      and purge_check.frontmatter_description is sd.frontmatter_description,
      "context-purge/check.py measures through the same helper — no third copy to diverge")


# ---------------------------------------------------------------- defect 2 ----
def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _skill(desc: str) -> str:
    return f"---\nname: s\ndescription: {desc}\n---\n\n# s\nBody.\n"


LEAN = "Does one thing well and says so briefly."          # 8 prose words
FAT = " ".join(f"word{i}" for i in range(60))              # 60 prose words

with tempfile.TemporaryDirectory() as td:
    root = Path(td)

    # fleet-config-shaped repo: two tiers.
    _write(root / "self" / "skills" / "alpha" / "SKILL.md", _skill(LEAN))
    _write(root / "self" / ".claude" / "skills" / "beta" / "SKILL.md", _skill(FAT))
    # A sister repo with a project-scoped skill — invisible to the old scan.
    _write(root / "sister" / ".claude" / "skills" / "gamma" / "SKILL.md", _skill(FAT))
    # A sister repo that carries no skills at all: nothing to measure, not a gap.
    (root / "bare").mkdir()
    # Unmeasurable: undecodable bytes, and valid text with no description key.
    _write(root / "broken" / ".claude" / "skills" / "nodesc" / "SKILL.md", "---\nname: n\n---\n# n\n")
    p = root / "broken" / ".claude" / "skills" / "unreadable" / "SKILL.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"---\nname: u\ndescription: \xff\xfe not utf-8\n---\n")

    repos = {
        "self": root / "self",
        "sister": root / "sister",
        "bare": root / "bare",
        "broken": root / "broken",
        "gone": root / "does-not-exist",   # a repo in projects.toml, absent on disk
    }
    roots = audit.skill_roots(repos, user_tier_repo="self")

    check([r for r in roots if r.repo == "self" and r.tier == "user"],
          "the user-tier repo contributes its junctioned skills/ root")
    check(len([r for r in roots if r.repo == "self"]) == 2,
          "the user-tier repo contributes exactly two roots")
    check(all(len([r for r in roots if r.repo == n]) == 1 for n in ("sister", "bare", "broken", "gone")),
          "every other fleet repo contributes its .claude/skills root only")
    check(len(roots) == 6, f"one root per repo + the user tier (got {len(roots)})")

    over = audit.skill_roots(repos, user_tier_repo="self", self_root=root / "elsewhere")
    check(all(r.repo_dir == root / "elsewhere" for r in over if r.repo == "self"),
          "self_root replaces the user-tier repo's directory for both its tiers")

    rows, unmeasured = audit.scan_skills(50, roots)

    measured = {(r["repo"], r["skill"]): r for r in rows}
    check(set(measured) == {("self", "alpha"), ("self", "beta"), ("sister", "gamma")},
          f"every repo's skills are measured, not just the audit's own (got {sorted(measured)})")
    check(measured[("sister", "gamma")]["over_cap"] is True,
          "a sister repo's over-cap description is finally flagged — the 20 that were never seen")
    check(measured[("self", "alpha")]["over_cap"] is False and measured[("self", "alpha")]["tier"] == "user",
          "the junctioned user tier is measured and tagged")
    check(measured[("self", "beta")]["tier"] == "project",
          "the project tier is tagged distinctly")

    reasons = {(u["repo"], u["skill"]): u["reason"] for u in unmeasured}
    check(("broken", "unreadable") in reasons and reasons[("broken", "unreadable")] == "unreadable",
          "an undecodable SKILL.md is unmeasured, not compliant")
    check(("broken", "nodesc") in reasons and reasons[("broken", "nodesc")] == "no frontmatter description",
          "a SKILL.md with no description: is unmeasured, not silently skipped")
    check(("gone", "*") in reasons and reasons[("gone", "*")] == "repo checkout not found",
          "a missing repo checkout is one unmeasured entry, not silence")
    check(not any(u["repo"] == "bare" for u in unmeasured),
          "a repo with no skills dir is not unmeasured — there is nothing there to measure")
    check(not any((u["repo"], u["skill"]) in measured for u in unmeasured),
          "nothing unmeasured leaks into the measured rows")

    by_repo = audit.per_repo_summary(rows, unmeasured)
    summary = {r["repo"]: r for r in by_repo}
    check(summary["self"]["skills"] == 2 and summary["self"]["over_cap"] == 1,
          "per-repo summary counts skills and over-cap per repo")
    check(summary["sister"]["over_cap"] == 1,
          "an over-cap description names the repo it lives in")
    check(summary["broken"]["skills"] == 0 and summary["broken"]["unmeasured"] == 2,
          "a repo whose skills could not be read reports 0 measured / 2 unmeasured")
    check("bare" not in summary, "a repo with no surface at all adds no row")

    # The load-bearing invariant: the three states partition the working set, so
    # a gate that shrinks its own scope can never read the same as a clean run.
    total_files = len(rows) + len(unmeasured)
    check(total_files == 6,
          f"measured (3) + unmeasured (2 files + 1 missing repo) accounts for everything probed (got {total_files})")

_h.report_and_exit("test_context_audit")
