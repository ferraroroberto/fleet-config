"""Hook-matrix cases for the venv, Windows-path, dated-docs-filename and context-filter guards."""
from __future__ import annotations

import tempfile
from typing import List

from acceptance.hook_cases import Case
from acceptance.shared import REPO


# venv_discipline
VENV_CASES: List[Case] = [
    ("venv: python -m venv venv -> block",
     "venv_discipline",
     {"tool_name": "PowerShell", "cwd": str(REPO), "tool_input": {"command": "python -m venv venv"}},
     2),
    # cwd is a .venv-free dir (not REPO — this repo now ships its own .venv,
    # which would trip the bare-python rule): isolates the correct-name
    # creation form against the wrong-name rule (fleet-config#350).
    ("venv: python -m venv .venv -> allow",
     "venv_discipline",
     {"tool_name": "PowerShell", "cwd": tempfile.gettempdir(), "tool_input": {"command": "python -m venv .venv"}},
     0),
    ("venv: activate.ps1 -> block",
     "venv_discipline",
     {"tool_name": "PowerShell", "cwd": "E:/automation/app-launcher", "tool_input": {"command": ".\\.venv\\Scripts\\Activate.ps1"}},
     2),
    ("venv: source .venv/bin/activate -> block",
     "venv_discipline",
     {"tool_name": "Bash", "cwd": "E:/automation/app-launcher", "tool_input": {"command": "source .venv/bin/activate"}},
     2),
    ("venv: bare python with .venv present -> block",
     "venv_discipline",
     {"tool_name": "PowerShell", "cwd": "E:/automation/app-launcher", "tool_input": {"command": "python script.py"}},
     2),
    ("venv: path-scoped venv python -> allow",
     "venv_discipline",
     {"tool_name": "PowerShell", "cwd": "E:/automation/app-launcher", "tool_input": {"command": "& .\\.venv\\Scripts\\python.exe -m pip install foo"}},
     0),
    ("venv: bare python with NO .venv -> allow",
     "venv_discipline",
     {"tool_name": "Bash", "cwd": tempfile.gettempdir(), "tool_input": {"command": "python --version"}},
     0),
    # `_is_path_scoped` used to search the whole command string, so a
    # compound command pairing a correctly venv-scoped first clause with a
    # bare `python` in a later clause passed whole -- exactly the shape
    # the guard exists to catch, and the repo's own documented default
    # ("chain them in a single PowerShell call"). Fixed to evaluate
    # path-scoping per clause (fleet-config#709).
    ("venv: compound command, scoped first clause + bare python later -> block (fleet-config#709)",
     "venv_discipline",
     {"tool_name": "PowerShell", "cwd": "E:/automation/app-launcher",
      "tool_input": {"command": '& .\\.venv\\Scripts\\python.exe -c "1"; python foo.py'}},
     2),
    # The mirror case still allows: both clauses path-scoped.
    ("venv: compound command, both clauses path-scoped -> allow (fleet-config#709)",
     "venv_discipline",
     {"tool_name": "PowerShell", "cwd": "E:/automation/app-launcher",
      "tool_input": {"command": '& .\\.venv\\Scripts\\python.exe -c "1"; & .\\.venv\\Scripts\\python.exe foo.py'}},
     0),
    # The clause split ignored quoting, so a quoted `a|python|b`
    # alternation handed rule 3 a bare `python` clause. A segment that
    # opens inside quotes right after a lone `|` is now read as text
    # (fleet-config#885); every other quoted shape keeps its reading, so a
    # string a shell will run still blocks.
    *[(f"venv: quoted alternation naming the interpreter -> allow (fleet-config#885): {c}",
       "venv_discipline",
       {"tool_name": tool, "cwd": "E:/automation/app-launcher", "tool_input": {"command": c}},
       0)
      for tool, c in (
          ("PowerShell", "Get-CimInstance Win32_Process | Where-Object { $_.Name -match 'msedge|chrome|python|webview|app-launcher|tray' } | Select-Object -First 2 -ExpandProperty Name"),
          ("PowerShell", "Get-Process | Where-Object { $_.Name -match 'msedge|python' } | Select-Object -First 1 -ExpandProperty Name"),
          ("Bash", "grep -E 'foo|python|bar' file"),
          ("Bash", 'grep -E "a|pip|b" file'),
          ("Bash", "jq '.[] | select(.name|test(\"python\"))'"),
          ("Bash", "gh pr create --title 'fix: python|pip regex' --body-file body.md"),
      )],
    *[(f"venv: bare interpreter beside quoted text -> block (fleet-config#885): {c}",
       "venv_discipline",
       {"tool_name": "Bash", "cwd": "E:/automation/app-launcher", "tool_input": {"command": c}},
       2)
      for c in (
          ".venv/Scripts/python.exe -V && python x.py",
          "echo hi | python -",
          "ls; python3 -m pip install x",
          "grep -E 'a|b' x|python -",
          "echo x|python -c 'a;b'",
          'bash -c "cd sub; python x.py"',
          "ssh host 'cd repo | pip install -r req.txt'",
      )],
]

# bash_windows_path_guard
WINDOWS_PATH_CASES: List[Case] = [
    ("windows_path_guard: unquoted drive path -> block",
     "bash_windows_path_guard",
     {"tool_name": "Bash", "tool_input": {"command": r"ls E:\automation\fleet-config"}},
     2),
    ("windows_path_guard: forward-slash path -> allow",
     "bash_windows_path_guard",
     {"tool_name": "Bash", "tool_input": {"command": "ls E:/automation/fleet-config"}},
     0),
    ("windows_path_guard: double-quoted backslash path -> allow",
     "bash_windows_path_guard",
     {"tool_name": "Bash", "tool_input": {"command": 'echo "E:\\automation"'}},
     0),
    ("windows_path_guard: single-quoted backslash path -> allow",
     "bash_windows_path_guard",
     {"tool_name": "Bash", "tool_input": {"command": "echo 'E:\\automation'"}},
     0),
    ("windows_path_guard: backslash path inside heredoc body -> allow",
     "bash_windows_path_guard",
     {"tool_name": "Bash", "tool_input": {"command": "cat <<'EOF'\nE:\\automation\nEOF"}},
     0),
    ("windows_path_guard: same command on PowerShell -> allow (Bash-only guard)",
     "bash_windows_path_guard",
     {"tool_name": "PowerShell", "tool_input": {"command": r"cd E:\automation"}},
     0),
    ("windows_path_guard: plain command, no drive path -> allow",
     "bash_windows_path_guard",
     {"tool_name": "Bash", "tool_input": {"command": "git log --oneline"}},
     0),
]

# docs_dated_filename_guard (block hook; no disk read needed)
DOCS_GUARD_CASES: List[Case] = [
    ("docs_guard: Write docs/2026-06-18-x.md -> block",
     "docs_dated_filename_guard",
     {"tool_name": "Write", "tool_input": {"file_path": "E:/automation/foo/docs/2026-06-18-retro.md"}},
     2),
    ("docs_guard: Write docs/architecture.md (topic name) -> allow",
     "docs_dated_filename_guard",
     {"tool_name": "Write", "tool_input": {"file_path": "E:/automation/foo/docs/architecture.md"}},
     0),
    ("docs_guard: dated file NOT under docs/ -> allow",
     "docs_dated_filename_guard",
     {"tool_name": "Write", "tool_input": {"file_path": "E:/automation/foo/src/2026-06-18-x.md"}},
     0),
    ("docs_guard: Edit (not Write) a dated docs file -> allow",
     "docs_dated_filename_guard",
     {"tool_name": "Edit", "tool_input": {"file_path": "E:/automation/foo/docs/2026-06-18-retro.md"}},
     0),
]

# context_filter_hook
CONTEXT_FILTER_HOOK_CASES: List[Case] = [
    # Exit is 0 whether the machine's mode.json enables it or not; the
    # deterministic resolution cases live in checks_context_filter with an isolated
    # FLEET_CONTEXT_FILTER_DIR.
    ("context_filter_hook: default -> allow (exit 0 in every mode)",
     "context_filter_hook",
     {"tool_name": "PowerShell", "cwd": str(REPO), "tool_input": {"command": "git status --short"}},
     0),
]
