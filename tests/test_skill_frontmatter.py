"""Every SKILL.md frontmatter in this repo parses as written (fleet-config#845).

Two halves:

  1. `skills/_lib/frontmatter.py`'s `frontmatter_error()` pinned against shapes
     whose real-YAML outcome was established with PyYAML — this repo's `.venv`
     is stdlib-only, so the loader cannot run here; its verdicts are recorded.
  2. A live scan of both skill trees this repo ships — `skills/` (junctioned
     into every agent's user scope) and `.claude/skills/` — so a description a
     YAML loader rejects fails this gate instead of being found by eye during a
     purge. `issue-yolo`'s broken description sat on a branch reporting PASS
     precisely because nothing looked.

Run: E:/automation/fleet-config/.venv/Scripts/python.exe tests/test_skill_frontmatter.py
Exit 0 = all pass.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402
from frontmatter import frontmatter_error  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_h = CheckHarness()
check = _h.check


def fm(body: str) -> str:
    return f"---\n{body}\n---\n\n# s\nBody.\n"


# ---- shapes PyYAML accepts, with the value the text shows ----
for body in (
    "name: s\ndescription: Two modes - concise is default",
    "name: s\ndescription: a:b ratio, http://x.y, foo#bar, 50% off, what? really",
    "name: s\ndescription: -flag is fine",
    'name: s\ndescription: "quoted: fine"',
    "name: s\ndescription: 'it''s fine'",
    'name: s\ndescription: "e.g. \\"/foo\\"" ',
    "name: s\ndescription: >\n  folded text\n  more",
    "name: s\n# a comment line\ndescription: ok",
    "name: s\nmetadata:\n  type: user\ndescription: ok",
    'name: s\ndescription: Does a thing. E.g. "/foo", "run foo now".',
    # Claude Code's own frontmatter shapes — a gate that flags these is wrong.
    "name: s\ndescription: ok\nallowed-tools: [Read, Grep]\nargument-hint: [issue-number]",
    'name: s\ndescription: ok\nmeta: {a: "x, ]", b: 2}',
):
    check(frontmatter_error(fm(body)) is None, f"valid frontmatter accepted: {body!r}")

check(frontmatter_error(fm("name: s\r\ndescription: CRLF endings\r")) is None,
      "CRLF line endings are not a parse error")
check(frontmatter_error("# no frontmatter\n") is None,
      "a file with no frontmatter is not a parse failure")

# ---- shapes PyYAML rejects (or silently truncates), each with its reason ----
for body, reason in (
    ("name: s\ndescription: One-shot the GitHub-issue workflow: file the issue", "': '"),
    ("name: s\ndescription: ends with colon:", "': '"),
    ('name: s\ndescription: uses "/x", "y: z" quoted inside', "': '"),
    ('name: s\ndescription: "unterminated', "not closed"),
    ('name: s\ndescription: "closed" trailing', "after the closing"),
    ("name: s\ndescription: 'bad' x", "after the closing"),
    ("name: s\ndescription: @reserved", "indicator '@'"),
    ("name: s\ndescription: `tick", "indicator '`'"),
    ("name: s\ndescription: - list", "indicator '-'"),
    ("name: s\ndescription: *alias", "indicator '*'"),
    ("name: s\ndescription:\tTabbed", "tab after ':'"),
    ('name: s\ndescription: e.g. "/foo", "week #N", more', "' #'"),
    ("name: s\ndescription: plain\n  continued", "continues onto"),
    ("name: s\nname: t\ndescription: ok", "duplicate key"),
    ("name: s\njust text\ndescription: ok", "not a `key: value`"),
    ("name: s\ndescription: ok\nallowed-tools: [Read, Grep", "not closed"),
    ("name: s\ndescription: ok\nallowed-tools: [Read] Grep", "after the flow collection"),
    ("name: s\ndescription: [a, b]", "flow collection, not text"),
):
    err = frontmatter_error(fm(body))
    check(err is not None and reason in err,
          f"rejected with {reason!r}: {body!r} (got {err!r})")

check("no closing" in (frontmatter_error("---\nname: s\ndescription: ok\n") or ""),
      "an unterminated frontmatter is a parse failure")

# ---- live scan: every SKILL.md this repo ships ----
skill_mds = sorted(REPO.glob("skills/*/SKILL.md")) + sorted(REPO.glob(".claude/skills/*/SKILL.md"))
check(len(skill_mds) >= 10,
      f"the scan found this repo's skills (got {len(skill_mds)}) — an empty glob is not a clean tree")
for path in skill_mds:
    err = frontmatter_error(path.read_text(encoding="utf-8"))
    check(err is None, f"{path.relative_to(REPO).as_posix()}: frontmatter does not parse: {err}")

_h.report_and_exit("test_skill_frontmatter")
