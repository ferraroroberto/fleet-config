"""Unit tests for the conversation resume-identity / search layer (fleet-config#586).

Covers the three pieces that together make a past conversation findable and
reopenable, against synthetic captures and transcripts in a temp tree:

  * ``conversation_capture`` — the identity header (write / parse / strip).
  * ``conversation_index`` — digest-field parsing, the ``<!-- idx -->`` attr
    extension, and the ``index.json`` twin.
  * ``conversation_search`` — db build, ranked query, filters, and the
    resume-command mapping.

One check here is a regression for a bug found during the build:
``_digest_fields`` used ``lstrip("-*• ")``, which also ate the ``**`` that
opens the label, so every field in ``index.json`` silently came out empty — a
parse that looked successful and was wrong.

The one-shot ``heal_capture_sids`` backfill this suite also covered was
deleted in #598 once life-os's history was healed; its tests went with it.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/test_conversation_search.py`
(also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "hooks"))
import conversation_capture as cc  # noqa: E402
import conversation_index as ci  # noqa: E402
import conversation_search as cs  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check

SID = "66dcba75-9781-4c33-ab89-2a5bcc93cceb"
OTHER_SID = "11112222-3333-4444-5555-666677778888"


# --------------------------------------------------------------- the header

header = cc.capture_header(SID, "claude", "2026-08-09T18:00:00")
check(f'sid="{SID}"' in header and 'agent="claude"' in header,
      "capture_header: carries sid and agent")
check(cc.capture_header("", "", "") == "",
      "capture_header: nothing to say -> empty, never a blank header")

doc = cc.render_markdown("a description", [("user", "hello there friend"),
                                           ("assistant", "hi")], header=header)
parsed = cc.parse_capture_header(doc)
check(parsed.get("sid") == SID and parsed.get("agent") == "claude",
      "render_markdown/parse_capture_header: round trip")
check(doc.splitlines()[0] == "a description",
      "render_markdown: description stays the first line")
check("<!-- capture" not in cc.strip_capture_header(doc),
      "strip_capture_header: removes the header a digest shouldn't see")
check("**You**: hello there friend" in cc.strip_capture_header(doc),
      "strip_capture_header: leaves the conversation intact")
check(cc.parse_capture_header("no header here\n\n**You**: hi") == {},
      "parse_capture_header: legacy capture -> {} (optional by design)")
# A header-shaped string quoted deep in a transcript body is content, not identity.
buried = "desc\n\n**You**: hi\n" + "\n".join(f"line {i}" for i in range(10)) + \
         f'\n<!-- capture sid="{OTHER_SID}" -->'
check(cc.parse_capture_header(buried) == {},
      "parse_capture_header: ignores a header-shaped line in the body")

# The content signature is what makes a *resumed* conversation update its
# existing capture instead of writing a second one: `claude --resume` mints a
# new session id, so identity has to come from the opening turn, which doesn't
# change. These two checks pin exactly that property.
turns = [("user", "Base directory for this skill: E:/x"),
         ("user", "I want to research bone conduction headphones repair"),
         ("assistant", "sure")]
sig_live = cc.content_signature(turns)
check(bool(sig_live),
      "content_signature: derives an identity from the first real turn")
check(cc.content_signature(turns + [("user", "and one more thing entirely")])
      == sig_live,
      "content_signature: later turns don't move it (survives a resume)")


# ------------------------------------------------------- index digest fields

body = ("- **Topic:** repairing headphones\n"
        "- **Decisions:** use thin CA glue\n"
        "- **Open loops:** not yet attempted")
fields = ci._digest_fields(body)
check(fields["topic"] == "repairing headphones",
      "_digest_fields: topic parsed (regression: lstrip ate the ** and blanked it)")
check(fields["decisions"] == "use thin CA glue", "_digest_fields: decisions parsed")
check(fields["open_loops"] == "not yet attempted", "_digest_fields: open loops parsed")
check(ci._digest_fields("nothing structured here")["topic"] == "",
      "_digest_fields: unstructured body -> empty fields, no crash")

entries = {"2026-08-09-1826-a-b-c.md": ci.Entry(
    file="2026-08-09-1826-a-b-c.md", mtime=1786292799.0, turns=14,
    body=body, sid=SID, agent="claude")}
rendered = ci.render_index("geek-out", entries)
check(f'sid="{SID}"' in rendered and 'agent="claude"' in rendered,
      "render_index: idx attrs carry sid/agent")
check('file="2026-08-09-1826-a-b-c.md"' in rendered and "mtime=" in rendered,
      "render_index: existing attr grammar preserved, not reshaped")

tmp = Path(tempfile.mkdtemp(prefix="conv_idx_"))
try:
    idx = tmp / "index.md"
    idx.write_text(rendered, encoding="utf-8")
    back = ci.parse_index(idx)
    e = back["2026-08-09-1826-a-b-c.md"]
    check(e.sid == SID and e.agent == "claude", "parse_index: reads sid/agent back")
    check(e.turns == 14, "parse_index: still reads the pre-existing attrs")

    # An unused skill's empty conversations/ must stay empty — no header-only
    # index.md, no empty index.json conjured into it.
    empty = tmp / "empty_conversations"
    empty.mkdir()
    ci.index_dir(empty, "unused-skill")
    check(not (empty / ci.INDEX_NAME).exists() and not (empty / ci.INDEX_JSON_NAME).exists(),
          "index_dir: a dir with no captures gets no index files")

    payload = ci.index_json_payload("geek-out", entries)
    check(payload[0]["sid"] == SID and payload[0]["skill"] == "geek-out",
          "index_json_payload: identity + skill present")
    check(payload[0]["topic"] == "repairing headphones",
          "index_json_payload: digest fields populated (not empty)")
    check(payload[0]["date"] == "2026-08-09",
          "index_json_payload: date split off the filename")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

# Decay-zone round trip must survive the attr extension.
tmp = Path(tempfile.mkdtemp(prefix="conv_decay_"))
try:
    idx = tmp / "index.md"
    idx.write_text(rendered + "\n" + ci.DECAY_MARKER + "\n### 2026-04 · period summary\n- x\n",
                   encoding="utf-8")
    tail = ci.decay_tail(idx)
    check(ci.DECAY_MARKER in tail and "period summary" in tail,
          "decay zone: still round-tripped verbatim")
    check("2026-08-09-1826-a-b-c.md" in ci.parse_index(idx),
          "decay zone: entries above the marker still parse")
finally:
    shutil.rmtree(tmp, ignore_errors=True)


# -------------------------------------------------------------- the search

check(cs.resume_command("claude", SID) == f"claude --resume {SID}",
      "resume_command: claude")
check(cs.resume_command("codex", SID) == f"codex resume {SID}",
      "resume_command: codex uses its own verb, not claude's")
check(cs.resume_command("claude", "") == "",
      "resume_command: no sid -> no command (never a fabricated one)")
check(cs.resume_command("some-future-agent", SID) == "",
      "resume_command: unknown agent -> empty, caller must say 'not resumable'")

tmp = Path(tempfile.mkdtemp(prefix="conv_search_"))
try:
    root = tmp / "life-os"
    skills = root / ".claude" / "skills"
    for skill, topic, text in (
        ("geek-out", "repairing bone conduction headphones",
         "the titanium band fractured and I used thin cyanoacrylate"),
        ("journal-daily", "notion journal entry for the day",
         "wrote the diary page and flipped the gratitude checkbox"),
    ):
        cdir = skills / skill / "conversations"
        cdir.mkdir(parents=True)
        name = f"2026-08-09-1826-{skill}-abcd1234.md"
        (cdir / name).write_text(
            cc.render_markdown(
                "d", [("user", text)],
                header=cc.capture_header(SID if skill == "geek-out" else OTHER_SID,
                                         "claude", "2026-08-09T18:00:00")),
            encoding="utf-8")
        (cdir / "index.md").write_text(ci.render_index(skill, {name: ci.Entry(
            file=name, mtime=(cdir / name).stat().st_mtime, turns=2,
            body=f"- **Topic:** {topic}\n- **Decisions:** none\n- **Open loops:** none",
            sid=SID if skill == "geek-out" else OTHER_SID, agent="claude")}),
            encoding="utf-8")

    cfg = cc.CaptureConfig(root=root, routing="skills",
                           conversations_dir="conversations",
                           skills_dir=".claude/skills", active_marker=".active-skill")
    n = cs.sync(cfg)
    check(n == 2, f"search sync: indexed both conversations (got {n})")

    hits = cs.search(cfg, "headphones")
    check(len(hits) == 1 and hits[0]["skill"] == "geek-out",
          "search: digest hit found, right skill")
    check(hits[0]["resume"] == f"claude --resume {SID}" and hits[0]["resumable"],
          "search: result carries a ready-to-run resume command")
    # A body-only term proves the full transcript is searchable, not just digests.
    body_hits = cs.search(cfg, "cyanoacrylate")
    check(len(body_hits) == 1 and body_hits[0]["skill"] == "geek-out",
          "search: full-transcript term found (absent from the digest)")
    check(len(cs.search(cfg, "notion", skill="geek-out")) == 0,
          "search: skill filter excludes other skills")
    check(len(cs.search(cfg, "notion", skill="journal-daily")) == 1,
          "search: skill filter keeps the right skill")
    check(len(cs.search(cfg, "headphones", since="2026-09-01")) == 0,
          "search: since filter excludes older conversations")
    check(len(cs.search(cfg, "headphones", since="2026-01-01")) == 1,
          "search: since filter keeps newer conversations")
    # FTS5 syntax the user will type by accident must not surface as an error.
    check(isinstance(cs.search(cfg, "what's the --resume flag:"), list),
          "search: unparseable query falls back to quoted terms, no crash")
    check(cs.search(cfg, "zzzznotpresent") == [],
          "search: no match -> empty list")

    # The db is a pure derivative: deleting it must always be recoverable.
    cs.db_path(cfg).unlink()
    check(cs.search(cfg, "headphones") == [],
          "search: missing db -> empty, not an exception")
    check(cs.sync(cfg, rebuild=True) == 2, "search: rebuild restores from captures")
    check(len(cs.search(cfg, "headphones")) == 1, "search: queries work after rebuild")

    # A capture that disappears must not linger as a ghost row.
    next(iter((skills / "geek-out" / "conversations").glob("2026-*.md"))).unlink()
    cs.sync(cfg)
    check(cs.search(cfg, "headphones") == [],
          "search sync: prunes rows whose capture is gone")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

_h.report_and_exit("test_conversation_search")
