"""Acceptance checks for skill-tier helpers (fleet-config#680).

The pure logic behind two skills rather than behind a hook:
`.claude/skills/learning-log/report.py` and `skills/_lib`'s
`restart_and_verify_webapp` (restart-strategy selection + the recovery hint an
operator reads when the poll never confirms the new build).

Split out of the former 2681-line `unit_checks.py`; see `checks_context_filter`
for why.
"""
from __future__ import annotations

import contextlib
import io
import re
import sys
from pathlib import Path
from typing import Any, Tuple

from acceptance.shared import (
    HOOKS,
    REPO,
    _Checker,
)

# Every function below inserts its own sys.path entry (HOOKS or skills/_lib)
# right before its dynamic import -- matches the pre-split file's per-function
# style, so each check's dependency is visible at its own call site.


def _learning_log_unit_checks() -> Tuple[int, int]:
    """The pure window / section / bucketing / stats / ledger logic of
    learning-log/gather.py.

    No gh, no sub-agents — exercises last-run-at parsing, window resolution,
    section slicing, work-type bucketing, the exact stats computation + table
    render, and the archive-growing ledger body the unattended weekly run
    depends on."""
    import datetime as dt

    sys.path.insert(0, str(REPO / ".claude" / "skills" / "learning-log"))
    import gather as ll  # noqa: E402

    check = _Checker()

    # ---- last-run-at parsing ----
    check("learning_log: parse_last_run reads the stamp",
          ll.parse_last_run("<!-- learning-log-state -->\nlast-run-at: 2026-06-12\n") == "2026-06-12")
    check("learning_log: parse_last_run absent -> None",
          ll.parse_last_run("no stamp here") is None)

    # ---- window resolution: (since, source) — arg > ledger > trailing 7d ----
    today = dt.date(2026, 6, 15)
    check("learning_log: resolve_since explicit arg wins",
          ll.resolve_since("2026-05-01", "last-run-at: 2026-06-12", today) == ("2026-05-01", "arg"))
    check("learning_log: resolve_since falls to ledger last-run-at",
          ll.resolve_since(None, "last-run-at: 2026-06-12", today) == ("2026-06-12", "ledger"))
    check("learning_log: resolve_since first run -> trailing 7d",
          ll.resolve_since(None, "", today) == ("2026-06-08", "default"))

    # ---- section slicing ----
    text = "## TL;DR\n- a\n- b\n\n## Horizon → next week\n- [ ] x\n- [ ] y\n"
    check("learning_log: slice_section bounded by next H2",
          ll.slice_section(text, "## TL;DR") == "- a\n- b")
    check("learning_log: slice_section missing header -> ''",
          ll.slice_section(text, "## Nope") == "")

    # ---- discovery bullets get dated, non-bullets dropped ----
    bullets = ll.dated_discovery_bullets("- learned X (repo#1)\n- learned Y (repo#2)\nnoise", "2026-06-15")
    check("learning_log: dated_discovery_bullets dates + drops non-bullets",
          bullets == ["- 2026-06-15: learned X (repo#1)", "- 2026-06-15: learned Y (repo#2)"])

    # ---- work-type bucketing: PR title prefix, issue label ----
    check("learning_log: pr_bucket maps feat -> Features, fix -> Bug fixes, unknown -> Other",
          ll.pr_bucket("feat(api)!: x") == "Features & enhancements"
          and ll.pr_bucket("fix: y") == "Bug fixes"
          and ll.pr_bucket("random title") == "Other")
    check("learning_log: issue_bucket maps by label, none -> Other",
          ll.issue_bucket(["bug"]) == "Bug fixes"
          and ll.issue_bucket(["enhancement"]) == "Features & enhancements"
          and ll.issue_bucket([]) == "Other")

    # ---- exact stats: per-repo + per-bucket + grand total ----
    prs = [
        {"repo": "a", "bucket": "Bug fixes", "additions": 10, "deletions": 2},
        {"repo": "a", "bucket": "Features & enhancements", "additions": 5, "deletions": 0},
        {"repo": "b", "bucket": "Bug fixes", "additions": 3, "deletions": 1},
    ]
    issues = [{"repo": "a", "bucket": "Bug fixes"}, {"repo": "b", "bucket": "Other"}]
    stats = ll.compute_stats(prs, issues)
    check("learning_log: compute_stats grand totals (PRs/issues/LOC)",
          stats["total"] == {"prs": 3, "issues": 2, "add": 18, "del": 3})
    check("learning_log: compute_stats per-repo + per-bucket counts",
          stats["repos"]["a"]["prs"] == 2 and stats["repos"]["a"]["issues"] == 1
          and stats["repos"]["a"]["add"] == 15
          and stats["buckets"]["Bug fixes"]["prs"] == 2)
    table = ll.render_stats(stats, "2026-05-01", "2026-06-15")
    check("learning_log: render_stats has TOTAL row, a repo row, and a bucket row",
          "**TOTAL**" in table and "| a |" in table and "Bug fixes" in table)

    # ---- ledger body: new stamp + horizon, new discoveries prepended, old preserved ----
    prior = ("<!-- learning-log-state -->\nlast-run-at: 2026-06-08\n\n"
             "## Horizon → next week (set 2026-06-08)\n- [ ] old item\n\n"
             "## Decision / discovery archive\n- 2026-06-08: prior learning (repo#9)\n")
    body = ll.build_ledger_body(prior, "2026-06-15",
                                "- [ ] new horizon a\n- [ ] new horizon b",
                                "- fresh learning (repo#3)")
    check("learning_log: build_ledger_body stamps new last-run-at",
          "last-run-at: 2026-06-15" in body)
    check("learning_log: build_ledger_body carries the next horizon",
          "- [ ] new horizon a" in body and "## Horizon → next week (set 2026-06-15)" in body)
    check("learning_log: build_ledger_body prepends new discovery, preserves prior archive",
          "- 2026-06-15: fresh learning (repo#3)" in body
          and "- 2026-06-08: prior learning (repo#9)" in body
          and body.index("2026-06-15: fresh") < body.index("2026-06-08: prior learning"))

    # ---- archive windowing: dedupe on content, cap total (fleet-config#732) ----
    check("learning_log: window_archive dedupes same content across dates, keeps newest",
          ll.window_archive(["- 2026-06-15: same thing (repo#1)"],
                            ["- 2026-06-08: same thing (repo#1)", "- 2026-06-01: other (repo#2)"])
          == ["- 2026-06-15: same thing (repo#1)", "- 2026-06-01: other (repo#2)"])
    big_prior = [f"- 2026-01-{d:02d}: item {d} (repo#{d})" for d in range(1, 30)]
    windowed = ll.window_archive([], big_prior, cap=10)
    check("learning_log: window_archive caps to the newest N bullets",
          len(windowed) == 10 and windowed == big_prior[:10])
    check("learning_log: build_ledger_body archive never exceeds ARCHIVE_CAP bullets",
          len(ll.dated_discovery_bullets("- x\n", "2026-06-15") + [f"- 2026-01-{d:02d}: item {d}" for d in range(1, 200)])
          > ll.ARCHIVE_CAP
          and ll.build_ledger_body(
              "<!-- learning-log-state -->\nlast-run-at: 2026-06-08\n\n"
              "## Horizon → next week (set 2026-06-08)\n- [ ] old item\n\n"
              "## Decision / discovery archive\n"
              + "\n".join(f"- 2026-01-{d:02d}: item {d} (repo#{d})" for d in range(1, 200)) + "\n",
              "2026-06-15", "- [ ] h", "- fresh (repo#9)"
          ).count("\n- 2026-") <= ll.ARCHIVE_CAP)

    # ---- a failed gh read is unknown, never "0 PRs" ----
    import argparse
    import contextlib
    import io
    import subprocess as sp
    import tempfile

    saved = (ll._gh_json, ll.read_ledger_body, ll.git_run.run_gh)
    try:
        ll.git_run.run_gh = lambda args, **kw: sp.CompletedProcess(args, 1, "", "boom")
        check("learning_log: _gh_json returns None (not []) when gh exits non-zero",
              ll._gh_json(["pr", "list"]) is None)
        ll.git_run.run_gh = lambda args, **kw: sp.CompletedProcess(args, 0, "[]", "")
        check("learning_log: _gh_json still returns [] for a real empty answer",
              ll._gh_json(["pr", "list"]) == [])

        ll._gh_json = lambda args: None
        check("learning_log: list_repos returns None when enumeration fails",
              ll.list_repos("o") is None)
        check("learning_log: gather_repo returns None when a gh read fails",
              ll.gather_repo("o", "r", "2026-01-01") is None)

        ll.read_ledger_body = lambda repo: ""
        gargs = argparse.Namespace(owner="o", repo="o/ledger", since="2026-01-01",
                                   out_dir=tempfile.mkdtemp(prefix="ll_gather_"))
        with contextlib.redirect_stderr(io.StringIO()):
            check("learning_log: cmd_gather exits non-zero when repo enumeration fails",
                  ll.cmd_gather(gargs) != 0)

        def _fake(args):
            if args[0] == "repo":
                return [{"name": "good"}, {"name": "bad"}]
            if "o/bad" in args:
                return None
            return []
        ll._gh_json = _fake
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            code = ll.cmd_gather(gargs)
        out = buf.getvalue()
        check("learning_log: a failed repo is its own manifest state, named, not counted as zero",
              code == 0 and "FAILED_REPOS=bad" in out and "failed_repos=1" in out
              and "Incomplete: 1 of 2 repos" in out)

        # ---- a gh list that fills its --limit is a possibly truncated window,
        # said once on stderr; below the cap says nothing new (fleet-config#1295) ----
        cap = ll.PER_REPO_LIST_LIMIT
        old = {"number": 1, "title": "x", "mergedAt": "2020-01-01", "closedAt": "2020-01-01"}
        ll._gh_json = lambda args: [old] * (cap if args[0] == "pr" else cap - 1)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            ll.gather_repo("o", "r", "2026-01-01")
        lines = [ln for ln in err.getvalue().splitlines() if ln.strip()]
        check("learning_log: a PR list at PER_REPO_LIST_LIMIT warns once, naming repo, list and cap",
              len(lines) == 1 and "o/r" in lines[0] and "merged PRs" in lines[0] and str(cap) in lines[0],
              f"stderr: {err.getvalue()!r}")

        ll._gh_json = lambda args: [old] * (cap - 1)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            ll.gather_repo("o", "r", "2026-01-01")
        check("learning_log: lists below the cap print nothing", err.getvalue() == "",
              f"stderr: {err.getvalue()!r}")

        ll._gh_json = lambda args: [{"name": f"r{n}"} for n in range(ll.REPO_LIST_LIMIT)]
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            ll.list_repos("o")
        check("learning_log: a repo list at REPO_LIST_LIMIT warns, naming owner, list and cap",
              "o" in err.getvalue() and "repo list" in err.getvalue()
              and str(ll.REPO_LIST_LIMIT) in err.getvalue(),
              f"stderr: {err.getvalue()!r}")
    finally:
        ll._gh_json, ll.read_ledger_body, ll.git_run.run_gh = saved

    # ---- a failed prior-ledger read must never look like "no ledger yet"
    # to a caller about to overwrite it (fleet-config#1061) ----
    saved_run_audit = ll.run_audit_issue
    try:
        ll.run_audit_issue = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        threw = False
        try:
            ll.read_ledger_body("o/ledger", required=True)
        except RuntimeError:
            threw = True
        check("learning_log: read_ledger_body(required=True) re-raises instead of swallowing",
              threw)
        with contextlib.redirect_stderr(io.StringIO()):
            check("learning_log: read_ledger_body(required=False) still swallows, returns ''",
                  ll.read_ledger_body("o/ledger") == "")
    finally:
        ll.run_audit_issue = saved_run_audit

    saved_read = ll.read_ledger_body
    try:
        ll.read_ledger_body = lambda repo, **kw: (_ for _ in ()).throw(RuntimeError("boom"))
        out_path = Path(tempfile.mkdtemp(prefix="ll_assemble_")) / "ledger-body.md"
        aargs = argparse.Namespace(repo="o/ledger", horizon_file=None, discoveries_file=None,
                                    out=str(out_path))
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            code = ll.cmd_assemble_ledger(aargs)
        check("learning_log: cmd_assemble_ledger exits non-zero and writes nothing when the "
              "prior ledger cannot be read, instead of publishing an archive built from an "
              "empty prior",
              code != 0 and not out_path.exists())

        ll.read_ledger_body = lambda repo, **kw: (
            "<!-- learning-log-state -->\nlast-run-at: 2026-06-08\n\n"
            "## Decision / discovery archive\n- 2026-06-08: prior learning (repo#9)\n")
        with contextlib.redirect_stdout(io.StringIO()):
            code = ll.cmd_assemble_ledger(aargs)
        check("learning_log: cmd_assemble_ledger still writes the body when the prior ledger "
              "read succeeds",
              code == 0 and out_path.exists()
              and "2026-06-08: prior learning (repo#9)" in out_path.read_text(encoding="utf-8"))
    finally:
        ll.read_ledger_body = saved_read

    return check.failures, check.total


def _restart_webapp_unit_checks() -> Tuple[int, int]:
    """The tray-owned restart strategy: projects.toml carries a `restart_cmd`
    for the three tray apps, and the recovery hint stays actionable and
    :8446-safe. Both are pure (no tray needed), so they're gate-testable."""
    sys.path.insert(0, str(HOOKS))
    import restart_and_verify_webapp as rw  # noqa: E402
    import _lib  # noqa: E402

    check = _Checker()

    reg = _lib.load_registry()
    by_name = {p.name: p for p in reg.projects}

    check("restart_cmd: app-launcher respawns through WebappManager",
          "WebappManager" in (by_name["app-launcher"].restart_cmd or ""))
    check("restart_cmd: voice-transcriber now has webapp_port 8443 + respawn cmd",
          by_name["voice-transcriber"].webapp_port == 8443
          and "WebappManager" in (by_name["voice-transcriber"].restart_cmd or ""))
    check("restart_cmd: local-llm-hub keeps the tray_cmd path (no restart_cmd)",
          by_name["local-llm-hub"].restart_cmd is None)

    webapps = [project for project in reg.projects if project.webapp_port is not None]
    check("browser target: every webapp declares an HTTP/HTTPS scheme",
          all(project.extra.get("browser_scheme") in {"http", "https"} for project in webapps))
    check("browser target: local hub stays plain HTTP",
          by_name["local-llm-hub"].extra.get("browser_scheme") == "http")
    check("browser target: TLS fleet apps declare HTTPS",
          all(by_name[name].extra.get("browser_scheme") == "https" for name in (
              "app-launcher", "photo-ocr", "voice-transcriber", "home-automation",
              "grocery-shopping-automation", "task-os", "whatsapp-radar",
          )))

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

    captured = {}
    saved_popen = rw.subprocess.Popen
    rw.subprocess.Popen = lambda *a, **kw: captured.update(kw)
    try:
        rw._start_tray("tray.bat", Path("E:/automation/app-launcher"))
    finally:
        rw.subprocess.Popen = saved_popen
    flags = captured.get("creationflags", 0)
    check(
        "_start_tray: creationflags carries both CREATE_NEW_PROCESS_GROUP and "
        "CREATE_NO_WINDOW (fleet-config#409)",
        bool(flags & getattr(rw.subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        and bool(flags & getattr(rw.subprocess, "CREATE_NO_WINDOW", 0)),
    )

    # ---- build identity is confirmed, never assumed (fleet-config#562) ----
    # `expected.startswith("")` is unconditionally True, so the old predicate
    # reported a payload carrying no git_sha as `OK git_sha= (matches HEAD)`.
    head = "a1b2c3d4e5f6a7b8"
    check("sha_matches: empty got_sha never matches (the #562 false-verify)",
          not rw.sha_matches(head, ""))
    check("sha_matches: empty expected_sha never matches",
          not rw.sha_matches("", "a1b2c3d"))
    check("sha_matches: a 7-char prefix matches in either direction",
          rw.sha_matches(head, "a1b2c3d") and rw.sha_matches("a1b2c3d", head))
    check("sha_matches: a different sha does not match",
          not rw.sha_matches(head, "9999999abc"))

    def drive(payload: Any, git_head: Any = head) -> int:
        """Run main() end-to-end with the port/restart/HTTP layer stubbed out —
        proves the *exit code*, not just the predicate."""
        saved = (rw._pid_on_port, rw._restart_via_cmd, rw._git_head, rw._fetch_version,
                 rw.VERIFY_TIMEOUT_S, rw.POLL_INTERVAL_S, sys.argv)
        rw._pid_on_port = lambda port: None
        rw._restart_via_cmd = lambda cmd, cwd, port: True
        rw._git_head = lambda cwd: git_head
        rw._fetch_version = lambda port, path, timeout=2.0: payload
        rw.VERIFY_TIMEOUT_S, rw.POLL_INTERVAL_S = 0.15, 0.05
        sys.argv = ["restart_and_verify_webapp", "--cwd", "E:/automation/app-launcher"]
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                return rw.main()
        finally:
            (rw._pid_on_port, rw._restart_via_cmd, rw._git_head, rw._fetch_version,
             rw.VERIFY_TIMEOUT_S, rw.POLL_INTERVAL_S, sys.argv) = saved

    check("restart verify: git_sha matching HEAD -> exit 0",
          drive({"git_sha": head, "asset_hash": "deadbeef"}) == 0)
    check("restart verify: payload with NO git_sha key -> exit 3 (unconfirmed, not success)",
          drive({"asset_hash": "deadbeef"}) == 3)
    check("restart verify: payload with an empty git_sha -> exit 3 (unconfirmed)",
          drive({"git_sha": "", "asset_hash": "deadbeef"}) == 3)
    check("restart verify: payload with a null git_sha -> exit 3 (unconfirmed)",
          drive({"git_sha": None}) == 3)
    check("restart verify: HEAD unreadable -> exit 3 (nothing to compare against)",
          drive({"git_sha": head}, git_head=None) == 3)
    check("restart verify: a real sha that never converges -> exit 2 (mismatch, distinct from 3)",
          drive({"git_sha": "9999999abcdef"}) == 2)
    check("restart verify: endpoint never answers -> exit 2",
          drive(None) == 2)

    return check.failures, check.total


def _scheduled_skill_timeouts_named_check() -> Tuple[int, int]:
    """The unattended weekly skills' `gh`/`git`/hub timeouts are named, not bare (fleet-config#1306).

    A timeout is the only thing between a hung call and a stalled `claude -p`
    run, and a bare `timeout=120` does not say whether it is measured, an API
    limit or a guess - so nobody dares tune it. Each is a named constant
    carrying a one-clause reason; this pins the four files that regressed.
    """
    check = _Checker()
    bare = re.compile(r"\btimeout\s*=\s*\d")
    for rel in (
        ".claude/skills/learning-log/gather.py",
        ".claude/skills/context-purge/digest.py",
        ".claude/skills/context-purge/gate.py",
        ".claude/skills/insights-weekly/report.py",
    ):
        text = (REPO / rel).read_text(encoding="utf-8")
        hits = [ln.strip() for ln in text.splitlines() if bare.search(ln) and not ln.lstrip().startswith("#")]
        check(f"timeouts: {rel} carries no bare numeric timeout", not hits, "; ".join(hits))

    return check.failures, check.total
