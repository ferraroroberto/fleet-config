"""Acceptance checks for the notification surface (fleet-config#680).

Everything that decides *whether, where and how* something reaches Roberto:
`slack_notify`'s transport (no network touched), `notify_on_idle`'s mention
construction / prompt classification / idle suppression, its Fleet-Board deep
link and chief-managed routing, `notify_complete`'s deterministic message
assembly, and the category -> channel routing table.

Split out of the former 2681-line `unit_checks.py` (see `checks_context_filter`
for why). Each function is self-contained and returns its own
`(failures, total)`; `tests/run_acceptance.py` sums them exactly as before.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Tuple

from acceptance.shared import (
    HOOKS,
    REPO,
    _Checker,
    run,
)

# Every function below inserts its own sys.path entry (HOOKS or skills/_lib)
# right before its dynamic import -- matches the pre-split file's per-function
# style, so each check's dependency is visible at its own call site.


def _slack_notify_unit_checks() -> Tuple[int, int]:
    """Exercise slack_notify without touching the network. Returns failure count."""
    sys.path.insert(0, str(HOOKS))
    import slack_notify  # noqa: E402

    check = _Checker()

    check(
        "slack_notify: archive URL -> bare id",
        slack_notify.parse_channel("https://x.slack.com/archives/C0B76GBA0LS") == "C0B76GBA0LS",
    )
    check(
        "slack_notify: bare id passes through",
        slack_notify.parse_channel("  C0B76GBA0LS  ") == "C0B76GBA0LS",
    )

    # Missing token must return False (never raise, never post). Force-unset the
    # env var AND neutralize the settings.json fallback around the call, so
    # neither a real token in the dev box's env nor one in ~/.claude/settings.json
    # can trigger a post — this exercises the genuine "no token anywhere" path.
    saved = os.environ.pop(slack_notify.TOKEN_ENV_VAR, None)
    saved_from_settings = slack_notify._token_from_settings
    slack_notify._token_from_settings = lambda: None
    try:
        result = slack_notify.notify("test", channel="C0B76GBA0LS", token=None)
    finally:
        slack_notify._token_from_settings = saved_from_settings
        if saved is not None:
            os.environ[slack_notify.TOKEN_ENV_VAR] = saved
    check("slack_notify: missing token -> False (graceful)", result is False)

    # The settings.json fallback resolves a token when the env var is unset —
    # this is the launcher-agnostic behaviour (#192). Stub the file reader so the
    # check is hermetic (independent of whether the dev box's settings.json has a
    # token) and confirm the resolution order: env var wins, else settings.json.
    saved_env = os.environ.pop(slack_notify.TOKEN_ENV_VAR, None)
    saved_reader = slack_notify._token_from_settings
    slack_notify._token_from_settings = lambda: "xoxb-from-settings"
    try:
        from_settings = slack_notify._resolve_token(None)
        os.environ[slack_notify.TOKEN_ENV_VAR] = "xoxb-from-env"
        env_wins = slack_notify._resolve_token(None)
    finally:
        slack_notify._token_from_settings = saved_reader
        os.environ.pop(slack_notify.TOKEN_ENV_VAR, None)
        if saved_env is not None:
            os.environ[slack_notify.TOKEN_ENV_VAR] = saved_env
    check("slack_notify: settings.json fallback resolves token when env unset",
          from_settings == "xoxb-from-settings")
    check("slack_notify: env var wins over settings.json fallback",
          env_wins == "xoxb-from-env")

    return check.failures, check.total


def _notify_mention_unit_checks() -> Tuple[int, int]:
    """The single-sourced @mention decision in slack_notify (off by default).

    Mentioning now lives in exactly one place — ``slack_notify.notify()`` — via
    two pure helpers. No caller hand-assembles ``<@U…>`` anymore.
    """
    sys.path.insert(0, str(HOOKS))
    import slack_notify  # noqa: E402

    check = _Checker()

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

    return check.failures, check.total


def _notify_classify_unit_checks() -> Tuple[int, int]:
    """Per-type icon/wording and bridge session-link parsing — the two
    deterministic pieces of the notification logic."""
    sys.path.insert(0, str(HOOKS))
    import notify_on_idle  # noqa: E402

    check = _Checker()

    # ---- classify: icon per notification_type, message passed through ----
    icon, text = notify_on_idle.classify(
        {"notification_type": "permission_prompt", "message": "Claude needs your permission"}
    )
    check("classify: permission -> bell icon + 'awaits your input'",
          icon == "🔔" and text == "Claude Code awaits your input")
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

    return check.failures, check.total


def _notify_board_link_unit_checks() -> Tuple[int, int]:
    """Fleet-Board deep-link line (fleet-config#242): _lib.resolve_board_url's
    project-override/global-fallback/unset resolution, and notify_on_idle's
    board_link() message assembly — all against synthetic registries so
    nothing touches the real projects.toml."""
    sys.path.insert(0, str(HOOKS))
    import _lib  # noqa: E402
    import notify_on_idle  # noqa: E402

    check = _Checker()

    # The real machine may have FLEET_BOARD_URL genuinely set (fleet-config#271
    # is meant to be configured this way) — clear it for the duration of these
    # checks so "unset"/"[global] fallback" expectations aren't at the mercy of
    # the ambient environment, then restore whatever was there.
    env_key = _lib.BOARD_URL_ENV_VAR
    old_env = os.environ.pop(env_key, None)
    try:
        # ---- resolve_board_url: unset -> None (byte-identical default behavior) ----
        unset = _lib.Registry(projects=[], globals=_lib.GlobalConfig(never_kill_ports=()))
        check("resolve_board_url: neither set -> None",
              _lib.resolve_board_url(Path("E:/does/not/match"), registry=unset) is None)

        # ---- resolve_board_url: [global] fallback ----
        glob_only = _lib.Registry(
            projects=[], globals=_lib.GlobalConfig(never_kill_ports=(), board_url="https://global.example:8445"),
        )
        check("resolve_board_url: [global] fallback",
              _lib.resolve_board_url(Path("E:/does/not/match"), registry=glob_only) == "https://global.example:8445")

        # ---- resolve_board_url: per-project override wins ----
        proj = _lib.ProjectConfig(
            name="x", cwd_prefix=Path("E:/automation/x"), webapp_port=None,
            tray_cmd=None, restart_cmd=None,
            api_version_path=None, extra={"board_url": "https://proj.example:8445"},
        )
        reg = _lib.Registry(
            projects=[proj],
            globals=_lib.GlobalConfig(never_kill_ports=(), board_url="https://global.example:8445"),
        )
        check("resolve_board_url: per-project override wins over [global]",
              _lib.resolve_board_url(Path("E:/automation/x"), registry=reg) == "https://proj.example:8445")

        # ---- resolve_board_url: FLEET_BOARD_URL env var precedence (fleet-config#271) ----
        # public-repo-safe indirection: env var sits between the project override
        # and the committed [global] fallback.
        os.environ[env_key] = "https://env.example:8445"
        check("resolve_board_url: env var alone -> resolves",
              _lib.resolve_board_url(Path("E:/does/not/match"), registry=unset) == "https://env.example:8445")
        check("resolve_board_url: env var wins over [global]",
              _lib.resolve_board_url(Path("E:/does/not/match"), registry=glob_only) == "https://env.example:8445")
        check("resolve_board_url: per-project override still wins over env var",
              _lib.resolve_board_url(Path("E:/automation/x"), registry=reg) == "https://proj.example:8445")
        os.environ.pop(env_key, None)

        # ---- board_link: configured + session_id -> mrkdwn deep link ----
        payload = {"session_id": "abc-123", "cwd": "E:/automation/x"}
        check("board_link: configured -> Slack mrkdwn deep link",
              notify_on_idle.board_link(payload, registry=reg)
              == "📋 <https://proj.example:8445/?board=abc-123|Open on the Board>")

        # ---- board_link: trailing slash on board_url is stripped ----
        trailing = _lib.Registry(
            projects=[], globals=_lib.GlobalConfig(never_kill_ports=(), board_url="https://global.example:8445/"),
        )
        check("board_link: trailing slash on board_url stripped",
              notify_on_idle.board_link(payload, registry=trailing)
              == "📋 <https://global.example:8445/?board=abc-123|Open on the Board>")

        # ---- board_link: board_url with an existing query string merges, not concatenates (fleet-config#273) ----
        tokened = _lib.Registry(
            projects=[], globals=_lib.GlobalConfig(never_kill_ports=(), board_url="https://global.example:8445?token=secret"),
        )
        check("board_link: existing ?token= on board_url survives alongside ?board=",
              notify_on_idle.board_link(payload, registry=tokened)
              == "📋 <https://global.example:8445/?token=secret&board=abc-123|Open on the Board>")

        # ---- board_link: unconfigured -> None (default, current behavior unchanged) ----
        check("board_link: board_url unset -> None",
              notify_on_idle.board_link(payload, registry=unset) is None)

        # ---- board_link: missing session_id -> None, even when configured ----
        check("board_link: missing session_id -> None",
              notify_on_idle.board_link({"cwd": "E:/automation/x"}, registry=reg) is None)
    finally:
        if old_env is None:
            os.environ.pop(env_key, None)
        else:
            os.environ[env_key] = old_env

    return check.failures, check.total


def _notify_chief_routing_unit_checks() -> Tuple[int, int]:
    """`is_chief_managed`/`parse_chief_sid` — the pure decision logic behind
    routing a chief-dispatched worker's blocked-on-input notification to
    chief instead of Slack (fleet-config#443).

    Deliberately does NOT exercise `notify_chief`'s live subprocess/network
    call here (or via a `run()` end-to-end hook invocation with a genuinely
    chief-managed sid): doing so would require a real `chief-managed.json`
    entry and could actually shell out to `chief_ops.py chief-sid`/`say`
    against whatever launcher happens to be listening on 127.0.0.1:8445 on
    the machine running this suite — risking a real post into a real live
    chief session as a side effect of a unit test. The two pure functions
    below are the entire decision surface; the I/O wrapper composing them is
    exercised by hand against a real launcher, the same way `chief_ops.py`'s
    own network-touching CLI commands are.
    """
    sys.path.insert(0, str(HOOKS))
    import notify_on_idle  # noqa: E402

    check = _Checker()

    # ---- is_chief_managed: file-based, fully isolated from the real state dir ----
    tmp = Path(tempfile.mkdtemp(prefix="chief_managed_route_"))
    try:
        target = tmp / "chief-managed.json"
        check("is_chief_managed: missing state file -> False",
              notify_on_idle.is_chief_managed("sid-1", path=target) is False)

        target.write_text(json.dumps({"sid-1": {"repo": "app-launcher", "number": 528,
                                                  "dispatched_at": "2026-07-27T12:00:00Z"}}),
                           encoding="utf-8")
        check("is_chief_managed: marked sid -> True",
              notify_on_idle.is_chief_managed("sid-1", path=target) is True)
        check("is_chief_managed: unrelated sid -> False",
              notify_on_idle.is_chief_managed("sid-2", path=target) is False)

        target.write_text("{not json", encoding="utf-8")
        check("is_chief_managed: corrupt state file -> False (no crash)",
              notify_on_idle.is_chief_managed("sid-1", path=target) is False)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # ---- parse_chief_sid: pure stdout-line parsing ----
    check("parse_chief_sid: CHIEF_SID=<sid> -> the sid",
          notify_on_idle.parse_chief_sid("CHIEF_SID=abc-123\n") == "abc-123")
    check("parse_chief_sid: CHIEF_SID=none -> empty (no chief live)",
          notify_on_idle.parse_chief_sid("CHIEF_SID=none\n") == "")
    check("parse_chief_sid: no matching line -> empty",
          notify_on_idle.parse_chief_sid("some other output\n") == "")
    check("parse_chief_sid: line among other output -> still extracted",
          notify_on_idle.parse_chief_sid("noise\nCHIEF_SID=xyz-789\nmore noise\n") == "xyz-789")

    return check.failures, check.total


def _notify_complete_unit_checks() -> Tuple[int, int]:
    """Canonical per-kind message assembly + the shared slack-target resolver."""
    sys.path.insert(0, str(HOOKS))
    import notify_complete  # noqa: E402
    import _lib  # noqa: E402

    check = _Checker()

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
    check("build: design -> design sweep + summary",
          bm("design", summary="8 swept · 3 drifted · 11 findings filed")
          == "🎨 Design sweep — 8 swept · 3 drifted · 11 findings filed")
    check("build: design with no summary degrades cleanly",
          bm("design") == "🎨 Design sweep")
    check("category: design routes to the activity log, not attention",
          notify_complete.category_for("design") == "log")
    check("build: learning -> log + summary + comment link",
          bm("learning", summary="12 PRs / 8 issues · 2/3 horizon", url="http://gh/c")
          == "📓 Learning log — 12 PRs / 8 issues · 2/3 horizon · http://gh/c")
    check("build: learning with no url degrades cleanly",
          bm("learning", summary="quiet week") == "📓 Learning log — quiet week")
    check("build: finish-batch -> merged + blocked counts",
          bm("finish-batch", merged="4", blocked="1") == "🏁 Finished batch: 4 merged, 1 blocked")
    check("build: finish-batch (0 blocked) drops the blocked clause",
          bm("finish-batch", merged="5", blocked="0") == "🏁 Finished batch: 5 merged")
    check("build: security -> lock + summary + PR link",
          bm("security", issue="42", title="audit: security findings", url="http://pr", summary="auto-merged, review the diff")
          == "🔒 Security #42 audit: security findings — auto-merged, review the diff · http://pr")
    check("build: security with no summary defaults to review-the-diff",
          bm("security", issue="42", url="http://pr") == "🔒 Security #42 — review the diff · http://pr")

    # --summary crosses the harness -> shell -> CreateProcess boundary, which is
    # not UTF-8 safe on Windows: a literal `·` reached Slack as `??`
    # (fleet-config#507). Skills spell the separator with the ASCII token `|`,
    # and whatever mojibake is still recoverable is repaired on the way in.
    ns = notify_complete.normalize_summary
    check("normalize_summary: ASCII token renders as the middle-dot separator",
          ns("8 swept | 2 drifted | 4 findings filed") == "8 swept · 2 drifted · 4 findings filed")
    check("normalize_summary: token spacing is normalised either way",
          ns("8 swept|2 drifted") == "8 swept · 2 drifted")
    check("normalize_summary: cp1252-mangled middle-dot is repaired",
          ns("8 swept Â· 2 drifted") == "8 swept · 2 drifted")
    check("normalize_summary: an intact middle-dot survives untouched",
          ns("8 swept · 2 drifted") == "8 swept · 2 drifted")
    check("normalize_summary: plain ASCII prose is untouched",
          ns("review the diff, then /issue-finish") == "review the diff, then /issue-finish")
    check("normalize_summary: None stays None", ns(None) is None)
    check("build: design accepts the ASCII separator token",
          bm("design", summary="8 swept | 3 drifted | 11 findings filed")
          == "🎨 Design sweep — 8 swept · 3 drifted · 11 findings filed")

    rm = _lib.repair_mojibake
    check("repair_mojibake: mangled em-dash restored", rm("a â€” b") == "a — b")
    check("repair_mojibake: genuine accented prose left alone", rm("não é") == "não é")
    check("repair_mojibake: pure ASCII short-circuits", rm("plain text") == "plain text")
    check("repair_mojibake: empty/None pass through", rm("") == "" and rm(None) is None)

    # The separator token only exists because non-ASCII must not be authored into
    # an argv string — a SKILL.md (or the doc a model copies the command from)
    # that re-inlines a literal `·` puts the corruption straight back.
    # Emoji (>= U+2600) are exempt: they are the glanceable status cue, and the
    # reported corruption was of punctuation. Everything else non-ASCII is an
    # offender — separators, dashes, quotes.
    offenders: list[str] = []
    arg_text = re.compile(r'--(?:summary|text)\s+"([^"]*)"')
    sources = sorted((REPO / ".claude" / "skills").rglob("SKILL.md"))
    sources += sorted((REPO / "skills").rglob("SKILL.md"))
    sources += sorted((REPO / "docs").rglob("*.md"))
    sources += [REPO / "README.md", REPO / "CLAUDE.md", REPO / "global-CLAUDE.md"]
    for source in sources:
        if not source.is_file():
            continue
        for lineno, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
            for value in arg_text.findall(line):
                bad = sorted({c for c in value if not c.isascii() and ord(c) < 0x2600})
                if bad:
                    offenders.append(f"{source.relative_to(REPO).as_posix()}:{lineno} {bad}")
    check("skills + docs author only ASCII punctuation into --summary/--text argv"
          + (f" (offenders: {offenders})" if offenders else ""),
          not offenders)

    # The shared resolver: unknown cwd -> [global] channel/user + 'claude' name.
    ch, usr, nm = _lib.resolve_slack_target(Path("E:/does/not/match/anything"))
    check("resolve_slack_target: global fallback + claude name",
          ch == "C0B76GBA0LS" and usr == "U0B71PQEL6S" and nm == "claude")

    # lookup(): --repo threads onto the gh invocation as `-R repo`, for both the
    # issue path and the pr-by-number path, so a cross-repo ping can't silently
    # resolve against the caller's CWD repo instead (fleet-config#497).
    captured_args = []
    saved_gh_json = notify_complete.gh_json
    notify_complete.gh_json = lambda a: (captured_args.append(a), {"title": "T", "url": "http://u"})[1]
    try:
        notify_complete.lookup("add", "496", None, repo="ferraroroberto/fleet-config")
        check("lookup: issue path threads -R <repo> onto gh issue view",
              captured_args[-1] == ["issue", "view", "496", "-R", "ferraroroberto/fleet-config", "--json", "title,url"])

        notify_complete.lookup("add", "30", None)
        check("lookup: issue path omits -R when repo not supplied (CWD-relative, unchanged)",
              captured_args[-1] == ["issue", "view", "30", "--json", "title,url"])

        notify_complete.lookup("finish", None, "31", repo="ferraroroberto/fleet-config")
        check("lookup: pr-by-number path threads -R <repo> onto gh pr view",
              captured_args[-1] == ["pr", "view", "31", "-R", "ferraroroberto/fleet-config", "--json", "title,url"])

        notify_complete.lookup("finish", None, None, pr_url="http://pr", repo="ferraroroberto/fleet-config")
        check("lookup: pr_url path ignores repo (absolute URL already CWD-independent)",
              captured_args[-1] == ["pr", "view", "http://pr", "--json", "title"])
    finally:
        notify_complete.gh_json = saved_gh_json

    return check.failures, check.total


def _slack_routing_unit_checks() -> Tuple[int, int]:
    """Category → channel routing (issue #139): the resolver picks the dedicated
    channel per category, falls back to the single channel when a category is
    unset, and the kind → category map sends action-needed pings to attention."""
    sys.path.insert(0, str(HOOKS))
    import _lib  # noqa: E402
    import notify_complete  # noqa: E402

    check = _Checker()

    cwd = Path("E:/does/not/match/anything")  # global-only resolution

    # ---- category routes to its dedicated [global] channel ----
    ch, _u, _n = _lib.resolve_slack_target(cwd, category="attention")
    check("route: attention -> #attention channel", ch == "C0BAGNEQ163")
    ch, _u, _n = _lib.resolve_slack_target(cwd, category="log")
    check("route: log -> #log channel", ch == "C0BARRUBG03")
    # No category -> the plain channel (back-compat: existing callers unchanged).
    ch, _u, _n = _lib.resolve_slack_target(cwd)
    check("route: no category -> slack_notify_channel", ch == "C0B76GBA0LS")

    # ---- graceful degradation: category channels unset -> single-channel fallback ----
    single = _lib.Registry(
        projects=[],
        globals=_lib.GlobalConfig(never_kill_ports=(), slack_notify_channel="C_ONLY"),
    )
    ch, _u, _n = _lib.resolve_slack_target(cwd, registry=single, category="attention")
    check("route: unset category channel -> falls back to single channel", ch == "C_ONLY")

    # ---- per-project override of a category channel wins over [global] ----
    proj = _lib.ProjectConfig(
        name="x", cwd_prefix=Path("E:/automation/x"), webapp_port=None,
        tray_cmd=None, restart_cmd=None,
        api_version_path=None, extra={"slack_channel_log": "C_PROJ_LOG"},
    )
    reg = _lib.Registry(
        projects=[proj],
        globals=_lib.GlobalConfig(never_kill_ports=(), slack_notify_channel="C_G",
                                  slack_channel_log="C_GLOBAL_LOG"),
    )
    ch, _u, _n = _lib.resolve_slack_target(Path("E:/automation/x"), registry=reg, category="log")
    check("route: per-project category channel overrides [global]", ch == "C_PROJ_LOG")

    # ---- kind -> category map ----
    cat = notify_complete.category_for
    check("category_for: start -> attention", cat("start") == "attention")
    check("category_for: batch -> attention", cat("batch") == "attention")
    check("category_for: security -> attention", cat("security") == "attention")
    check("category_for: cleanup with review>0 -> attention", cat("cleanup", review="2") == "attention")
    check("category_for: cleanup with review=0 -> log", cat("cleanup", review="0") == "log")
    check("category_for: log kinds -> log",
          all(cat(k) == "log" for k in ("add", "finish", "yolo", "audit", "recap", "learning", "finish-batch")))

    return check.failures, check.total
