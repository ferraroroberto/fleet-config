"""Unit tests for the stored-transcript backfill CLI (fleet-config#785).

Covers the concrete acceptance criteria from #785: a Codex session with no
``.active-skill`` marker routes by transcript inference instead of falling to
``_archive``; the backfilled filename is stamped from the transcript's own
recorded start rather than the recovery run's wall clock; re-running the
backfill over an already-captured session is a no-op; and discovery is scoped
to the target project's own ``cwd`` (an unrelated project's transcript, even
sharing a store, is never captured).

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/test_conversation_backfill.py`
(also invoked by tests/run_acceptance.py)
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "hooks"))
import _lib  # noqa: E402
import conversation_backfill as cb  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check

SID_CODEX = "01a07678-8bc3-7973-bd6e-15d708b9075b"
SID_UNRELATED = "01a07678-8d72-7112-bba9-487a6c2bc823"
SID_CLAUDE = "44444444-4444-4444-8444-444444444444"

tmp = Path(tempfile.mkdtemp(prefix="conv_backfill_"))
saved_claude_env = _lib.os.environ.get("CLAUDE_TRANSCRIPTS_DIR")
saved_codex_env = _lib.os.environ.get("CODEX_SESSIONS_DIR")
try:
    root = tmp / "testproj"
    other_root = tmp / "otherproj"
    (root / ".claude" / "skills" / "demo-skill").mkdir(parents=True)
    (root / ".claude" / "skills" / "probe").mkdir(parents=True)
    other_root.mkdir(parents=True)

    claude_store = tmp / "claude-projects"
    codex_store = tmp / "codex-sessions"
    claude_store.mkdir()
    codex_store.mkdir()

    toml_path = tmp / "projects.toml"
    toml_path.write_text(
        f'[testproj]\n'
        f'cwd_prefix = "{str(root).replace(chr(92), "/")}"\n'
        f'capture = true\n'
        f'capture_harnesses = ["claude", "codex"]\n'
        f'capture_routing = "skills"\n'
        f'skills_dir = ".claude/skills"\n'
        f'conversations_dir = "conversations"\n'
        f'active_marker = ".active-skill"\n'
        f'\n[otherproj]\n'
        f'cwd_prefix = "{str(other_root).replace(chr(92), "/")}"\n',
        encoding="utf-8",
    )
    registry = _lib.load_registry(path=toml_path)
    project = next(p for p in registry.projects if p.name == "testproj")
    cfg = cb.capture_config_from_project(project)
    check(cfg is not None and cfg.routing == "skills", "fixture: testproj resolves to skills routing")

    _lib.os.environ["CLAUDE_TRANSCRIPTS_DIR"] = str(claude_store)
    _lib.os.environ["CODEX_SESSIONS_DIR"] = str(codex_store)

    # ---- a Codex rollout mentioning a known skill by path, no marker at all ----
    codex_dir = codex_store / "2026" / "08" / "01"
    codex_dir.mkdir(parents=True)
    codex_records = [
        {"type": "session_meta", "timestamp": "2026-08-01T08:59:00Z",
         "payload": {"id": SID_CODEX, "cwd": str(root), "cli_version": "0.153.4",
                     "originator": "codex_exec"}},
        {"type": "event_msg", "timestamp": "2026-08-01T09:00:00Z",
         "payload": {"type": "item_completed", "thread_id": SID_CODEX, "turn_id": "t1",
                     "item": {"type": "UserMessage", "id": "u1", "content": [
                         {"type": "text",
                          "text": "Use the demo-skill skill from .claude/skills/demo-skill/SKILL.md."}]}}},
        {"type": "event_msg", "timestamp": "2026-08-01T09:00:05Z",
         "payload": {"type": "item_completed", "thread_id": SID_CODEX, "turn_id": "t1",
                     "item": {"type": "AgentMessage", "id": "a1", "content": [
                         {"type": "text", "text": "Logged the demo entry."}]}}},
    ]
    codex_path = codex_dir / f"rollout-2026-08-01T08-59-00-{SID_CODEX}.jsonl"
    codex_path.write_text("\n".join(json.dumps(r) for r in codex_records) + "\n", encoding="utf-8")

    # An unrelated project's session, same store, must never be discovered as testproj's.
    unrelated_records = [
        {"type": "session_meta", "timestamp": "2026-08-01T08:59:00Z",
         "payload": {"id": SID_UNRELATED, "cwd": str(other_root), "cli_version": "0.153.4",
                     "originator": "codex_exec"}},
        {"type": "event_msg", "timestamp": "2026-08-01T09:00:00Z",
         "payload": {"type": "item_completed", "thread_id": SID_UNRELATED, "turn_id": "t1",
                     "item": {"type": "UserMessage", "id": "u1",
                              "content": [{"type": "text", "text": "unrelated project turn"}]}}},
        {"type": "event_msg", "timestamp": "2026-08-01T09:00:05Z",
         "payload": {"type": "item_completed", "thread_id": SID_UNRELATED, "turn_id": "t1",
                     "item": {"type": "AgentMessage", "id": "a1",
                              "content": [{"type": "text", "text": "ok"}]}}},
    ]
    (codex_dir / f"rollout-2026-08-01T08-59-01-{SID_UNRELATED}.jsonl").write_text(
        "\n".join(json.dumps(r) for r in unrelated_records) + "\n", encoding="utf-8")

    found = cb.find_codex_sources("testproj", registry)
    check(len(found) == 1 and found[0] == codex_path,
          "find_codex_sources: only the target project's rollout is discovered")

    stats = cb.backfill_project(project, cfg, registry=registry)
    check(stats == {"scanned": 1, "written": 1},
          f"backfill_project: scans and captures exactly the one owned session (got {stats})")

    demo_dir = root / ".claude" / "skills" / "demo-skill" / "conversations"
    archive_dir = root / "conversations" / "_archive"
    demo_files = list(demo_dir.glob("*.md"))
    check(len(demo_files) == 1 and not archive_dir.exists(),
          "acceptance #1: a markerless Codex session naming a known skill routes to it, not _archive")

    text = demo_files[0].read_text(encoding="utf-8")
    check(demo_files[0].name.startswith("2026-08-01-0859-"),
          f"acceptance #3: filename stamped from the session's own start, not today (got {demo_files[0].name})")
    check(cb.write_capture.__module__ == "conversation_capture", "sanity: reusing the shared writer")
    header = __import__("conversation_capture").parse_capture_header(text)
    check(header.get("agent") == "codex" and header.get("sid") == SID_CODEX,
          "backfilled capture carries the real Codex identity")
    check("Logged the demo entry." in text, "backfilled capture holds the real conversation text")

    # ---- idempotence: re-running captures nothing new, bytes unchanged ----
    before = demo_files[0].read_bytes()
    stats2 = cb.backfill_project(project, cfg, registry=registry)
    check(stats2 == {"scanned": 1, "written": 0},
          f"acceptance #4: re-running the backfill over an already-captured session is a no-op (got {stats2})")
    check(demo_files[0].read_bytes() == before, "idempotent re-run leaves the capture byte-identical")

    # ---- dry-run against an already-captured session: reports 0, writes nothing ----
    dry_stats = cb.backfill_project(project, cfg, registry=registry, dry_run=True)
    check(dry_stats == {"scanned": 1, "written": 0},
          f"dry-run against an up-to-date session reports written=0, not every scanned session (got {dry_stats})")
    check(len(list(demo_dir.glob("*.md"))) == 1, "dry-run against an up-to-date session creates no file")

    # ---- dry-run against a genuinely new session: reports it, still writes nothing ----
    # Regression for a real bug caught live against life-os: an earlier dry-run
    # skipped the dedup lookup entirely and reported every scanned session as
    # "would capture", including hundreds already captured (fleet-config#785).
    SID_CODEX_NEW = "01a07678-9999-7973-bd6e-15d708b9075c"
    new_records = [
        {"type": "session_meta", "timestamp": "2026-08-03T08:59:00Z",
         "payload": {"id": SID_CODEX_NEW, "cwd": str(root), "cli_version": "0.153.4",
                     "originator": "codex_exec"}},
        {"type": "event_msg", "timestamp": "2026-08-03T09:00:00Z",
         "payload": {"type": "item_completed", "thread_id": SID_CODEX_NEW, "turn_id": "t1",
                     "item": {"type": "UserMessage", "id": "u1",
                              "content": [{"type": "text", "text": "another unrelated turn"}]}}},
        {"type": "event_msg", "timestamp": "2026-08-03T09:00:05Z",
         "payload": {"type": "item_completed", "thread_id": SID_CODEX_NEW, "turn_id": "t1",
                     "item": {"type": "AgentMessage", "id": "a1",
                              "content": [{"type": "text", "text": "ok"}]}}},
    ]
    (codex_dir / f"rollout-2026-08-03T08-59-00-{SID_CODEX_NEW}.jsonl").write_text(
        "\n".join(json.dumps(r) for r in new_records) + "\n", encoding="utf-8")

    dry_stats_new = cb.backfill_project(project, cfg, registry=registry, dry_run=True)
    check(dry_stats_new == {"scanned": 2, "written": 1},
          f"dry-run correctly distinguishes the one genuinely new session (got {dry_stats_new})")
    check(len(list(archive_dir.glob("*.md"))) == 0, "dry-run of a new session still creates no file")

    real_stats_new = cb.backfill_project(project, cfg, registry=registry)
    check(real_stats_new == {"scanned": 2, "written": 1},
          f"the real run then actually captures exactly that one session (got {real_stats_new})")
    check(len(list(archive_dir.glob("*.md"))) == 1, "the new session lands in _archive (no skill mention)")

    # ---- a Claude transcript with no skill mention falls to _archive, as live capture would ----
    claude_dir = claude_store / "some-encoded-testproj-dir"
    claude_dir.mkdir()
    claude_records = [
        {"type": "user", "sessionId": SID_CLAUDE, "timestamp": "2026-08-02T10:00:00Z",
         "cwd": str(root), "message": {"role": "user", "content": [
             {"type": "text", "text": "just checking something, no skill involved"}]}},
        {"type": "assistant", "sessionId": SID_CLAUDE, "timestamp": "2026-08-02T10:00:02Z",
         "cwd": str(root), "message": {"role": "assistant", "content": [
             {"type": "text", "text": "sure, go ahead"}]}},
    ]
    (claude_dir / f"{SID_CLAUDE}.jsonl").write_text(
        "\n".join(json.dumps(r) for r in claude_records) + "\n", encoding="utf-8")

    found_claude = cb.find_claude_sources("testproj", registry)
    check(len(found_claude) == 1, "find_claude_sources: discovers the Claude-store transcript via CLAUDE_TRANSCRIPTS_DIR")
    claude_stats = cb.backfill_project(project, cfg, harnesses=["claude"], registry=registry)
    check(claude_stats == {"scanned": 1, "written": 1}, f"claude backfill captured the session (got {claude_stats})")

    archived = list(archive_dir.glob("*.md"))
    check(len(archived) == 2,
          "both no-skill-mention sessions (Codex and Claude) fall through to _archive")

finally:
    if saved_claude_env is None:
        _lib.os.environ.pop("CLAUDE_TRANSCRIPTS_DIR", None)
    else:
        _lib.os.environ["CLAUDE_TRANSCRIPTS_DIR"] = saved_claude_env
    if saved_codex_env is None:
        _lib.os.environ.pop("CODEX_SESSIONS_DIR", None)
    else:
        _lib.os.environ["CODEX_SESSIONS_DIR"] = saved_codex_env
    shutil.rmtree(tmp, ignore_errors=True)

_h.report_and_exit("test_conversation_backfill")
