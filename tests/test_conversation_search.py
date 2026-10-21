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
import sqlite3
import sys
import tempfile
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
check(fields["summary"] == "",
      "_digest_fields: an old-shape entry (decisions/open loops) has no summary")
new_fields = ci._digest_fields("- **Topic:** repairing headphones\n"
                               "- **Summary:** Glued the band; still untested.")
check(new_fields["summary"] == "Glued the band; still untested."
      and new_fields["decisions"] == "" and new_fields["open_loops"] == "",
      "_digest_fields: a new-shape entry parses topic + summary only")

# ---- the digest prompt + digest() (fleet-config#1373) ----
check("Topic:" in ci.DIGEST_PROMPT and "Summary:" in ci.DIGEST_PROMPT
      and "Decisions:" not in ci.DIGEST_PROMPT and "Open loops:" not in ci.DIGEST_PROMPT
      and "EXACTLY two lines" in ci.DIGEST_PROMPT,
      "DIGEST_PROMPT: asks for exactly Topic + Summary, no Decisions / Open loops")
_saved_complete = ci.hub_client.complete
try:
    ci.hub_client.complete = lambda *a, **k: "Topic: glue job\nSummary: Repaired the band; check it holds."
    check(ci.digest("t") == "- **Topic:** glue job\n- **Summary:** Repaired the band; check it holds.",
          "digest: hub answer -> Topic + Summary bullets")
    ci.hub_client.complete = lambda *a, **k: "- Topic: glue job\n- summary: bulleted and lowercase"
    check(ci.digest("t").endswith("- **Summary:** bulleted and lowercase"),
          "digest: bullet prefixes and label case tolerated")
    # A legacy-shaped or malformed answer must fail safe: an entry, never an exception.
    ci.hub_client.complete = lambda *a, **k: "Topic: t\nDecisions: d\nOpen loops: o"
    check(ci.digest("t") == "- **Topic:** t\n- **Summary:** none",
          "digest: old three-line answer -> topic kept, summary 'none', no crash")
    ci.hub_client.complete = lambda *a, **k: "free prose with no labels at all"
    check(ci.digest("t") == "- **Topic:** free prose with no labels at all\n- **Summary:** none",
          "digest: unlabelled answer -> first line becomes the topic, no crash")
    ci.hub_client.complete = lambda *a, **k: None
    check(ci.digest("t") is None, "digest: hub down -> None (entry retried later)")
finally:
    ci.hub_client.complete = _saved_complete

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
            # geek-out is a new-shape (#1373) entry, journal-daily a pre-existing old one.
            body=(f"- **Topic:** {topic}\n- **Summary:** ferrule trimmed and band reglued"
                  if skill == "geek-out" else
                  f"- **Topic:** {topic}\n- **Decisions:** none\n- **Open loops:** none"),
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
    check(hits[0]["summary"] == "ferrule trimmed and band reglued"
          and len(cs.search(cfg, "ferrule")) == 1,
          "search: a new-shape entry's summary is stored, returned and searchable")
    check(cs.search(cfg, "notion")[0]["summary"] == "",
          "search: an old-shape entry still loads (summary empty)")
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

    # --- an in-progress word must not be a dead end (#935) ---------------
    # Whole-word-only matching made every keystroke before a word completed
    # render as "Nothing matched", so the feature read as broken while typing.
    check(len(cs.search(cfg, "headph")) == 1,
          "search: a prefix of an indexed word matches (still-typing case)")
    check(len(cs.search(cfg, "cyanoacry")) == 1,
          "search: prefix reaches the body layer, not just the digest")
    check(len(cs.search(cfg, "headphones bone")) == 1,
          "search: a completed multi-word query still matches")
    check(len(cs.search(cfg, "headphones con")) == 1,
          "search: the last term of a multi-word query is the prefix")
    # Only the final term is a prefix -- an already-typed word keeps its
    # exact meaning, so a completed query is never silently widened.
    check(len(cs.search(cfg, "headph bone")) == 0,
          "search: a non-final term stays exact, never a prefix")
    check(len(cs.search(cfg, "salt or pepper")) == 0,
          "search: lowercase 'or' is a term, not an operator -> prefix path")

    # Deliberate FTS5 operator expressions are passed through untouched.
    check(len(cs.search(cfg, "headphones OR notion")) == 2,
          "search: uppercase OR still ORs, not rewritten into a prefix query")
    check(len(cs.search(cfg, "NEAR(titanium cyanoacrylate)")) == 1,
          "search: NEAR() still parses")
    check(len(cs.search(cfg, "headphon*")) == 1,
          "search: a user's own trailing * is honoured, not double-starred")

    # The footguns: FTS5 rejects a bare '*' outright ("unknown special
    # query"), and a punctuation-only term must not reach it unquoted.
    check(isinstance(cs.search(cfg, "*"), list),
          "search: a bare * is rescued, never a crash")
    check(isinstance(cs.search(cfg, "--"), list),
          "search: punctuation-only query -> no crash")
    check(cs.search(cfg, "   ") == [],
          "search: whitespace-only query -> empty, never a SQL error")

    # A db built before the summary column existed gains it on connect.
    _legacy_db = tmp / "legacy.db"
    _c = sqlite3.connect(str(_legacy_db))
    _c.execute("CREATE TABLE conversations (id INTEGER PRIMARY KEY, skill TEXT, file TEXT,"
               " path TEXT UNIQUE, title TEXT NOT NULL DEFAULT '', topic TEXT)")
    _c.commit()
    _c.close()
    _c = cs.connect(_legacy_db)
    check("summary" in {r[1] for r in _c.execute("PRAGMA table_info(conversations)")},
          "connect: pre-summary db gains the summary column")
    _c.close()

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

    # Archived captures (an index decay parks them in conversations/archive/) stay
    # searchable and resumable, but are never digested (fleet-config#1346).
    jd = skills / "journal-daily" / "conversations"
    arch = jd / "archive"
    arch.mkdir()
    old = "2026-06-02-0900-journal-daily-old00001.md"
    (arch / old).write_text(
        cc.render_markdown("d", [("user", "remember the marmalade experiment")],
                           header=cc.capture_header(OTHER_SID, "claude",
                                                    "2026-06-02T09:00:00")),
        encoding="utf-8")
    (arch / "deeper").mkdir()
    (arch / "deeper" / "2026-06-03-0900-nested-zzzz0001.md").write_text(
        cc.render_markdown("d", [("user", "nested quokka note")]), encoding="utf-8")
    (arch / "notes.txt").write_text("quokka", encoding="utf-8")
    cs.sync(cfg)
    hits = cs.search(cfg, "marmalade", skill="journal-daily")
    check(len(hits) == 1 and hits[0]["file"] == old and hits[0]["date"] == "2026-06-02",
          "search sync: archived capture is searchable, right skill and date")
    check(hits[0]["resume"] == f"claude --resume {OTHER_SID}" and hits[0]["resumable"],
          "search sync: archived capture still resumes from its header sid")
    check(cs.search(cfg, "quokka") == [],
          "search sync: nested dirs and non-.md files in archive/ are not scanned")
    only_archive = tmp / "only-archive"
    (only_archive / "archive").mkdir(parents=True)
    (only_archive / "archive" / old).write_text("x", encoding="utf-8")
    check(ci.index_dir(only_archive, "x") == 0 and not (only_archive / ci.INDEX_NAME).exists(),
          "index_dir: archived captures are never digested or given an index entry")
    (arch / old).unlink()
    cs.sync(cfg)
    check(cs.search(cfg, "marmalade") == [],
          "search sync: archived capture deleted from disk is pruned like any other")
finally:
    shutil.rmtree(tmp, ignore_errors=True)


# ---- the owner's title outranks digest and body matches (fleet-config#1419) ----
# Nine conversations mention the word in their digest (and body); one has it as
# its whole owner title and says almost nothing else. Before the title had its own
# FTS column it sat at digest weight inside a long digest and ranked behind them.
WORD = "kestrel"
tmp = Path(tempfile.mkdtemp(prefix="conv_title_"))
try:
    root = tmp / "life-os"
    cdir = root / ".claude" / "skills" / "journal-daily" / "conversations"
    cdir.mkdir(parents=True)
    titled = "2026-09-01-0900-journal-daily-title000.md"
    names = [titled] + [f"2026-08-{d:02d}-0900-journal-daily-other{d:03d}.md" for d in range(1, 10)]
    idx_entries, titles_map = {}, {titled: WORD}
    for name in names:
        is_titled = name == titled
        (cdir / name).write_text(
            cc.render_markdown("d", [("user", f"we looked at the {WORD} again today" * (1 if is_titled else 3))]),
            encoding="utf-8")
        body = ("- **Topic:** an unrelated afternoon of errands and planning\n"
                "- **Summary:** talked through the week and the shopping list at length, "
                "with nothing else of note to record" if is_titled else
                f"- **Topic:** the {WORD} {WORD} migration notes\n"
                f"- **Summary:** the {WORD} came up again; more {WORD} detail followed")
        idx_entries[name] = ci.Entry(file=name, mtime=(cdir / name).stat().st_mtime,
                                     turns=2, body=body, sid="", agent="claude")
    (cdir / "index.md").write_text(ci.render_index("journal-daily", idx_entries), encoding="utf-8")
    (cdir / ci.TITLES_NAME).write_text(json.dumps(titles_map), encoding="utf-8")
    cfg = cc.CaptureConfig(root=root, routing="skills", conversations_dir="conversations",
                           skills_dir=".claude/skills", active_marker=".active-skill")
    check(cs.sync(cfg) == 10, "title rank: synced the fixture conversations")
    hits = cs.search(cfg, WORD)
    check(len(hits) == 10 and hits[0]["file"] == titled,
          "title rank: a full-title match ranks first, ahead of digest and body matches")
    check(cs.search(cfg, WORD[:-2])[0]["file"] == titled,
          "title rank: the still-typing prefix of the title also ranks it first")
    check(set(hits[0]) == {"skill", "file", "path", "date", "slug", "sid", "agent", "title",
                           "topic", "summary", "decisions", "open_loops", "turns", "rank",
                           "resume", "resumable"},
          "title rank: the result shape (the app-launcher --json contract) is unchanged")
    # Untitled conversations rank exactly as before: no title column content to add.
    check(len(cs.search(cfg, "migration")) == 9
          and all(h["file"] != titled for h in cs.search(cfg, "migration")),
          "title rank: a digest-only query is unaffected by the title column")

    # A db built before the title column existed (digest, body) upgrades in place on
    # connect, keeps answering queries, and `sync` re-reads every row to settle it.
    cs.db_path(cfg).unlink()
    _old = sqlite3.connect(str(cs.db_path(cfg)))
    _old.executescript(
        "CREATE TABLE conversations (id INTEGER PRIMARY KEY, skill TEXT NOT NULL, file TEXT NOT NULL,"
        " path TEXT NOT NULL UNIQUE, date TEXT NOT NULL DEFAULT '', slug TEXT NOT NULL DEFAULT '',"
        " sid TEXT NOT NULL DEFAULT '', agent TEXT NOT NULL DEFAULT '', title TEXT NOT NULL DEFAULT '',"
        " topic TEXT NOT NULL DEFAULT '', summary TEXT NOT NULL DEFAULT '',"
        " decisions TEXT NOT NULL DEFAULT '', open_loops TEXT NOT NULL DEFAULT '',"
        " turns INTEGER NOT NULL DEFAULT 0, mtime REAL NOT NULL DEFAULT 0);"
        "CREATE VIRTUAL TABLE conv_fts USING fts5(digest, body);"
        f"INSERT INTO conversations (id, skill, file, path, title, mtime)"
        f" VALUES (1, 'journal-daily', 'legacy.md', 'legacy-path', '{WORD}', 1);"
        f"INSERT INTO conv_fts (rowid, digest, body) VALUES (1, '{WORD} legacy digest', 'legacy body');")
    _old.commit()
    _old.close()
    _c = cs.connect(cs.db_path(cfg))
    check([r[1] for r in _c.execute("PRAGMA table_info(conv_fts)")] == ["title", "topic", "digest", "body"],
          "title rank: a pre-title index gains the title (and topic) column in place")
    _c.close()
    check([h["file"] for h in cs.search(cfg, "legacy")] == ["legacy.md"],
          "title rank: the upgraded index still answers before any re-sync")
    check(cs.sync(cfg) == 10 and cs.search(cfg, WORD)[0]["file"] == titled,
          "title rank: the next sync settles the upgraded index, title first")
    check(not cs.search(cfg, "legacy"), "title rank: the upgrade's stale row is pruned by sync")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

# ---- a visible-title hit outranks every digest/body hit, whatever the length (#1521) ----
# FTS5 bm25() saturates term frequency inside one column and normalises by the whole
# row's length, so a title hit on a long capture lost to short rows that merely mention
# the word. The title tier must hold regardless of capture length.
WORD = "osprey"
tmp = Path(tempfile.mkdtemp(prefix="conv_tier_"))
try:
    root = tmp / "life-os"
    cdir = root / ".claude" / "skills" / "journal-daily" / "conversations"
    cdir.mkdir(parents=True)
    filler = " ".join(f"filler{i % 97}" for i in range(20_000))
    owner_titled = "2026-09-01-0900-journal-daily-owner0001.md"
    topic_titled = "2026-09-02-0900-journal-daily-topic0001.md"
    summary_only = [f"2026-08-{d:02d}-0900-journal-daily-digest{d:03d}.md" for d in range(1, 6)]
    body_only = [f"2026-07-{d:02d}-0900-journal-daily-bodyon{d:03d}.md" for d in range(1, 6)]
    idx_entries, titles_map = {}, {owner_titled: f"{WORD} notes"}
    for name in [owner_titled, topic_titled, *summary_only, *body_only]:
        long_capture = name in (owner_titled, topic_titled)
        text = (f"we looked at the {WORD} once. {filler}" if long_capture else
                (f"{WORD} {WORD} {WORD} chat" if name in body_only else "a short chat"))
        (cdir / name).write_text(cc.render_markdown("d", [("user", text)]), encoding="utf-8")
        if name == owner_titled:
            digest = "- **Topic:** errands\n- **Summary:** a long unrelated afternoon"
        elif name == topic_titled:
            digest = f"- **Topic:** {WORD} sighting\n- **Summary:** a long unrelated afternoon"
        elif name in summary_only:
            digest = f"- **Topic:** errands\n- **Summary:** {WORD} {WORD} {WORD} came up, {WORD} again"
        else:
            digest = "- **Topic:** errands\n- **Summary:** nothing of note"
        idx_entries[name] = ci.Entry(file=name, mtime=(cdir / name).stat().st_mtime,
                                     turns=2, body=digest, sid="", agent="claude")
    (cdir / "index.md").write_text(ci.render_index("journal-daily", idx_entries), encoding="utf-8")
    (cdir / ci.TITLES_NAME).write_text(json.dumps(titles_map), encoding="utf-8")
    cfg = cc.CaptureConfig(root=root, routing="skills", conversations_dir="conversations",
                           skills_dir=".claude/skills", active_marker=".active-skill")
    check(cs.sync(cfg) == 12, "visible-title tier: synced the fixture conversations")
    order = [h["file"] for h in cs.search(cfg, WORD)]
    check(len(order) == 12, f"visible-title tier: every match is returned (got {len(order)})")
    check(order[0] == owner_titled,
          "visible-title tier: an owner-title hit on a long capture ranks first")
    check(order[1] == topic_titled,
          "visible-title tier: an untitled conversation's topic hit ranks next, still on a long capture")
    check(set(order[2:7]) == set(summary_only) and set(order[7:]) == set(body_only),
          "visible-title tier: then digest-only hits, then body-only hits")
    check([h["file"] for h in cs.search(cfg, WORD[:-2])][:2] == [owner_titled, topic_titled],
          "visible-title tier: holds for the still-typing prefix too")
    check(cs.search(cfg, f'"{WORD}" OR zzzabsent')[0]["file"] == owner_titled,
          "visible-title tier: holds for a raw FTS5 operator query")

    # A db whose conv_fts predates the topic column upgrades in place, idempotently,
    # keeps answering before any re-sync, and the next sync settles it.
    cs.db_path(cfg).unlink()
    _old = sqlite3.connect(str(cs.db_path(cfg)))
    _old.executescript(
        "CREATE TABLE conversations (id INTEGER PRIMARY KEY, skill TEXT NOT NULL, file TEXT NOT NULL,"
        " path TEXT NOT NULL UNIQUE, date TEXT NOT NULL DEFAULT '', slug TEXT NOT NULL DEFAULT '',"
        " sid TEXT NOT NULL DEFAULT '', agent TEXT NOT NULL DEFAULT '', title TEXT NOT NULL DEFAULT '',"
        " topic TEXT NOT NULL DEFAULT '', summary TEXT NOT NULL DEFAULT '',"
        " decisions TEXT NOT NULL DEFAULT '', open_loops TEXT NOT NULL DEFAULT '',"
        " turns INTEGER NOT NULL DEFAULT 0, mtime REAL NOT NULL DEFAULT 0);"
        "CREATE VIRTUAL TABLE conv_fts USING fts5(title, digest, body);"
        "INSERT INTO conversations (id, skill, file, path, mtime) VALUES (1, 'journal-daily', 'old.md', 'old-path', 1);"
        f"INSERT INTO conv_fts (rowid, title, digest, body) VALUES (1, '', '{WORD} errands', 'old body');")
    _old.commit()
    _old.close()
    for _ in range(2):
        _c = cs.connect(cs.db_path(cfg))
        check([r[1] for r in _c.execute("PRAGMA table_info(conv_fts)")] == ["title", "topic", "digest", "body"],
              "visible-title tier: a pre-topic index gains the topic column in place (idempotent)")
        _c.close()
    check([h["file"] for h in cs.search(cfg, WORD)] == ["old.md"],
          "visible-title tier: the upgraded index still answers before any re-sync")
    check(cs.sync(cfg) == 12 and cs.search(cfg, WORD)[0]["file"] == owner_titled
          and not [h for h in cs.search(cfg, WORD) if h["file"] == "old.md"],
          "visible-title tier: the next sync settles the upgraded index, title first")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

_h.report_and_exit("test_conversation_search")
