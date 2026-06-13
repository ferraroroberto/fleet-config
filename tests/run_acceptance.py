"""Drive each hook with a sample payload and assert the expected exit code.

Run from the repo root:
    py tests/run_acceptance.py

Exit 0 if all cases pass, 1 otherwise. Prints a single line per case.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

REPO     = Path(__file__).resolve().parent.parent
HOOKS    = REPO / "hooks"

# Resolve a Python interpreter that can run the hooks
PYTHON   = shutil.which("py") or shutil.which("python") or sys.executable

# A synthetic Slack-token-shaped string for the secret_scan_guard cases. It is
# assembled from fragments at runtime so the literal `xoxb-` token body never
# sits in this source file — a contiguous literal would trip GitHub's push
# protection (and the very guard under test). The assembled value still matches
# secret_scan_guard's regex `xoxb-\d{6,}-\d{6,}-[A-Za-z0-9]{8,}`.
FAKE_XOXB = "-".join(("xo" + "xb", "2444556677", "8899001122", "AbCdEfGhIjKlMnOpQrStUvWx"))


def run(hook: str, payload: Dict[str, Any]) -> Tuple[int, str, str]:
    # Strip SLACK_BOT_TOKEN so a hook that posts to Slack (notify_on_idle) takes
    # the graceful-fail path instead of firing a real ping on every test run.
    env = {k: v for k, v in os.environ.items() if k != "SLACK_BOT_TOKEN"}
    res = subprocess.run(
        [PYTHON, str(HOOKS / f"{hook}.py")],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=15,
        env=env,
    )
    return res.returncode, res.stdout, res.stderr


def assert_exit(case: str, expected: int, got: int, stderr: str) -> bool:
    ok = got == expected
    flag = "OK   " if ok else "FAIL "
    extra = "" if ok else f" (got {got}, expected {expected})"
    print(f"{flag} {case}{extra}")
    if not ok and stderr:
        for line in stderr.strip().splitlines():
            print(f"        | {line}")
    return ok


def main() -> int:
    cases: List[Tuple[str, str, Dict[str, Any], int]] = [
        # ---- pre_commit_no_ai_trailer ----
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

        # ---- secret_scan_guard ----
        # cwd is a non-repo tempdir so `git diff --cached` is empty and only the
        # command string is scanned (keeps the case hermetic / git-state-free).
        ("secret_scan: live xoxb- token in commit one-liner -> block",
         "secret_scan_guard",
         {"tool_name": "Bash", "cwd": tempfile.gettempdir(),
          "tool_input": {"command": f'git add config.toml && git commit -m "wip: SLACK_BOT_TOKEN = {FAKE_XOXB}"'}},
         2),
        ("secret_scan: xoxb- placeholder (docs ellipsis) -> allow",
         "secret_scan_guard",
         {"tool_name": "Bash", "cwd": tempfile.gettempdir(),
          "tool_input": {"command": 'git commit -m "docs: show xoxb-… placeholder in slack-workflow.md"'}},
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

        # ---- safe_kill_guard ----
        ("safe_kill: Stop-Process -Name python -> block",
         "safe_kill_guard",
         {"tool_name": "PowerShell", "tool_input": {"command": "Stop-Process -Name python -Force"}},
         2),
        ("safe_kill: Stop-Process -Name pythonw -> block",
         "safe_kill_guard",
         {"tool_name": "PowerShell", "tool_input": {"command": "Stop-Process -Name pythonw -Force"}},
         2),
        ("safe_kill: port-scoped kill on 8446 (protected) -> block",
         "safe_kill_guard",
         {"tool_name": "PowerShell", "tool_input": {"command": "Get-NetTCPConnection -LocalPort 8446 | Select -ExpandProperty OwningProcess | ForEach-Object { Stop-Process -Id $_ -Force }"}},
         2),
        ("safe_kill: port-scoped kill on 8445 (project port) -> allow",
         "safe_kill_guard",
         {"tool_name": "PowerShell", "tool_input": {"command": "Get-NetTCPConnection -LocalPort 8445 | Select -ExpandProperty OwningProcess | ForEach-Object { Stop-Process -Id $_ -Force }"}},
         0),
        ("safe_kill: git push --force origin main -> block",
         "safe_kill_guard",
         {"tool_name": "Bash", "tool_input": {"command": "git push --force origin main"}},
         2),
        ("safe_kill: git push --force feature/x -> allow",
         "safe_kill_guard",
         {"tool_name": "Bash", "tool_input": {"command": "git push --force origin feature/foo"}},
         0),
        ("safe_kill: git commit --no-verify -> block",
         "safe_kill_guard",
         {"tool_name": "Bash", "tool_input": {"command": "git commit --no-verify -m hi"}},
         2),

        # ---- venv_discipline ----
        ("venv: python -m venv venv -> block",
         "venv_discipline",
         {"tool_name": "PowerShell", "cwd": str(REPO), "tool_input": {"command": "python -m venv venv"}},
         2),
        ("venv: python -m venv .venv -> allow",
         "venv_discipline",
         {"tool_name": "PowerShell", "cwd": str(REPO), "tool_input": {"command": "python -m venv .venv"}},
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
    ]

    # ---- py_syntax_check needs real files ----
    tmp = Path(tempfile.mkdtemp(prefix="claude-config-test-"))
    broken = tmp / "broken.py"
    good   = tmp / "good.py"
    broken.write_text("def foo(:\n    pass\n", encoding="utf-8")
    good.write_text("def foo():\n    return 1\n", encoding="utf-8")

    cases.append((
        "py_syntax: broken file -> block",
        "py_syntax_check",
        {"tool_name": "Edit", "cwd": str(tmp), "tool_input": {"file_path": str(broken)}},
        2,
    ))
    cases.append((
        "py_syntax: good file -> allow",
        "py_syntax_check",
        {"tool_name": "Edit", "cwd": str(tmp), "tool_input": {"file_path": str(good)}},
        0,
    ))
    cases.append((
        "py_syntax: non-py file -> allow",
        "py_syntax_check",
        {"tool_name": "Edit", "cwd": str(tmp), "tool_input": {"file_path": str(tmp / "x.txt")}},
        0,
    ))

    # ---- notify_on_idle ----
    # claude-config itself has no per-project slack_notify_channel in projects.toml,
    # but the [global] fallback IS now set. The hook will try to post but the
    # SLACK_BOT_TOKEN is not in the subprocess env, so slack_notify returns False
    # gracefully and the hook still exits 0.
    cases.append((
        "notify_on_idle: global channel set, missing token -> allow (graceful fail)",
        "notify_on_idle",
        {"hook_event_name": "Notification", "cwd": str(REPO), "message": "needs input"},
        0,
    ))
    # idle_prompt is now a deliberate no-op (the 💤 nag was dropped). It must exit
    # 0 without attempting a post — exercises the early-return guard.
    cases.append((
        "notify_on_idle: idle_prompt -> allow (no-op, idle nag dropped)",
        "notify_on_idle",
        {"hook_event_name": "Notification", "notification_type": "idle_prompt",
         "cwd": str(REPO), "message": "Claude is waiting for your input"},
        0,
    ))
    # permission_prompt still pings — with no token it takes the graceful-fail path.
    cases.append((
        "notify_on_idle: permission_prompt -> allow (ping attempted, graceful fail)",
        "notify_on_idle",
        {"hook_event_name": "Notification", "notification_type": "permission_prompt",
         "cwd": str(REPO), "message": "needs permission"},
        0,
    ))

    # ---- session_index: opt-in gating ----
    # A project not opted into capture must be a silent no-op (no indexer spawn).
    cases.append((
        "session_index: non-opted project (tempdir) -> no-op exit 0",
        "session_index",
        {"hook_event_name": "SessionStart", "cwd": tempfile.gettempdir()},
        0,
    ))

    failures = 0
    for name, hook, payload, expected in cases:
        code, _stdout, stderr = run(hook, payload)
        if not assert_exit(name, expected, code, stderr):
            failures += 1

    # ---- slack_notify unit checks (pure / no network) ----
    failures += _slack_notify_unit_checks()

    # ---- notify_on_idle mention-construction unit checks ----
    failures += _notify_mention_unit_checks()

    # ---- notify_on_idle classify / session-link / idle-suppression ----
    failures += _notify_classify_unit_checks()

    # ---- notify_complete deterministic message assembly + resolver ----
    failures += _notify_complete_unit_checks()

    # ---- conversation_capture session-dedup logic ----
    failures += _conversation_capture_unit_checks()

    # ---- conversation capture/index config-driven routing + indexing ----
    failures += _conversation_index_unit_checks()

    # ---- restart_and_verify_webapp restart-strategy + recovery hint ----
    failures += _restart_webapp_unit_checks()

    # ---- gh_body_file_guard: warn-only stdout assertions ----
    failures += _gh_body_file_guard_unit_checks()

    # ---- audit_issue helper pure-logic tests (skills/_lib) ----
    failures += _audit_issue_unit_check()

    # ---- system-map: fleet ↔ data ↔ doc coverage (architecture/) ----
    failures += _system_map_coverage_check()

    # Cleanup
    shutil.rmtree(tmp, ignore_errors=True)

    print()
    print(f"Total: {len(cases) + _UNIT_CHECK_COUNT} | Failed: {failures}")
    return 0 if failures == 0 else 1


# Sum of the unit checks below: slack_notify (3) + mention (5) + classify (6) +
# notify_complete (16) + conversation_capture (13) + conversation_index (6) +
# restart_webapp (6) + gh_body_file_guard (6) + audit_issue (1) + system_map (3).
_UNIT_CHECK_COUNT = 65


def _system_map_coverage_check() -> int:
    """The system map must cover exactly the fleet, and the doc must agree.

    Guards the `/system-map` single source of truth (architecture/fleet.data.js)
    against drift, mechanically:
      1. every fleet repo (projects.toml − [global] architecture_ignore) appears
         on the map;
      2. no map entry is a stale/typo'd repo absent from the fleet;
      3. every mapped repo also appears in ARCHITECTURE.md (data ↔ doc agree).
    Returns the failure count.
    """
    import json
    import tomllib

    failures = 0

    def check(case: str, ok: bool) -> None:
        nonlocal failures
        print(f"{'OK   ' if ok else 'FAIL '} {case}")
        if not ok:
            failures += 1

    arch = REPO / "architecture"
    toml = tomllib.loads((REPO / "hooks" / "projects.toml").read_text(encoding="utf-8"))
    ignore = set(toml.get("global", {}).get("architecture_ignore", []))
    fleet = {
        name for name, tbl in toml.items()
        if name != "global" and isinstance(tbl, dict) and "cwd_prefix" in tbl
    } - ignore

    # fleet.data.js holds `window.FLEET = { ...strict JSON... };` — slice the object out.
    raw = (arch / "fleet.data.js").read_text(encoding="utf-8")
    data = json.loads(raw[raw.index("{"): raw.rindex("}") + 1])
    mapped = {
        e.get("repo", e["nm"])
        for section in ("governance", "enabling", "web", "pipe")
        for e in data.get(section, [])
    }

    missing = fleet - mapped
    stale = mapped - fleet
    check(f"system_map: every fleet repo is on the map (missing: {sorted(missing) or 'none'})", not missing)
    check(f"system_map: no stale map entries (stale: {sorted(stale) or 'none'})", not stale)

    doc = (arch / "ARCHITECTURE.md").read_text(encoding="utf-8")
    doc_missing = sorted(r for r in mapped if r not in doc)
    check(f"system_map: every mapped repo is in ARCHITECTURE.md (missing: {doc_missing or 'none'})", not doc_missing)

    return failures


def _slack_notify_unit_checks() -> int:
    """Exercise slack_notify without touching the network. Returns failure count."""
    sys.path.insert(0, str(HOOKS))
    import slack_notify  # noqa: E402

    failures = 0

    def check(case: str, ok: bool) -> None:
        nonlocal failures
        print(f"{'OK   ' if ok else 'FAIL '} {case}")
        if not ok:
            failures += 1

    check(
        "slack_notify: archive URL -> bare id",
        slack_notify.parse_channel("https://x.slack.com/archives/C0B76GBA0LS") == "C0B76GBA0LS",
    )
    check(
        "slack_notify: bare id passes through",
        slack_notify.parse_channel("  C0B76GBA0LS  ") == "C0B76GBA0LS",
    )

    # Missing token must return False (never raise, never post). Force-unset the
    # env var around the call so a real token on the dev box can't trigger a post.
    saved = os.environ.pop(slack_notify.TOKEN_ENV_VAR, None)
    try:
        result = slack_notify.notify("test", channel="C0B76GBA0LS", token=None)
    finally:
        if saved is not None:
            os.environ[slack_notify.TOKEN_ENV_VAR] = saved
    check("slack_notify: missing token -> False (graceful)", result is False)

    return failures


def _notify_mention_unit_checks() -> int:
    """The single-sourced @mention decision in slack_notify (off by default).

    Mentioning now lives in exactly one place — ``slack_notify.notify()`` — via
    two pure helpers. No caller hand-assembles ``<@U…>`` anymore.
    """
    sys.path.insert(0, str(HOOKS))
    import slack_notify  # noqa: E402

    failures = 0

    def check(case: str, ok: bool) -> None:
        nonlocal failures
        print(f"{'OK   ' if ok else 'FAIL '} {case}")
        if not ok:
            failures += 1

    check("mention_prefix: enabled + user -> tag",
          slack_notify._mention_prefix("U0B71PQEL6S", True) == "<@U0B71PQEL6S> ")
    check("mention_prefix: disabled -> no tag",
          slack_notify._mention_prefix("U0B71PQEL6S", False) == "")
    check("mention_prefix: enabled but no user -> no tag",
          slack_notify._mention_prefix(None, True) == "")
    check("resolve_mention: explicit override wins",
          slack_notify._resolve_mention(True) is True
          and slack_notify._resolve_mention(False) is False)
    # None -> read the [global] slack_notify_mention toggle, which ships off.
    check("resolve_mention: None -> global toggle (off by default)",
          slack_notify._resolve_mention(None) is False)

    return failures


def _notify_classify_unit_checks() -> int:
    """Per-type icon/wording and bridge session-link parsing — the two
    deterministic pieces of the notification logic."""
    sys.path.insert(0, str(HOOKS))
    import notify_on_idle  # noqa: E402

    failures = 0

    def check(case: str, ok: bool) -> None:
        nonlocal failures
        print(f"{'OK   ' if ok else 'FAIL '} {case}")
        if not ok:
            failures += 1

    # ---- classify: icon per notification_type, message passed through ----
    icon, text = notify_on_idle.classify(
        {"notification_type": "permission_prompt", "message": "Claude needs your permission"}
    )
    check("classify: permission -> bell icon + 'awaits your input'",
          icon == "🔔" and text == "Claude awaits your input")
    icon, text = notify_on_idle.classify(
        {"notification_type": "idle_prompt", "message": "Claude is waiting for your input"}
    )
    check("classify: idle -> sleep icon + passthrough",
          icon == "💤" and "waiting" in text)
    icon, _ = notify_on_idle.classify({"message": "x"})
    check("classify: unknown type -> bell fallback", icon == "🔔")

    # ---- session_link: bridge id -> web url, local session -> None ----
    tmp = Path(tempfile.mkdtemp(prefix="notify_link_"))
    try:
        def transcript(*entries: dict) -> str:
            path = tmp / f"t{len(list(tmp.iterdir()))}.jsonl"
            path.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
            return str(path)

        link = notify_on_idle.session_link(transcript(
            {"type": "mode", "mode": "normal"},
            {"type": "bridge-session", "bridgeSessionId": "cse_01HNYE6TFWrUXEGcY8oUiGFr"},
        ))
        check("session_link: bridge id -> claude.ai url",
              link == "https://claude.ai/code/session_01HNYE6TFWrUXEGcY8oUiGFr")
        check("session_link: local session -> None",
              notify_on_idle.session_link(transcript({"type": "user"})) is None)
        check("session_link: missing path -> None", notify_on_idle.session_link(None) is None)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    return failures


def _gh_body_file_guard_unit_checks() -> int:
    """The warn-only nudge fires on the two payload traps and stays silent
    otherwise. Exit is always 0, so these assert on STDOUT, not the exit code:
    a nudge present (non-empty stdout) for the risky forms, empty for the safe
    ones."""
    failures = 0

    def check(case: str, ok: bool) -> None:
        nonlocal failures
        print(f"{'OK   ' if ok else 'FAIL '} {case}")
        if not ok:
            failures += 1

    def stdout_for(command: str) -> str:
        code, out, _err = run("gh_body_file_guard", {"tool_name": "Bash", "tool_input": {"command": command}})
        # warn-only: the hook must never block (exit non-zero) regardless of input.
        return out.strip() if code == 0 else f"__NONZERO_EXIT_{code}__"

    check("gh_guard: gh pr create --body with backtick -> nudge",
          bool(stdout_for('gh pr create --title x --body "see `uname -a`"')))
    check("gh_guard: gh issue comment --body with heredoc -> nudge",
          bool(stdout_for('gh issue comment 5 --body "$(cat <<EOF\nhi\nEOF\n)"')))
    check("gh_guard: PowerShell here-string through Bash -> nudge",
          bool(stdout_for("printf '%s' @'\nhello\n'@")))
    check("gh_guard: gh pr create --body-file -> silent",
          stdout_for("gh pr create --title x --body-file E:/tmp/pr-116.md") == "")
    check("gh_guard: gh issue list (read) -> silent",
          stdout_for("gh issue list --state open --limit 20") == "")
    check("gh_guard: gh pr create plain inline body (no risky construct) -> silent",
          stdout_for('gh pr create --title x --body "plain text, nothing to expand"') == "")

    return failures


def _audit_issue_unit_check() -> int:
    """Run skills/_lib/audit_issue.py's pure-logic tests as a subprocess.

    Kept standalone (not inlined here) so the helper's marker / title-adoption /
    keep-close logic is testable on its own, and reachable from the one gate.
    """
    proc = subprocess.run(
        [PYTHON, str(REPO / "tests" / "test_audit_issue.py")],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    ok = proc.returncode == 0
    print(f"{'OK   ' if ok else 'FAIL '} audit_issue: pure-logic unit tests")
    if not ok:
        for line in (proc.stdout or "").strip().splitlines():
            print(f"        | {line}")
    return 0 if ok else 1


def _conversation_capture_unit_checks() -> int:
    """The per-session dedup logic: stable token, filename shape, and the
    supersede-prior sweep that collapses a session's many Stop captures to one."""
    sys.path.insert(0, str(HOOKS))
    import conversation_capture as cc  # noqa: E402

    failures = 0

    def check(case: str, ok: bool) -> None:
        nonlocal failures
        print(f"{'OK   ' if ok else 'FAIL '} {case}")
        if not ok:
            failures += 1

    check("session_token: last 8 alnum of a uuid-ish id",
          cc.session_token("01HNYE6TF-AbCd-1234") == "abcd1234")
    check("session_token: no id -> empty (dedup skipped)",
          cc.session_token("") == "" and cc.session_token(None) == "")
    check("capture_filename: session token only (degenerate content)",
          cc.capture_filename("2026-06-02-2020", "day-today", "abcd1234", "")
          == "2026-06-02-2020-day-today-abcd1234.md")
    check("capture_filename: both tokens -> session then signature",
          cc.capture_filename("2026-06-02-2020", "day-today", "abcd1234", "cafe9999")
          == "2026-06-02-2020-day-today-abcd1234-cafe9999.md")
    check("capture_filename: no tokens -> plain timestamped name",
          cc.capture_filename("2026-06-02-2020", "day-today", "", "")
          == "2026-06-02-2020-day-today.md")

    # content_signature is the resume-stable identity: it keys off the first real
    # user turn (copied forward verbatim on --resume), not the session id. So two
    # transcripts sharing that opening turn — but with different later turns and a
    # different session_id — hash identically; a preamble-only turn yields "".
    preamble = ("user", "Base directory for this skill: E:/automation/life-os/x")
    turn1 = ("user", "I want to record today's licenses and GPS for the ferry trip")
    orig = [preamble, turn1, ("assistant", "ok")]
    resumed = [preamble, turn1, ("assistant", "ok"),
               ("user", "and add the return ferry time"), ("assistant", "done")]
    check("content_signature: stable across resume (same first turn), non-empty",
          cc.content_signature(orig) == cc.content_signature(resumed) != "")
    check("content_signature: preamble-only turn -> empty (falls back to session token)",
          cc.content_signature([preamble]) == "")

    # conversation_slug keys off the WHOLE conversation's salient words, not the
    # opener — issue #84. A vague opening line ("tell me about your day") must not
    # decide the slug when a topic word recurs throughout the exchange.
    convo = [
        preamble,
        ("user", "Let me tell you about my day, I want to share what happened"),
        ("assistant", "Sure — how was the ferry crossing?"),
        ("user", "The ferry crossing was rough and the licenses paperwork slipped"),
        ("assistant", "Did you sort the ferry licenses after the crossing?"),
        ("user", "Yes, renewed the licenses once the ferry crossing ended"),
    ]
    slug = cc.conversation_slug(convo)
    check("conversation_slug: topic words beat the vague opener",
          "ferry" in slug and "licenses" in slug and "share" not in slug)
    check("conversation_slug: frequency ordering, ties by first appearance",
          slug == "ferry-crossing-licenses")
    check("conversation_slug: no significant words -> first-turn fallback",
          cc.conversation_slug([preamble]) == "session")
    check("conversation_slug: command tags / preamble stripped before counting",
          cc.conversation_slug([
              preamble,
              ("user", "<command-name>/journal-daily</command-name> logbook logbook entries"),
          ]) == "logbook-entries")

    # supersede_prior removes this session's earlier captures, leaves others.
    tmp = Path(tempfile.mkdtemp(prefix="cc_dedup_"))
    try:
        (tmp / "2026-06-02-2016-session-abcd1234.md").write_text("early", encoding="utf-8")
        (tmp / "2026-06-02-2018-other-abcd1234.md").write_text("mid", encoding="utf-8")
        (tmp / "2026-06-02-2020-real-deadbeef.md").write_text("other session", encoding="utf-8")
        cc.supersede_prior(tmp, "abcd1234", "")
        remaining = sorted(p.name for p in tmp.iterdir())
        check("supersede_prior: drops same-session files, keeps other sessions",
              remaining == ["2026-06-02-2020-real-deadbeef.md"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # supersede_prior on resume: the predecessor carries the SAME content
    # signature but a DIFFERENT (now-rewritten) session token, so only the
    # signature match collapses it. An unrelated conversation is left untouched.
    tmp = Path(tempfile.mkdtemp(prefix="cc_resume_"))
    try:
        (tmp / "2026-06-05-1606-licenses-and-gps-aaaa1111-cafe9999.md").write_text("v1", encoding="utf-8")
        (tmp / "2026-06-08-2105-other-topic-bbbb2222-dead8888.md").write_text("unrelated", encoding="utf-8")
        # resumed capture: new session token, same content signature.
        cc.supersede_prior(tmp, "eeee5555", "cafe9999")
        remaining = sorted(p.name for p in tmp.iterdir())
        check("supersede_prior: resume (new session id, same signature) drops predecessor",
              remaining == ["2026-06-08-2105-other-topic-bbbb2222-dead8888.md"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    return failures


def _conversation_index_unit_checks() -> int:
    """Config-driven capture routing + the indexer's digest/upsert/decay logic.

    Hermetic: the hub is stubbed, so no network is touched. Covers the opt-in
    gate (a non-registered project captures nothing), routing resolution, and
    the index round-trip including the preserved decay zone."""
    sys.path.insert(0, str(HOOKS))
    import conversation_capture as cc  # noqa: E402
    import conversation_index as ci  # noqa: E402
    import hub_client  # noqa: E402

    failures = 0

    def check(case: str, ok: bool) -> None:
        nonlocal failures
        print(f"{'OK   ' if ok else 'FAIL '} {case}")
        if not ok:
            failures += 1

    # ---- config resolution / opt-in gate ----
    lo = cc.resolve_capture_config({"cwd": "E:/automation/life-os"})
    check("capture_config: life-os -> skills routing",
          lo is not None and lo.routing == "skills" and lo.active_marker == ".active-skill")
    check("capture_config: non-opted project -> None",
          cc.resolve_capture_config({"cwd": "E:/automation/app-launcher"}) is None)

    # ---- conversations_dirs: flat -> one dir labelled by project ----
    flat = cc.CaptureConfig(root=Path(tempfile.gettempdir()) / "proj", routing="flat",
                            conversations_dir="conversations", skills_dir=".claude/skills",
                            active_marker=".active-skill")
    dirs = ci.conversations_dirs(flat)
    check("conversations_dirs: flat -> single dir labelled by project",
          len(dirs) == 1 and dirs[0][1] == "proj")

    # ---- index_dir: hermetic digest/upsert + decay-zone preservation ----
    saved = hub_client.complete
    ci.hub_client.complete = lambda *a, **k: "Topic: t\nDecisions: none\nOpen loops: none"
    tmp = Path(tempfile.mkdtemp(prefix="idx_unit_"))
    try:
        cap = tmp / "2026-06-10-1200-foo-aaaa1111.md"
        cap.write_text("d\n\n**You**: x\n\n**Claude**: y\n", encoding="utf-8")
        os.utime(cap, (time.time() - 600, time.time() - 600))  # settled
        n = ci.index_dir(tmp, "t")
        idx = (tmp / "index.md").read_text(encoding="utf-8")
        check("index_dir: writes one <!-- idx --> entry",
              n == 1 and "<!-- idx" in idx and "**Topic:**" in idx)
        check("index_dir: idempotent re-run -> 0", ci.index_dir(tmp, "t") == 0)
        with open(tmp / "index.md", "a", encoding="utf-8") as fh:
            fh.write("\n" + ci.DECAY_MARKER + "\n### 2026-04 · period\n- squashed\n")
        cap2 = tmp / "2026-06-11-1300-bar-bbbb2222.md"
        cap2.write_text("d\n\n**You**: x\n\n**Claude**: y\n", encoding="utf-8")
        os.utime(cap2, (time.time() - 600, time.time() - 600))
        ci.index_dir(tmp, "t")
        check("index_dir: decay zone preserved across re-index",
              "squashed" in (tmp / "index.md").read_text(encoding="utf-8"))
    finally:
        ci.hub_client.complete = saved
        shutil.rmtree(tmp, ignore_errors=True)

    return failures


def _restart_webapp_unit_checks() -> int:
    """The tray-owned restart strategy: projects.toml carries a `restart_cmd`
    for the three tray apps, and the recovery hint stays actionable and
    :8446-safe. Both are pure (no tray needed), so they're gate-testable."""
    sys.path.insert(0, str(HOOKS))
    import restart_and_verify_webapp as rw  # noqa: E402
    import _lib  # noqa: E402

    failures = 0

    def check(case: str, ok: bool) -> None:
        nonlocal failures
        print(f"{'OK   ' if ok else 'FAIL '} {case}")
        if not ok:
            failures += 1

    reg = _lib.load_registry()
    by_name = {p.name: p for p in reg.projects}

    check("restart_cmd: app-launcher respawns through WebappManager",
          "WebappManager" in (by_name["app-launcher"].restart_cmd or ""))
    check("restart_cmd: voice-transcriber now has webapp_port 8443 + respawn cmd",
          by_name["voice-transcriber"].webapp_port == 8443
          and "WebappManager" in (by_name["voice-transcriber"].restart_cmd or ""))
    check("restart_cmd: local-llm-hub keeps the tray_cmd path (no restart_cmd)",
          by_name["local-llm-hub"].restart_cmd is None)

    hint = rw.recovery_hint(
        "app-launcher", 8445, Path("E:/automation/app-launcher"),
        by_name["app-launcher"].restart_cmd, "tray.bat",
    )
    check("recovery_hint: leads with the manager respawn + flags it :8446-safe",
          "WebappManager" in hint and "spares :8446" in hint)
    check("recovery_hint: tray --restart present but flagged a :8446-destroying last resort",
          "tray.bat --restart" in hint and "destroys :8446" in hint)

    tray_only = rw.recovery_hint("local-llm-hub", 8000, Path("E:/automation/local-llm-hub"), None, "tray.bat")
    check("recovery_hint: no restart_cmd -> option 1 is the tray, no respawn line",
          "WebappManager" not in tray_only and "1) Full clean restart" in tray_only)

    return failures


def _notify_complete_unit_checks() -> int:
    """Canonical per-kind message assembly + the shared slack-target resolver."""
    sys.path.insert(0, str(HOOKS))
    import notify_complete  # noqa: E402
    import _lib  # noqa: E402

    failures = 0

    def check(case: str, ok: bool) -> None:
        nonlocal failures
        print(f"{'OK   ' if ok else 'FAIL '} {case}")
        if not ok:
            failures += 1

    bm = notify_complete.build_message
    check("build: add -> filed + issue link",
          bm("add", issue="5", title="T", url="http://u") == "🆕 Filed #5 T · http://u")
    check("build: start -> ready-to-validate + summary",
          bm("start", issue="5", title="T", summary="do X") == "🚦 #5 T — ready to validate. do X")
    check("build: start -> ready-to-validate + summary + issue link",
          bm("start", issue="5", title="T", url="http://u", summary="do X")
          == "🚦 #5 T — ready to validate. do X · http://u")
    check("build: finish -> done + PR link",
          bm("finish", issue="5", title="T", url="http://u") == "✅ Done #5 T — PR merged · http://u")
    check("build: yolo -> shipped + PR link",
          bm("yolo", issue="5", title="T", url="http://u") == "🚀 Shipped #5 T — PR · http://u")
    check("build: batch -> passed/total",
          bm("batch", passed="2", total="3") == "🏁 Batch done: 2/3 passed — /issue-finish each branch to ship")
    check("build: finish with no url/title degrades cleanly",
          bm("finish", issue="5") == "✅ Done #5 — PR merged")
    check("build: audit -> fleet audit + summary + comment link",
          bm("audit", summary="3 audited, 2 issues", url="http://gh/comment") == "📊 Fleet audit — 3 audited, 2 issues · http://gh/comment")
    check("build: audit with no url degrades cleanly",
          bm("audit", summary="0 audited") == "📊 Fleet audit — 0 audited")
    check("build: cleanup -> bucket + merged + review counts",
          bm("cleanup", summary="documentation", merged="5", review="2")
          == "🧹 Cleanup documentation: 5 merged, 2 awaiting review")
    check("build: cleanup easy-mode (0 review) drops the review clause",
          bm("cleanup", summary="documentation", merged="3", review="0")
          == "🧹 Cleanup documentation: 3 merged")
    check("build: recap -> weekly recap + summary",
          bm("recap", summary="5 skills swept, 3 proposals") == "🔄 Weekly recap — 5 skills swept, 3 proposals")
    check("build: recap with no summary degrades cleanly",
          bm("recap") == "🔄 Weekly recap")
    check("build: finish-batch -> merged + blocked counts",
          bm("finish-batch", merged="4", blocked="1") == "🏁 Finished batch: 4 merged, 1 blocked")
    check("build: finish-batch (0 blocked) drops the blocked clause",
          bm("finish-batch", merged="5", blocked="0") == "🏁 Finished batch: 5 merged")

    # The shared resolver: unknown cwd -> [global] channel/user + 'claude' name.
    ch, usr, nm = _lib.resolve_slack_target(Path("E:/does/not/match/anything"))
    check("resolve_slack_target: global fallback + claude name",
          ch == "C0B76GBA0LS" and usr == "U0B71PQEL6S" and nm == "claude")

    return failures


if __name__ == "__main__":
    sys.exit(main())
