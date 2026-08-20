"""Acceptance checks for the record-what-happened hooks (fleet-config#680).

The three that write a durable artefact after the fact: `conversation_capture`'s
session dedup, `conversation_index`'s config-driven routing + indexing, and
`work_summary`'s deterministic PR roll-up (file/LOC table, no LLM, no `gh` in
the test).

Split out of the former 2681-line `unit_checks.py`; see `checks_context_filter`
for why.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Tuple

from acceptance.shared import (
    HOOKS,
    _Checker,
)

# Every function below inserts its own sys.path entry (HOOKS or skills/_lib)
# right before its dynamic import -- matches the pre-split file's per-function
# style, so each check's dependency is visible at its own call site.


def _conversation_capture_unit_checks() -> Tuple[int, int]:
    """The per-session dedup logic: stable token, filename shape, and the
    supersede-prior sweep that collapses a session's many Stop captures to one."""
    sys.path.insert(0, str(HOOKS))
    import conversation_capture as cc  # noqa: E402
    import _lib  # noqa: E402

    check = _Checker()

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

    # _trigger_delayed_index: the near-close trigger (fleet-config#673) — spawns
    # conversation_index.py detached, with the project name and a delay, and
    # never lets a spawn failure raise out of the Stop hook.
    captured: dict = {}
    saved_popen = cc.subprocess.Popen
    cc.subprocess.Popen = lambda argv, **kw: captured.update(argv=argv, kw=kw)
    try:
        cc._trigger_delayed_index("life-os")
    finally:
        cc.subprocess.Popen = saved_popen
    argv = captured.get("argv", [])
    check("_trigger_delayed_index: spawns conversation_index.py for the project",
          str(cc.INDEXER) in argv and "--project" in argv and "life-os" in argv)
    check("_trigger_delayed_index: passes --delay-seconds",
          "--delay-seconds" in argv
          and float(argv[argv.index("--delay-seconds") + 1]) == cc._INDEX_DELAY_SECONDS)
    check("_trigger_delayed_index: detached, creationflags carries NO_WINDOW",
          captured.get("kw", {}).get("stdout") is cc.subprocess.DEVNULL
          and captured.get("kw", {}).get("creationflags") == _lib.NO_WINDOW)

    saved_popen = cc.subprocess.Popen
    def _raise(*_a, **_kw):
        raise OSError("spawn refused")
    cc.subprocess.Popen = _raise
    try:
        cc._trigger_delayed_index("life-os")  # must not raise (fail-open)
        check("_trigger_delayed_index: a spawn failure is swallowed, not raised", True)
    except OSError:
        check("_trigger_delayed_index: a spawn failure is swallowed, not raised", False)
    finally:
        cc.subprocess.Popen = saved_popen

    return check.failures, check.total


def _conversation_index_unit_checks() -> Tuple[int, int]:
    """Config-driven capture routing + the indexer's digest/upsert/decay logic.

    Hermetic: the hub is stubbed, so no network is touched. Covers the opt-in
    gate (a non-registered project captures nothing), routing resolution, and
    the index round-trip including the preserved decay zone."""
    sys.path.insert(0, str(HOOKS))
    import conversation_capture as cc  # noqa: E402
    import conversation_index as ci  # noqa: E402
    import hub_client  # noqa: E402

    check = _Checker()

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

    # apply_delay: the Stop-triggered near-close run's sleep, stubbed so the
    # acceptance suite never actually waits (fleet-config#673).
    sleeps: list = []
    saved_sleep = ci.time.sleep
    ci.time.sleep = lambda s: sleeps.append(s)
    try:
        ci.apply_delay(60.0)
        check("apply_delay: seconds > 0 -> sleeps that long", sleeps == [60.0])
        ci.apply_delay(0.0)
        check("apply_delay: 0 -> no sleep (SessionStart trigger never delays)", sleeps == [60.0])
    finally:
        ci.time.sleep = saved_sleep

    return check.failures, check.total


def _work_summary_unit_checks() -> Tuple[int, int]:
    """The work-summary roll-up block + per-file table (hooks/work_summary.py).

    Pure / no gh: feed the formatters a synthetic ``gh pr view`` payload (an
    added/modified/renamed/deleted mix) and assert the exact rendered roll-up and
    table, the empty-bucket drop, and the no-files degrade-to-empty path that
    keeps a finish ping block-less instead of crashing."""
    sys.path.insert(0, str(HOOKS))
    import work_summary as ws  # noqa: E402

    check = _Checker()

    M = ws.MINUS  # U+2212, as the formatters emit

    # changeType -> bucket, source-agnostic (GraphQL DELETED + REST removed both deleted).
    check("work_summary: bucket_for maps add/copy->new, del/removed->deleted, else->changed",
          ws.bucket_for("ADDED") == "new" and ws.bucket_for("COPIED") == "new"
          and ws.bucket_for("DELETED") == "deleted" and ws.bucket_for("removed") == "deleted"
          and ws.bucket_for("MODIFIED") == "changed" and ws.bucket_for("RENAMED") == "changed"
          and ws.bucket_for(None) == "changed")

    # Consistent synthetic PR: 2 new (+210), 2 changed (+98 -40), 1 deleted (-7).
    data = {
        "additions": 308, "deletions": 47, "changedFiles": 5,
        "files": [
            {"path": "a_new.py", "additions": 110, "deletions": 0, "changeType": "ADDED"},
            {"path": "b_new.py", "additions": 100, "deletions": 0, "changeType": "ADDED"},
            {"path": "c_mod.py", "additions": 50, "deletions": 30, "changeType": "MODIFIED"},
            {"path": "d_ren.py", "additions": 48, "deletions": 10, "changeType": "RENAMED"},
            {"path": "e_del.py", "additions": 0, "deletions": 7, "changeType": "DELETED"},
        ],
    }
    check("work_summary: format_block renders the exact roll-up",
          ws.format_block(data) ==
          f"📊 +308 {M}47 · 5 files\n"
          f"   🆕 2 new (+210)  ✏️ 2 changed (+98 {M}40)  🗑️ 1 deleted ({M}7)")

    check("work_summary: format_table is churn-sorted with status icons",
          ws.format_table(data) ==
          "| | File | + | − |\n"
          "|---|---|--:|--:|\n"
          f"| 🆕 | `a_new.py` | +110 | {M}0 |\n"
          f"| 🆕 | `b_new.py` | +100 | {M}0 |\n"
          f"| ✏️ | `c_mod.py` | +50 | {M}30 |\n"
          f"| ✏️ | `d_ren.py` | +48 | {M}10 |\n"
          f"| 🗑️ | `e_del.py` | +0 | {M}7 |")

    # Single modified file: empty new/deleted buckets dropped, singular "1 file".
    one = {"additions": 8, "deletions": 1, "changedFiles": 1,
           "files": [{"path": "x.py", "additions": 8, "deletions": 1, "changeType": "MODIFIED"}]}
    check("work_summary: empty buckets dropped + singular 'file'",
          ws.format_block(one) == f"📊 +8 {M}1 · 1 file\n   ✏️ 1 changed (+8 {M}1)")

    # Degrade path: no files (or a {} from a failed gh call) → "" both renderings.
    check("work_summary: no files → empty block and empty table",
          ws.format_block({}) == "" and ws.format_table({}) == ""
          and ws.format_block({"files": []}) == "")

    return check.failures, check.total
