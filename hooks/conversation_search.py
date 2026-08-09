"""Ranked full-text search over captured conversations (fleet-config#586).

``conversation_capture`` writes one markdown capture per conversation and
``conversation_index`` digests each into ``index.md`` / ``index.json``. Those
answer *"what happened recently"* for one skill. This answers the other
question — *"which conversation was the one about X, out of everything, across
every skill"* — and hands back the identity needed to reopen it.

Two layers are searched at different weights, because they fail differently:

  * the **digest** (topic / decisions / open loops) — dense and abstracted, so a
    match here is usually the conversation you meant; weighted high.
  * the **full capture text** — verbatim, so it finds the offhand detail no
    digest would ever mention ("the ferry booking reference"); weighted low so
    it ranks below a digest hit rather than drowning it.

Storage is a single SQLite **FTS5** database (stdlib, no dependency) for the
whole project, so one query spans every skill. It is a pure derivative of the
captures plus the index: **deleting it is always safe**, and the next indexer
run rebuilds it. That property is deliberate — it means this file can never
become the thing that loses data.

Kept up to date by :func:`sync`, called at the end of each
``conversation_index`` run (which the ``session_index`` SessionStart hook
already triggers lazily), so search is current without any new scheduling.

Usage (invoke the resolved Python path directly — a bare ``py``/``python`` is
not reliably on ``PATH`` here; see ``_lib.find_python_executable``)::

    …/python.exe hooks/conversation_search.py --project life-os --query "ferry licenses"
    …/python.exe hooks/conversation_search.py --project life-os --query "notion" --skill journal-daily
    …/python.exe hooks/conversation_search.py --project life-os --query "roast" --since 2026-07-01 --json
    …/python.exe hooks/conversation_search.py --project life-os --rebuild
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib  # noqa: E402
from conversation_capture import (  # noqa: E402
    CaptureConfig,
    capture_config_from_project,
    parse_capture_header,
    strip_capture_header,
)

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except (AttributeError, ValueError):
    pass

logger = logging.getLogger("conversation_search")

DB_NAME = ".search.db"
# The indexer runs detached from SessionStart, so a query and a sync can overlap.
BUSY_TIMEOUT_MS = 5000
# Cap the body text stored per conversation. Keeps the db small and the FTS
# tokenizer fast; a match beyond this depth in a very long transcript is rare
# enough not to justify the size.
MAX_BODY_CHARS = 200_000

# How each harness reopens a conversation by id. Native resume is per-harness —
# a Claude session cannot be continued inside Codex — so this maps an agent to
# *its own* command rather than pretending one command fits all. An agent absent
# from this map yields no resume command, and the caller must present the
# conversation as readable-but-not-resumable rather than guessing.
RESUME_COMMANDS = {
    "claude": "claude --resume {sid}",
    "codex": "codex resume {sid}",
}


def resume_command(agent: str, sid: str) -> str:
    """The command that reopens this conversation, or ``""`` when unknown."""
    if not sid:
        return ""
    template = RESUME_COMMANDS.get((agent or "claude").lower())
    return template.format(sid=sid) if template else ""


# ------------------------------------------------------------------ schema

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id         INTEGER PRIMARY KEY,
    skill      TEXT NOT NULL,
    file       TEXT NOT NULL,
    path       TEXT NOT NULL UNIQUE,
    date       TEXT NOT NULL DEFAULT '',
    slug       TEXT NOT NULL DEFAULT '',
    sid        TEXT NOT NULL DEFAULT '',
    agent      TEXT NOT NULL DEFAULT '',
    topic      TEXT NOT NULL DEFAULT '',
    decisions  TEXT NOT NULL DEFAULT '',
    open_loops TEXT NOT NULL DEFAULT '',
    turns      INTEGER NOT NULL DEFAULT 0,
    mtime      REAL NOT NULL DEFAULT 0
);
CREATE VIRTUAL TABLE IF NOT EXISTS conv_fts USING fts5(digest, body);
"""


def db_path(cfg: CaptureConfig) -> Path:
    """The project's search db — one file, covering every skill."""
    return cfg.root / cfg.conversations_dir / DB_NAME


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=BUSY_TIMEOUT_MS / 1000)
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    conn.executescript(_SCHEMA)
    return conn


# ------------------------------------------------------------------- sync


def sync(cfg: CaptureConfig, *, rebuild: bool = False) -> int:
    """Bring the search db in line with the captures on disk. Returns # upserted.

    Incremental: a conversation is re-read only when its mtime moved. Rows whose
    capture has disappeared are dropped, so the db can't accumulate ghosts of
    deleted or archived conversations.

    Fail-open like the rest of this pipeline — a locked or corrupt db logs and
    returns 0 rather than breaking the indexer that called it (and since the db
    is a pure derivative, ``--rebuild`` always fixes it).
    """
    # Imported lazily: conversation_index imports this module for `sync`, so a
    # module-level import here would be circular.
    import conversation_index as ci

    try:
        conn = connect(db_path(cfg))
    except (sqlite3.Error, OSError) as exc:
        # OSError too: `connect` creates the parent directory, which can fail on
        # a read-only or missing tree. The hook path already swallows this, but
        # the standalone CLI would otherwise traceback instead of degrading.
        logger.error("search db unavailable: %s", exc)
        return 0

    upserted = 0
    try:
        if rebuild:
            conn.executescript(
                "DELETE FROM conversations; DELETE FROM conv_fts;"
            )
        known = {row[0]: row[1] for row in conn.execute("SELECT path, mtime FROM conversations")}
        seen: set[str] = set()

        for conv_dir, label in ci.conversations_dirs(cfg):
            if not conv_dir.is_dir():
                continue
            entries = ci.parse_index(conv_dir / ci.INDEX_NAME)
            for path in conv_dir.glob("*.md"):
                if path.name == ci.INDEX_NAME:
                    continue
                key = str(path)
                seen.add(key)
                try:
                    mtime = path.stat().st_mtime
                except OSError:
                    continue
                if not rebuild and key in known and abs(known[key] - mtime) < 1:
                    continue
                try:
                    text = path.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                entry = entries.get(path.name)
                fields = ci._digest_fields(entry.body) if entry else {}
                date, slug = ci._split_name(path.name)
                header = parse_capture_header(text)
                row = {
                    "skill": label,
                    "file": path.name,
                    "path": key,
                    "date": date,
                    "slug": slug,
                    "sid": (entry.sid if entry and entry.sid else header.get("sid", "")),
                    "agent": (entry.agent if entry and entry.agent else header.get("agent", "")),
                    "topic": fields.get("topic", ""),
                    "decisions": fields.get("decisions", ""),
                    "open_loops": fields.get("open_loops", ""),
                    "turns": entry.turns if entry else 0,
                    "mtime": mtime,
                }
                digest_text = " ".join(
                    v for v in (slug, row["topic"], row["decisions"], row["open_loops"]) if v
                )
                body_text = strip_capture_header(text)[:MAX_BODY_CHARS]
                _upsert(conn, row, digest_text, body_text)
                upserted += 1

        for stale in [p for p in known if p not in seen]:
            _delete(conn, stale)
        conn.commit()
    except sqlite3.Error as exc:
        logger.error("search sync failed: %s", exc)
        return 0
    finally:
        conn.close()
    return upserted


def _delete(conn: sqlite3.Connection, path: str) -> None:
    row = conn.execute("SELECT id FROM conversations WHERE path = ?", (path,)).fetchone()
    if row:
        conn.execute("DELETE FROM conv_fts WHERE rowid = ?", (row[0],))
        conn.execute("DELETE FROM conversations WHERE id = ?", (row[0],))


def _upsert(conn: sqlite3.Connection, row: dict, digest_text: str, body_text: str) -> None:
    """Replace this conversation's row and its FTS twin, keeping rowids aligned.

    ``conv_fts.rowid`` is always ``conversations.id`` — that join is the whole
    reason a contentless FTS table isn't used here; it keeps the ranked query a
    single statement.
    """
    _delete(conn, row["path"])
    cur = conn.execute(
        """INSERT INTO conversations
           (skill, file, path, date, slug, sid, agent, topic, decisions, open_loops, turns, mtime)
           VALUES (:skill, :file, :path, :date, :slug, :sid, :agent, :topic,
                   :decisions, :open_loops, :turns, :mtime)""",
        row,
    )
    conn.execute(
        "INSERT INTO conv_fts (rowid, digest, body) VALUES (?, ?, ?)",
        (cur.lastrowid, digest_text, body_text),
    )


# ------------------------------------------------------------------ query

# Digest matches outrank body matches 10:1 — see the module docstring.
_BM25 = "bm25(conv_fts, 10.0, 1.0)"


def _quote_terms(query: str) -> str:
    """Re-express a query as quoted terms ANDed together.

    The fallback for a query FTS5 refuses to parse. Bare punctuation a user
    naturally types — ``notion:``, ``--resume``, ``what's`` — is FTS5 *syntax*,
    so the raw query is tried first (operators keep working for anyone who wants
    them) and this rescues the rest instead of surfacing a SQL error.
    """
    terms = [t.replace('"', "") for t in query.split()]
    return " ".join(f'"{t}"' for t in terms if t)


def search(
    cfg: CaptureConfig,
    query: str,
    *,
    skill: Optional[str] = None,
    since: Optional[str] = None,
    limit: int = 20,
) -> "list[dict]":
    """Ranked matches, best first. Returns ``[]`` when the db is absent."""
    path = db_path(cfg)
    if not path.exists():
        return []
    try:
        conn = connect(path)
    except (sqlite3.Error, OSError) as exc:
        logger.error("search db unavailable: %s", exc)
        return []

    where = ["conv_fts MATCH ?"]
    params: list = [query]
    if skill:
        where.append("c.skill = ?")
        params.append(skill)
    if since:
        where.append("c.date >= ?")
        params.append(since)
    sql = (
        "SELECT c.skill, c.file, c.path, c.date, c.slug, c.sid, c.agent, "
        "       c.topic, c.decisions, c.open_loops, c.turns, "
        f"      {_BM25} AS rank "
        "FROM conv_fts JOIN conversations c ON c.id = conv_fts.rowid "
        f"WHERE {' AND '.join(where)} ORDER BY rank LIMIT ?"
    )
    try:
        try:
            rows = conn.execute(sql, [*params, limit]).fetchall()
        except sqlite3.OperationalError:
            params[0] = _quote_terms(query)
            if not params[0].strip():
                return []
            try:
                rows = conn.execute(sql, [*params, limit]).fetchall()
            except sqlite3.OperationalError as exc:
                logger.error("unparseable query %r: %s", query, exc)
                return []
    finally:
        conn.close()

    cols = ["skill", "file", "path", "date", "slug", "sid", "agent",
            "topic", "decisions", "open_loops", "turns", "rank"]
    out = []
    for row in rows:
        item = dict(zip(cols, row))
        item["resume"] = resume_command(item["agent"], item["sid"])
        item["resumable"] = bool(item["resume"])
        out.append(item)
    return out


# -------------------------------------------------------------------- CLI


def _render(results: "list[dict]") -> str:
    if not results:
        return "no matches"
    lines = []
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. [{r['skill']}] {r['date']} · {r['topic'] or r['slug']}")
        if r["decisions"] and r["decisions"].lower() != "none":
            lines.append(f"     decisions: {r['decisions']}")
        if r["open_loops"] and r["open_loops"].lower() != "none":
            lines.append(f"     open loops: {r['open_loops']}")
        lines.append(f"     file: {r['path']}")
        # An unresumable hit is stated as such — the transcript is gone or the
        # agent has no resume command. Never printed as a silent omission.
        lines.append(
            f"     resume: {r['resume']}" if r["resumable"]
            else "     resume: unavailable (no stored session id)"
        )
        lines.append("")
    return "\n".join(lines).rstrip()


def resolve_config(project: Optional[str], cwd: Optional[str]) -> Optional[CaptureConfig]:
    reg = _lib.load_registry()
    if project:
        match = next((p for p in reg.projects if p.name == project), None)
    else:
        match = _lib.detect_project(Path(cwd or "."), reg)
    return capture_config_from_project(match) if match else None


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    ap = argparse.ArgumentParser(description="Search captured conversations.")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--project", help="project name from projects.toml")
    g.add_argument("--cwd", help="resolve the project by a cwd path")
    ap.add_argument("--query", help="search terms")
    ap.add_argument("--skill", help="restrict to one skill")
    ap.add_argument("--since", help="only conversations on/after this YYYY-MM-DD")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--rebuild", action="store_true",
                    help="drop and rebuild the index from captures, then exit")
    args = ap.parse_args()

    cfg = resolve_config(args.project, args.cwd)
    if cfg is None:
        print("project not found or not opted into capture", file=sys.stderr)
        return 1

    if args.rebuild:
        n = sync(cfg, rebuild=True)
        print(f"rebuilt — {n} conversation{'' if n == 1 else 's'} indexed")
        return 0

    if not args.query:
        ap.error("--query is required unless --rebuild is given")

    results = search(cfg, args.query, skill=args.skill, since=args.since, limit=args.limit)
    if args.json:
        print(json.dumps(results, indent=2, ensure_ascii=False))
    else:
        print(_render(results))
    return 0


if __name__ == "__main__":
    sys.exit(main())
