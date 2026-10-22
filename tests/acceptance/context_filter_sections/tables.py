"""Case tables for the context-filter hook's passthrough and wrap decisions."""
from __future__ import annotations

# A helper shipped with a skill produces one payload the orchestrator parses
# directly, so wrapping it risks the #424 truncation for no compression
# upside. #427 widened the rule from skills/_lib to every skill directory --
# the longest-running helpers live beside their own skill (fleet-health's
# capture.py blocks 540s against the 600s cap; system-map's build_data.py
# crawls the whole fleet). The negative rows are the point of the table:
# widening the pattern must not swallow ordinary work.
PYTHON_PREFIX = '& "E:/automation/fleet-config/.venv/Scripts/python.exe" '
SKILL_DIR = '"E:/automation/fleet-config/.claude/skills/'
# (label, command, expect_passthrough, issue)
PASSTHROUGH_CASES = [
    ("skills/_lib sweep helper",
     PYTHON_PREFIX + '"skills/_lib/fleet_audit_scan.py" --root E:\\automation', True, 424),
    ("fleet-health capture.py",
     PYTHON_PREFIX + SKILL_DIR + 'fleet-health/capture.py" --minutes 9', True, 427),
    ("system-map build_data.py",
     PYTHON_PREFIX + SKILL_DIR + 'system-map/build_data.py"', True, 427),
    # #564: design_lint is an executable package directory now, so the
    # /design-sync invocation carries no `.py` — it must still pass through.
    ("design_lint package directory",
     PYTHON_PREFIX + '"C:/Users/rober/.claude/skills/_lib/design_lint" all E:/automation/home-automation',
     True, 564),
    ("ordinary python -c", PYTHON_PREFIX + '-c "print(1)"', False, 427),
    # #1268: reading a helper's source is not running it.
    ("rg over a skill helper's source",
     'rg -n def "E:/automation/fleet-config/skills/_lib/worktree_claim.py"', False, 1268),
    ("ordinary git", "git status --short", False, 427),
    # ...and the extension-less branch must not swallow ordinary work under
    # a skill directory, which matches the same bare `skills/<x>/<y>` shape.
    ("ordinary rg under a skill directory",
     "rg design_lint E:/automation/fleet-config/skills/design-sync/", False, 564),
]

# Under 1% of shell output was wrapped: a `cd X &&` prefix, any pipe, an env prefix, or a
# skill *file path* anywhere in the command each excluded it before the base command was looked at.
# (label, command, tool, expect_wrap)
DECISION_CASES = [
    ("cd X && git status", "cd E:/automation/fleet-config && git status", "Bash", True),
    ("quoted cd; git diff", 'cd "E:/a b"; git diff --stat', "Bash", True),
    ("env prefix", "PYTHONUTF8=1 python x.py", "Bash", True),
    ("pipe to stdout", "git log | head", "Bash", True),
    ("cd + pipe", "cd /e/x && grep -rn foo . | head -40", "Bash", True),
    ("reading a skill helper's source", "grep -n def skills/_lib/worktree_claim.py", "Bash", True),
    ("python heredoc with > in its body",
     "cd /e/x && python - <<'EOF'\nif 2 > 1:\n    print('x')\nEOF", "Bash", True),
    ("stdout to a file", "git log > out.txt", "Bash", False),
    ("stdout appended to a file", "cd /e/x && git log >> out.txt", "Bash", False),
    ("heredoc written to a file", "cat > f.txt <<'EOF'\nhello\nEOF", "Bash", False),
    ("PowerShell Out-File", "git log | Out-File log.txt", "PowerShell", False),
    ("git push behind cd", "cd /e/x && git push origin HEAD", "Bash", False),
    ("streaming", "tail -f app.log | grep ERROR", "Bash", False),
    ("background job", "python server.py &", "Bash", False),
    ("running a skill helper behind cd",
     "cd /e/x && E:/automation/fleet-config/.venv/Scripts/python.exe skills/_lib/chief_ops.py board | head", "Bash", False),
    ("export after cd", "cd /e/x && export A=1; git status", "Bash", False),
    ("cd mid-command", "git status && cd sub", "Bash", False),
    ("&& under the 5.1 wrapper", "git status && git log -1", "PowerShell", False),
    ("&& under bash", "git status && git log -1", "Bash", True),
]
