"""Unit tests for the conversation resume-identity / search layer (fleet-config#586).

Covers the three pieces that together make a past conversation findable and
reopenable, against synthetic captures and transcripts in a temp tree:

  * ``conversation_capture`` — the identity header (write / parse / strip).
  * ``conversation_index`` — digest-field parsing, the ``<!-- idx -->`` attr
    extension, and the ``index.json`` twin.
  * ``heal_capture_sids`` — the three matching tiers plus the two guards that
    keep a *wrong* match from ever being written.
  * ``conversation_search`` — db build, ranked query, filters, and the
    resume-command mapping.

Two checks here are regressions for bugs found during the build, both of the
same family — a match that looked successful but was wrong:

  * ``_digest_fields`` used ``lstrip("-*• ")``, which also ate the ``**`` that
    opens the label, so every field in ``index.json`` silently came out empty.
  * the healer matched captures on ``[Request interrupted by user]`` — Claude
    Code's own system text, shared by dozens of unrelated conversations — and
    paired a 2026-06-01 capture with a 2026-07-17 transcript.

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
import heal_capture_sids as heal  # noqa: E402

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

# The signature must be identical whether it came from a live transcript's
# messages or from a rendered capture -- the healer's whole premise.
turns = [("user", "Base directory for this skill: E:/x"),
         ("user", "I want to research bone conduction headphones repair"),
         ("assistant", "sure")]
sig_live = cc.content_signature(turns)
sig_capture = cc.signature_of(heal.capture_first_turn(
    cc.render_markdown("d", turns, header="")))
check(sig_live and sig_live == sig_capture,
      "signature: transcript-side and capture-side agree")


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


# ------------------------------------------------------------- healer tiers

def _transcript(dirpath: Path, sid: str, first_turn: str, started: str) -> Path:
    """A minimal Claude-Code-shaped transcript JSONL named by its session id."""
    path = dirpath / f"{sid}.jsonl"
    rows = [
        {"type": "mode", "sessionId": sid},
        {"type": "user", "timestamp": started,
         "message": {"role": "user", "content": first_turn}},
        {"type": "assistant", "timestamp": started,
         "message": {"role": "assistant", "content": "ok"}},
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return path


REAL_TURN = "I want to research bone conduction headphones repair options"

tmp = Path(tempfile.mkdtemp(prefix="conv_heal_"))
try:
    tdir = tmp / "transcripts"
    tdir.mkdir()
    _transcript(tdir, SID, REAL_TURN, "2026-06-13T19:00:00.000Z")
    _transcript(tdir, OTHER_SID, "a completely different opening question here",
                "2026-06-14T19:00:00.000Z")
    transcripts = heal.load_transcripts(tdir)
    check(len(transcripts) == 2, "load_transcripts: reads the store")
    check(any(t.started == date(2026, 6, 13) for t in transcripts),
          "load_transcripts: start date parsed from the first timestamp")

    cdir = tmp / "conversations"
    cdir.mkdir()

    # Tier 1: filename session token (last 8 of the sid).
    tok = cc.session_token(SID)
    f1 = cdir / f"2026-06-13-1900-headphones-{tok}.md"
    f1.write_text(cc.render_markdown("d", [("user", REAL_TURN)]), encoding="utf-8")
    hit, tier = heal.match_transcript(f1, f1.read_text(encoding="utf-8"), transcripts)
    check(hit is not None and hit.sid == SID and tier == "session-token",
          "healer tier 1: filename session token matches")

    # Tier 2: filename content signature.
    sig = cc.signature_of(REAL_TURN)
    f2 = cdir / f"2026-06-13-1901-headphones-{sig}.md"
    f2.write_text(cc.render_markdown("d", [("user", REAL_TURN)]), encoding="utf-8")
    hit, tier = heal.match_transcript(f2, f2.read_text(encoding="utf-8"), transcripts)
    check(hit is not None and hit.sid == SID and tier in ("content-signature", "content-match"),
          "healer tier 2: filename content signature matches")

    # Tier 3: no tokens at all (pre-token era / renamed by app-launcher).
    f3 = cdir / "2026-06-13-1902-renamed-by-hand.md"
    f3.write_text(cc.render_markdown("d", [("user", REAL_TURN)]), encoding="utf-8")
    hit, tier = heal.match_transcript(f3, f3.read_text(encoding="utf-8"), transcripts)
    check(hit is not None and hit.sid == SID and tier == "content-match",
          "healer tier 3: tokenless capture matched on its opening turn")

    # Guard A -- a system marker is not identity (regression: this paired a
    # 2026-06-01 capture with a 2026-07-17 transcript).
    marker = "[Request interrupted by user]"
    _transcript(tdir, "99998888-7777-6666-5555-444433332222", marker,
                "2026-07-17T19:00:00.000Z")
    transcripts = heal.load_transcripts(tdir)
    check(heal.identity_signature(marker) == "",
          "guard: a bracketed system marker carries no identity")
    f4 = cdir / "2026-06-01-1822-request-interrupted-user.md"
    f4.write_text(cc.render_markdown("d", [("user", marker)]), encoding="utf-8")
    hit, _ = heal.match_transcript(f4, f4.read_text(encoding="utf-8"), transcripts)
    check(hit is None,
          "guard: boilerplate-only capture stays unmatched, never a wrong sid")

    # Guard B -- a transcript cannot predate-match a capture written before it.
    late_sid = "aaaabbbb-cccc-dddd-eeee-ffff00001111"
    _transcript(tdir, late_sid, "a late unique opening turn about ferries",
                "2026-07-20T19:00:00.000Z")
    transcripts = heal.load_transcripts(tdir)
    f5 = cdir / "2026-06-02-1000-ferries.md"
    f5.write_text(cc.render_markdown(
        "d", [("user", "a late unique opening turn about ferries")]), encoding="utf-8")
    hit, _ = heal.match_transcript(f5, f5.read_text(encoding="utf-8"), transcripts)
    check(hit is None,
          "guard: transcript starting after the capture date is rejected")
    check(heal.plausible(heal.Transcript("x", 0.0, "s", date(2026, 6, 13)),
                         date(2026, 6, 13)) is True,
          "plausible: same-day transcript accepted")
    check(heal.plausible(heal.Transcript("x", 0.0, "s", None), date(2026, 6, 13)) is True,
          "plausible: unknown start -> not rejected (can't establish it)")

    # write_header: inserts identity and preserves mtime.
    original = f1.read_text(encoding="utf-8")
    before = f1.stat().st_mtime
    heal.write_header(f1, original, SID, "claude")
    after = f1.read_text(encoding="utf-8")
    check(cc.parse_capture_header(after).get("sid") == SID,
          "write_header: header lands where parse_capture_header finds it")
    check(after.splitlines()[0] == original.splitlines()[0],
          "write_header: description line untouched")
    check(abs(f1.stat().st_mtime - before) < 2,
          "write_header: mtime preserved (no mass re-digest)")
    check("**You**: " + REAL_TURN in after, "write_header: body preserved")

    # First-turn collision between two different days: the transcript that began
    # on the capture's own date wins over the merely-newer one, so a shared
    # opening sentence can't pair an older capture with an unrelated later session.
    shared = "lets go through the weekly numbers together now"
    early_sid = "11110000-0000-0000-0000-000000000001"
    later_sid = "22220000-0000-0000-0000-000000000002"
    _transcript(tdir, early_sid, shared, "2026-06-20T09:00:00.000Z")
    _transcript(tdir, later_sid, shared, "2026-06-25T09:00:00.000Z")
    transcripts = heal.load_transcripts(tdir)
    f6 = cdir / "2026-06-20-0900-weekly-numbers.md"
    f6.write_text(cc.render_markdown("d", [("user", shared)]), encoding="utf-8")
    hit, _ = heal.match_transcript(f6, f6.read_text(encoding="utf-8"), transcripts)
    check(hit is not None and hit.sid == early_sid,
          "collision: same-day transcript beats the newer one")

    check(heal.filename_tokens(f"2026-06-13-1900-x-{tok}-{sig}.md") == [tok, sig],
          "filename_tokens: both tokens, in order")
    check(heal.filename_tokens("2026-06-13-1900-renamed.md") == [],
          "filename_tokens: renamed capture has none")
    check(heal.capture_date("2026-06-13-1900-x.md") == date(2026, 6, 13),
          "capture_date: parsed from the filename prefix")
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
