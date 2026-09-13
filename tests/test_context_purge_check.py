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

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check


MERMAID = (
    "<!-- system-map:mermaid:start -->\n### Fleet map\n```mermaid\nflowchart LR\n```\n"
    "<!-- system-map:mermaid:end -->"
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

# ---- --base: every changed instruction file on a branch (fleet-config#833) ----
check(cp.is_instruction_file("CLAUDE.md") and cp.is_instruction_file("a/.claude/skills/x/SKILL.md")
      and cp.is_instruction_file(".claude/rules/style.md") and cp.is_instruction_file("AGENTS.md")
      and not cp.is_instruction_file("README.md") and not cp.is_instruction_file("docs/CLAUDE-notes.txt"),
      "instruction-file classification matches the /prompt-audit surface")

import contextlib  # noqa: E402
import io  # noqa: E402
import shutil  # noqa: E402
import tempfile  # noqa: E402

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
finally:
    shutil.rmtree(_tmp, ignore_errors=True)

_h.report_and_exit("test_context_purge_check")
