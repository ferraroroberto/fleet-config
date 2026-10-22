"""Hook-matrix cases for the two commit-time guards: the AI-trailer hook and the secret scan."""
from __future__ import annotations

import tempfile
from typing import List

from acceptance.hook_cases import Case
from acceptance.hook_cases.fakes import FAKE_AKIA, FAKE_GHP, FAKE_SK, FAKE_XOXB


# pre_commit_no_ai_trailer
PRE_COMMIT_CASES: List[Case] = [
    ("pre_commit: Co-Authored-By Claude -> block",
     "pre_commit_no_ai_trailer",
     {"tool_name": "Bash", "tool_input": {"command": 'git commit -m "feat: x\n\nCo-Authored-By: Claude <noreply@anthropic.com>"'}},
     2),
    ("pre_commit: Generated with Claude Code -> block",
     "pre_commit_no_ai_trailer",
     {"tool_name": "Bash", "tool_input": {"command": 'git commit -m "feat: x\n\n🤖 Generated with Claude Code"'}},
     2),
    ("pre_commit: clean message -> allow",
     "pre_commit_no_ai_trailer",
     {"tool_name": "Bash", "tool_input": {"command": 'git commit -m "feat: clean message"'}},
     0),
    ("pre_commit: non-commit Bash -> allow",
     "pre_commit_no_ai_trailer",
     {"tool_name": "Bash", "tool_input": {"command": 'git status'}},
     0),
    # fleet-config#1288: the session-link trailer the harness asks agents to
    # append, and a precondition that scans only a real `git … commit`.
    ("pre_commit: session-link trailer in a heredoc message -> block",
     "pre_commit_no_ai_trailer",
     {"tool_name": "Bash", "tool_input": {"command": (
         "git commit -m \"$(cat <<'EOF'\nfix: x\n\n- why\n\n"
         "Claude-Session: https://claude.ai/code/session_x\nEOF\n)\"")}},
     2),
    ("pre_commit: session-link trailer from PowerShell -> block",
     "pre_commit_no_ai_trailer",
     {"tool_name": "PowerShell", "tool_input": {"command": (
         "git commit -m @'\nfix: x\n\nClaude-Session: https://claude.ai/code/session_x\n'@")}},
     2),
    ("pre_commit: 'Claude session' in prose -> allow",
     "pre_commit_no_ai_trailer",
     {"tool_name": "Bash", "tool_input": {"command": 'git commit -m "fix: keep the Claude session id in the log"'}},
     0),
    ("pre_commit: git -C <path> commit with a trailer -> block",
     "pre_commit_no_ai_trailer",
     {"tool_name": "Bash", "tool_input": {"command": 'git -C E:/repo commit -m "feat: x\n\nCo-Authored-By: Claude <noreply@anthropic.com>"'}},
     2),
    ("pre_commit: quoted git.exe path after the call operator -> block",
     "pre_commit_no_ai_trailer",
     {"tool_name": "PowerShell", "tool_input": {"command": '& "C:/Program Files/Git/cmd/git.exe" -c core.quotepath=off commit -m "feat: x`n`nCo-Authored-By: Claude"'}},
     2),
    ("pre_commit: commit after cd && with a heredoc -F message -> block",
     "pre_commit_no_ai_trailer",
     {"tool_name": "Bash", "tool_input": {"command": "cd E:/repo && git commit -F - <<'EOF'\nfeat: x\n\n🤖 Generated with Claude Code\nEOF"}},
     2),
    ("pre_commit: gh issue body written by heredoc quoting a trailer -> allow",
     "pre_commit_no_ai_trailer",
     {"tool_name": "Bash", "tool_input": {"command": (
         "cat > /tmp/body.md <<'EOF'\nThe guard misses a git commit carrying\n"
         "git commit -m \"Co-Authored-By: Claude\"\nEOF\n"
         "gh issue create --title t --body-file /tmp/body.md")}},
     0),
    ("pre_commit: inline gh body naming git commit and a trailer -> allow",
     "pre_commit_no_ai_trailer",
     {"tool_name": "Bash", "tool_input": {"command": (
         'gh issue create --title t --body "every git commit must drop\n'
         'git commit -m \'Co-Authored-By: Claude\'"')}},
     0),
    ("pre_commit: PowerShell here-string body quoting a trailer -> allow",
     "pre_commit_no_ai_trailer",
     {"tool_name": "PowerShell", "tool_input": {"command": (
         "Set-Content body.md @'\ngit commit -m \"Co-Authored-By: Claude\" is what it's for\n'@")}},
     0),
    # A nested shell's quoted argument is a command (fleet-config#1304): both
    # commit guards re-read it through _lib.runs_git_commit.
    ("pre_commit: commit inside bash -c with a trailer -> block",
     "pre_commit_no_ai_trailer",
     {"tool_name": "Bash", "tool_input": {"command": (
         "bash -c \"git commit -m 'feat: x' -m 'Co-Authored-By: Claude <noreply@anthropic.com>'\"")}},
     2),
    ("pre_commit: commit inside powershell -Command with a trailer -> block",
     "pre_commit_no_ai_trailer",
     {"tool_name": "Bash", "tool_input": {"command": (
         "powershell.exe -NoProfile -Command \"git commit -m 'Co-Authored-By: Claude'\"")}},
     2),
    ("pre_commit: echo quoting bash -c git commit with a trailer -> allow",
     "pre_commit_no_ai_trailer",
     {"tool_name": "Bash", "tool_input": {"command": (
         "echo \"bash -c 'git commit -m Co-Authored-By: Claude'\"")}},
     0),
    # Shell keywords and wrappers that keep the next word in command position
    # (fleet-config#1440): both commit guards read one shared prefix vocabulary.
    ("pre_commit: `if git commit` with a trailer -> block",
     "pre_commit_no_ai_trailer",
     {"tool_name": "Bash", "tool_input": {"command": (
         "if git commit -m 'feat: x' -m 'Co-Authored-By: Claude <noreply@anthropic.com>'; then echo ok; fi")}},
     2),
    ("pre_commit: `sudo git commit` with a trailer -> block",
     "pre_commit_no_ai_trailer",
     {"tool_name": "Bash", "tool_input": {"command": (
         "sudo git commit -m 'feat: x' -m 'Co-Authored-By: Claude <noreply@anthropic.com>'")}},
     2),
]

# secret_scan_guard
SECRET_SCAN_CASES: List[Case] = [
    # cwd is a non-repo tempdir so `git diff --cached` is empty and only the
    # command string is scanned (keeps the case hermetic / git-state-free).
    ("secret_scan: live xoxb- token in commit one-liner -> block",
     "secret_scan_guard",
     {"tool_name": "Bash", "cwd": tempfile.gettempdir(),
      "tool_input": {"command": f'git add config.toml && git commit -m "wip: TELEGRAM_BOT_TOKEN = {FAKE_XOXB}"'}},
     2),
    # The three families the guard was blind to until fleet-config#561 —
    # `context_filter`'s redactor already knew all four, the guard's own
    # copy knew only Slack, and the narrow copy was the one blocking commits.
    # A search that merely says "commit" is not a commit (fleet-config#1304): the
    # guard shares _lib.runs_git_commit with the trailer hook instead of a substring test.
    ("secret_scan: a search that names commit and git is not a commit -> allow",
     "secret_scan_guard",
     {"tool_name": "Bash", "cwd": tempfile.gettempdir(),
      "tool_input": {"command": f'rg "git" --glob "*commit*" -e "TOKEN = {FAKE_XOXB}"'}},
     0),
    # The substring test caught a commit hidden in a nested shell; the shared
    # runs_git_commit re-reads the nested shell's quoted command (fleet-config#1304).
    ("secret_scan: live token in a commit inside bash -c -> block",
     "secret_scan_guard",
     {"tool_name": "Bash", "cwd": tempfile.gettempdir(),
      "tool_input": {"command": f"bash -c \"git commit -m 'wip: GH_TOKEN = {FAKE_GHP}'\""}},
     2),
    ("secret_scan: a search inside bash -c that names commit is not a commit -> allow",
     "secret_scan_guard",
     {"tool_name": "Bash", "cwd": tempfile.gettempdir(),
      "tool_input": {"command": f"bash -c \"rg git --glob '*commit*' -e 'TOKEN = {FAKE_XOXB}'\""}},
     0),
    ("secret_scan: live GitHub token in commit one-liner -> block",
     "secret_scan_guard",
     {"tool_name": "Bash", "cwd": tempfile.gettempdir(),
      "tool_input": {"command": f'git commit -m "wip: GH_TOKEN = {FAKE_GHP}"'}},
     2),
    ("secret_scan: live sk- API key in commit one-liner -> block",
     "secret_scan_guard",
     {"tool_name": "Bash", "cwd": tempfile.gettempdir(),
      "tool_input": {"command": f'git commit -m "wip: API_KEY = {FAKE_SK}"'}},
     2),
    ("secret_scan: AWS access key id in commit one-liner -> block",
     "secret_scan_guard",
     {"tool_name": "Bash", "cwd": tempfile.gettempdir(),
      "tool_input": {"command": f'git commit -m "wip: AWS_ACCESS_KEY_ID = {FAKE_AKIA}"'}},
     2),
    # The `\b` anchors on those patterns: `sk-` inside `risk-…` and `gh?_`
    # inside `highp_…` are prose, not credentials. A redactor could afford
    # that false positive; a guard that refuses `git commit` cannot.
    ("secret_scan: 'risk-'/'highp_' prose lookalikes -> allow",
     "secret_scan_guard",
     {"tool_name": "Bash", "cwd": tempfile.gettempdir(),
      "tool_input": {"command": 'git commit -m "docs: risk-assessment-framework-controls, highp_conversion_estimates"'}},
     0),
    ("secret_scan: xoxb- placeholder (docs ellipsis) -> allow",
     "secret_scan_guard",
     {"tool_name": "Bash", "cwd": tempfile.gettempdir(),
      "tool_input": {"command": 'git commit -m "docs: show xoxb-… placeholder in telegram-workflow.md"'}},
     0),
    ("secret_scan: clean commit message -> allow",
     "secret_scan_guard",
     {"tool_name": "Bash", "cwd": tempfile.gettempdir(),
      "tool_input": {"command": 'git commit -m "feat: clean message"'}},
     0),
    ("secret_scan: non-commit Bash with a token -> allow (only guards commits)",
     "secret_scan_guard",
     {"tool_name": "Bash", "cwd": tempfile.gettempdir(),
      "tool_input": {"command": f'echo {FAKE_XOXB}'}},
     0),
]
