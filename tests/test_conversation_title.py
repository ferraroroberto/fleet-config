"""User titles on captured conversations survive re-indexing (fleet-config#1348).

Every fixture is invented and lives in a temp tree; the digest model is
stubbed, so no hub call is made. Covers the issue's acceptance criteria:

  * a title survives the indexer re-digesting a changed capture, a resumed
    conversation's re-capture, and a ``--force`` rebuild, while the topic
    underneath keeps updating;
  * clearing it falls back to the topic (``title`` back to ``""``);
  * search finds a conversation by its title, including after a title-only
    change (no capture mtime moved) and once the capture is archived;

plus the writer's contract: the CLI call shape, its refusals and the
corrupt-sidecar guard.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/test_conversation_title.py`
(also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "hooks"))
import conversation_capture as cc  # noqa: E402
import conversation_index as ci  # noqa: E402
import conversation_search as cs  # noqa: E402
import conversation_title as ct  # noqa: E402
from transcript_readers import Transcript  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check

SID = "0f0f0f0f-1111-4222-8333-444455556666"
SKILL = "garden"
TITLE = "Espalier sketchbook"


def _digest_stub(topic: str):
    return lambda _text: (f"- **Topic:** {topic}\n- **Decisions:** none\n"
                          f"- **Open loops:** none")


def _age(path: Path) -> None:
    """Push a capture past the settle window so the indexer will digest it."""
    old = time.time() - 3600
    os.utime(path, (old, old))


def _rows(conv_dir: Path) -> "dict[str, dict]":
    return {r["file"]: r for r in json.loads(
        (conv_dir / ci.INDEX_JSON_NAME).read_text(encoding="utf-8"))}


def _transcript(messages):
    return Transcript(status="ok", harness="claude", session_id=SID, messages=messages,
                      parent_session_id="", source_format="claude-jsonl",
                      source_version="1", detail="")


tmp = Path(tempfile.mkdtemp(prefix="conv_title_"))
try:
    root = tmp / "proj"
    conv = root / ".claude" / "skills" / SKILL / "conversations"
    conv.mkdir(parents=True)
    cfg = cc.CaptureConfig(root=root, routing="skills", conversations_dir="conversations",
                           skills_dir=".claude/skills", active_marker=".active-skill")

    # A real capture write, so the resumed-conversation path below is the
    # capture hook's own in-place rewrite, not a hand-edited file.
    source = tmp / "source.jsonl"
    source.write_text("{}", encoding="utf-8")
    first = [("user", "where should the hedge go"), ("assistant", "along the fence")]
    check(cc.write_capture(cfg, conv, source, _transcript(first)),
          "fixture: first capture written")
    cap = next(p for p in conv.glob("*.md"))
    _age(cap)
    with mock.patch.object(ci, "digest", _digest_stub("hedge placement")):
        ci.index_dir(conv, SKILL)
    check(_rows(conv)[cap.name]["title"] == "",
          "index.json: an untitled conversation carries title \"\"")

    # ---- the writer ------------------------------------------------------
    stored = ct.set_title(conv, SKILL, cap.name, f"  {TITLE}\n ")
    check(stored == TITLE, "set_title: whitespace and newlines collapsed")
    check(json.loads((conv / ci.TITLES_NAME).read_text(encoding="utf-8")) == {cap.name: TITLE},
          "set_title: sidecar keyed on the capture filename")
    check(_rows(conv)[cap.name]["title"] == TITLE,
          "set_title: index.json refreshed at once, no index run needed")
    check(_rows(conv)[cap.name]["topic"] == "hedge placement",
          "set_title: the digest topic is untouched")

    # ---- survives every re-index path -----------------------------------
    # A resumed conversation: the capture hook rewrites the same file in place.
    resumed = first + [("user", "and what about the quince"), ("assistant", "plant two")]
    check(cc.write_capture(cfg, conv, source, _transcript(resumed)),
          "fixture: resumed conversation re-captured")
    check([p.name for p in conv.glob("*.md") if p.name != ci.INDEX_NAME] == [cap.name],
          "fixture: a resume rewrites the same capture filename")
    _age(cap)
    os.utime(cap, (time.time() - 1800, time.time() - 1800))  # mtime moved, still settled
    with mock.patch.object(ci, "digest", _digest_stub("hedge and quince")):
        check(ci.index_dir(conv, SKILL) == 1, "re-index: the changed capture is re-digested")
    row = _rows(conv)[cap.name]
    check(row["title"] == TITLE, "re-index after resume: the title survives")
    check(row["topic"] == "hedge and quince", "re-index after resume: the topic still updates")

    with mock.patch.object(ci, "digest", _digest_stub("quince only")):
        ci.index_dir(conv, SKILL, force=True)
    check(_rows(conv)[cap.name]["title"] == TITLE, "re-index --force: the title survives")

    (conv / ci.INDEX_NAME).unlink()
    (conv / ci.INDEX_JSON_NAME).unlink()
    with mock.patch.object(ci, "digest", _digest_stub("rebuilt topic")):
        ci.index_dir(conv, SKILL)
    check(_rows(conv)[cap.name]["title"] == TITLE,
          "index rebuilt from nothing: the title survives (it never lived in the index)")

    # ---- search ----------------------------------------------------------
    cs.sync(cfg)
    hits = cs.search(cfg, "espalier sketchbook")
    check(len(hits) == 1 and hits[0]["title"] == TITLE, "search: found by its title")
    ct.set_title(conv, SKILL, cap.name, "Orchard notebook")
    cs.sync(cfg)  # the capture's mtime did not move — only the title changed
    check([h["title"] for h in cs.search(cfg, "orchard")] == ["Orchard notebook"],
          "search: a title-only change is re-synced")
    check(cs.search(cfg, "espalier") == [],
          "search: the old title no longer matches")

    # ---- clearing falls back to the topic -------------------------------
    check(ct.set_title(conv, SKILL, cap.name, "") == "", "clear: returns \"\"")
    check(_rows(conv)[cap.name]["title"] == "" and _rows(conv)[cap.name]["topic"],
          "clear: title back to \"\", topic still there to show")
    check(json.loads((conv / ci.TITLES_NAME).read_text(encoding="utf-8")) == {},
          "clear: the sidecar entry is removed, not blanked")

    # ---- archived captures keep their title in search -------------------
    ct.set_title(conv, SKILL, cap.name, "Orchard notebook")
    (conv / "archive").mkdir()
    shutil.move(str(cap), str(conv / "archive" / cap.name))
    cs.sync(cfg)
    check([h["file"] for h in cs.search(cfg, "orchard")] == [cap.name],
          "archive: an archived capture is still found by its title")
    check(ct.set_title(conv, SKILL, cap.name, "Archived name") == "Archived name",
          "archive: an archived capture can still be renamed")

    # ---- refusals --------------------------------------------------------
    for bad, why in (("missing.md", "absent capture"), ("../x.md", "path part"),
                     (ci.INDEX_NAME, "the index itself"), ("notes.txt", "not a capture")):
        try:
            ct.set_title(conv, SKILL, bad, "x")
            check(False, f"set_title refuses {why}")
        except ct.ConversationNotFound:
            check(True, f"set_title refuses {why}")
    try:
        ct.set_title(conv, SKILL, cap.name, "x" * (ct.MAX_TITLE_CHARS + 1))
        check(False, "set_title refuses an over-long title")
    except ValueError:
        check(True, "set_title refuses an over-long title")
    check(ct.set_title(conv, SKILL, "gone.md", "") == "",
          "clear: allowed for a capture that no longer exists")

    (conv / ci.TITLES_NAME).write_text("[not, a, map", encoding="utf-8")
    check(ci.read_titles(conv) == {}, "read_titles: a corrupt sidecar reads as no titles")
    try:
        ct.set_title(conv, SKILL, cap.name, "y")
        check(False, "set_title refuses to overwrite a corrupt sidecar")
    except OSError:
        check(True, "set_title refuses to overwrite a corrupt sidecar")
    check((conv / ci.TITLES_NAME).read_text(encoding="utf-8") == "[not, a, map",
          "corrupt sidecar left untouched for the owner to inspect")
    (conv / ci.TITLES_NAME).unlink()

    # ---- an existing search db gains the column ------------------------
    legacy_db = tmp / "legacy.db"
    with sqlite3.connect(str(legacy_db)) as old:
        old.executescript(cs._SCHEMA.replace("    title      TEXT NOT NULL DEFAULT '',\n", ""))
    conn = cs.connect(legacy_db)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(conversations)")}
    conn.close()
    check("title" in cols, "connect: a pre-#1348 search db is migrated, not rebuilt")

    # ---- the CLI call shape the launcher uses ----------------------------
    def _cli(*argv: str) -> "tuple[int, str, str]":
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "argv", ["conversation_title.py", *argv]), \
                mock.patch.object(cs, "resolve_config", lambda p, c: cfg if c == str(root) else None), \
                redirect_stdout(out), redirect_stderr(err):
            try:
                code = ct.main()
            except SystemExit as exc:
                code = int(exc.code or 0)
        return code, out.getvalue(), err.getvalue()

    code, out, _ = _cli("--cwd", str(root), "--skill", SKILL, "--file", cap.name,
                        "--title", "Via the CLI")
    check(code == 0 and json.loads(out) == {"skill": SKILL, "file": cap.name,
                                             "title": "Via the CLI"},
          "cli: set prints {skill, file, title} and exits 0")
    check([h["title"] for h in cs.search(cfg, "via the cli")] == ["Via the CLI"],
          "cli: search is current as soon as the call returns")
    code, out, _ = _cli("--cwd", str(root), "--skill", SKILL, "--file", cap.name, "--clear")
    check(code == 0 and json.loads(out)["title"] == "", "cli: --clear exits 0 with title \"\"")
    check(_cli("--cwd", str(tmp), "--skill", SKILL, "--file", cap.name, "--clear")[0] == 1,
          "cli: unknown project -> exit 1")
    check(_cli("--cwd", str(root), "--skill", "nope", "--file", cap.name, "--clear")[0] == 3,
          "cli: unknown skill -> exit 3")
    check(_cli("--cwd", str(root), "--skill", SKILL, "--file", "missing.md",
               "--title", "x")[0] == 3,
          "cli: unknown conversation -> exit 3")
    check(_cli("--cwd", str(root), "--skill", SKILL, "--file", cap.name,
               "--title", "x" * 500)[0] == 2,
          "cli: over-long title -> exit 2")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

_h.report_and_exit("test_conversation_title")
