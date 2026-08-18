"""Tier-1 conversation index — a cheap, searchable layer over raw captures.

``conversation_capture`` (the Stop hook) writes one verbatim markdown capture per
conversation, rewriting it every turn-end (cheap, no LLM). This adds the *middle
tier*: per conversations-dir, a ``index.md`` with one distilled entry per
conversation — topic, decisions, open loops — pointing back at the raw capture.
A consumer (e.g. a life-os skill) loads the index at startup and only ``Read``s a
full transcript when a specific past conversation is referenced.

Digesting is **decoupled from capture** — running it on the Stop hook would fire
an LLM call every turn-end. Instead the indexer runs lazily (the ``session_index``
SessionStart hook invokes it), so a conversation is digested **once, after it has
settled**. Each entry is keyed on the capture *filename*; ``supersede_prior`` in
the capture hook already collapses a conversation to one file before it settles.

Generic + ``projects.toml``-driven, mirroring the capture hook: a project opts in
with ``capture = true`` and a ``capture_routing`` of ``"flat"`` (one
conversations dir) or ``"skills"`` (per-skill dirs). Fail-open: if the hub is
unreachable an entry is skipped and retried next run (see :mod:`hub_client`).

Two triggers invoke this module (fleet-config#673): ``session_index.py``, on
``SessionStart`` (the original, still the fallback/catch-up path), and
``conversation_capture.py``, detached, ~60s after a ``Stop`` write — so a
closed conversation is digested near close rather than only the next time a
session starts. The latter passes ``--delay-seconds`` (see :func:`apply_delay`)
so the run lands after this module's own ``SETTLE_SECONDS`` window, and fires
once per turn during an active conversation harmlessly — the settle window and
the unchanged-mtime check below make a run against a capture that hasn't
settled, or hasn't changed, a near-instant no-op.

Usage (from anywhere — invoke the resolved Python path directly, not a bare
``py``/``python``, which is not reliably on ``PATH`` on this machine; see
``_lib.find_python_executable``)::

    E:/automation/fleet-config/.venv/Scripts/python.exe hooks/conversation_index.py --project life-os      # one project by name
    E:/automation/fleet-config/.venv/Scripts/python.exe hooks/conversation_index.py --cwd E:/automation/life-os
    E:/automation/fleet-config/.venv/Scripts/python.exe hooks/conversation_index.py --all                  # every opted-in project
    E:/automation/fleet-config/.venv/Scripts/python.exe hooks/conversation_index.py --all --force          # ignore settle, re-digest
    E:/automation/fleet-config/.venv/Scripts/python.exe hooks/conversation_index.py --project life-os --delay-seconds 60  # sleep, then index
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib  # noqa: E402
import hub_client  # noqa: E402
from conversation_capture import (  # noqa: E402
    CaptureConfig,
    capture_config_from_project,
    parse_capture_header,
    scan_known_skills,
    strip_capture_header,
)

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except (AttributeError, ValueError):
    pass

logger = logging.getLogger("conversation_index")

INDEX_NAME = "index.md"
# Machine-readable twin of index.md, written by the same function from the same
# entries (fleet-config#586) so the two can't drift. index.md stays the
# skill-facing surface a SKILL.md reads; index.json is the API for the search
# CLI and for app-launcher's Life OS conversation browser.
INDEX_JSON_NAME = "index.json"
# A capture younger than this is still "settling" (its session may not have fully
# ended). Small on purpose: at SessionStart the previous conversation is over, so
# this only guards the narrow race of a Stop write landing as a new session begins.
SETTLE_SECONDS = 45
# Cap the transcript sent to the digest model — keeps the call cheap.
MAX_DIGEST_CHARS = 12_000

# Everything from this marker to EOF is owned by the consumer's decay/recap step
# (monthly "period summary" blocks for squashed-away old conversations) and is
# round-tripped verbatim — the indexer only manages the per-conversation
# ``<!-- idx -->`` entries above it.
DECAY_MARKER = "<!-- decay-zone: period summaries below are preserved across re-indexing -->"

_ENTRY_RE = re.compile(
    r"<!-- idx (?P<attrs>[^>]*?)-->\n(?P<body>.*?)(?=\n<!-- idx |\Z)", re.DOTALL
)
_ATTR_RE = re.compile(r'(\w+)="?([^"\s]+)"?')
_NAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-(\d{4})-(.+?)\.md$")
# Pulls a digest field back out of a rendered bullet. Anchored on the bold label
# rather than stripped punctuation: a plain `lstrip("-*• ")` also eats the `**`
# that opens the label, so the match silently never fires and every field lands
# in index.json empty.
_FIELD_RE = re.compile(
    r"^[-*•\s]*\*\*(Topic|Decisions|Open loops):\*\*\s*(.*)$", re.IGNORECASE
)
_TOKEN_SUFFIX_RE = re.compile(r"(?:-[0-9a-f]{8})+$")

DIGEST_PROMPT = (
    "You are writing a compact index entry for a past assistant/user "
    "conversation so it can be found later without re-reading the whole "
    "transcript. Output EXACTLY three lines, nothing else:\n"
    "Topic: <one line on what it was about>\n"
    "Decisions: <concrete decisions or outcomes, or 'none'>\n"
    "Open loops: <unresolved threads / follow-ups, or 'none'>\n\n"
    "Transcript:\n\n"
)


@dataclass
class Entry:
    file: str
    mtime: float
    turns: int
    body: str
    # Resume identity, lifted from the capture's own header. Optional by
    # design: captures written before fleet-config#586 have no header, and the
    # healer leaves a capture sid-less when its transcript is gone rather than
    # inventing one. Consumers must treat "" as "searchable, not resumable".
    sid: str = ""
    agent: str = ""


def _split_name(filename: str) -> "tuple[str, str]":
    """``2026-06-12-1530-foo-bar-ab12cd34.md`` -> ``('2026-06-12', 'foo bar')``."""
    m = _NAME_RE.match(filename)
    if not m:
        return filename, filename
    date, _hhmm, slug = m.groups()
    slug = _TOKEN_SUFFIX_RE.sub("", slug)
    return date, slug.replace("-", " ")


def parse_index(path: Path) -> "dict[str, Entry]":
    """Read an existing ``index.md`` into ``{filename: Entry}`` (decay zone excluded)."""
    entries: "dict[str, Entry]" = {}
    if not path.exists():
        return entries
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return entries
    text = text.split(DECAY_MARKER, 1)[0]
    for m in _ENTRY_RE.finditer(text):
        attrs = dict(_ATTR_RE.findall(m.group("attrs")))
        fn = attrs.get("file")
        if not fn:
            continue
        try:
            mtime = float(attrs.get("mtime", 0) or 0)
        except ValueError:
            mtime = 0.0
        try:
            turns = int(attrs.get("turns", 0) or 0)
        except ValueError:
            turns = 0
        body = re.sub(r"^### .*\n?", "", m.group("body").strip(), count=1).strip()
        entries[fn] = Entry(
            file=fn, mtime=mtime, turns=turns, body=body,
            sid=attrs.get("sid", ""), agent=attrs.get("agent", ""),
        )
    return entries


def _digest_fields(body: str) -> "dict[str, str]":
    """Split a rendered entry body back into its three digest fields.

    ``index.json`` carries them separately so a consumer can show just the topic
    or search the fields at different weights; ``index.md`` keeps the prose
    bullets. Parsing the body the writer produced keeps one source of truth for
    the digest rather than storing it twice.
    """
    out = {"topic": "", "decisions": "", "open_loops": ""}
    for line in body.splitlines():
        m = _FIELD_RE.match(line)
        if m:
            out[m.group(1).lower().replace(" ", "_")] = m.group(2).strip()
    return out


def index_json_payload(label: str, entries: "dict[str, Entry]") -> "list[dict]":
    """``index.json``'s entry list — newest first, same order as ``index.md``."""
    rows = []
    for e in sorted(entries.values(), key=lambda e: e.file, reverse=True):
        date, slug = _split_name(e.file)
        rows.append({
            "skill": label,
            "file": e.file,
            "date": date,
            "slug": slug,
            "turns": e.turns,
            "mtime": e.mtime,
            "sid": e.sid,
            "agent": e.agent,
            **_digest_fields(e.body),
        })
    return rows


def decay_tail(path: Path) -> str:
    """The decay zone (``DECAY_MARKER`` to EOF) of an existing index, or ``""``."""
    if not path.exists():
        return ""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    if DECAY_MARKER in text:
        return DECAY_MARKER + text.split(DECAY_MARKER, 1)[1]
    return ""


def render_index(label: str, entries: "dict[str, Entry]", tail: str = "") -> str:
    lines = [
        f"# Conversation index — {label}",
        "",
        "_Auto-generated by `hooks/conversation_index.py` — newest first. Each "
        "entry distils one raw capture in this folder; `Read` that file only when "
        "you need the full transcript._",
        "",
    ]
    for e in sorted(entries.values(), key=lambda e: e.file, reverse=True):
        date, slug = _split_name(e.file)
        # Attrs are *extended*, never reshaped — `_recap` and life-os's
        # `_shared/bootstrap.md` both parse this grammar (fleet-config#586).
        extra = "".join(
            f' {k}="{v}"' for k, v in (("sid", e.sid), ("agent", e.agent)) if v
        )
        lines.append(
            f'<!-- idx file="{e.file}" mtime={int(e.mtime)} turns={e.turns}{extra} -->'
        )
        lines.append(f"### {date} · {slug}")
        lines.append(e.body)
        lines.append("")
    body = "\n".join(lines).rstrip() + "\n"
    if tail.strip():
        body += "\n" + tail.strip() + "\n"
    return body


def digest(text: str) -> "str | None":
    """Distil a capture's markdown into a 3-line body, or ``None`` if hub down."""
    out = hub_client.complete(DIGEST_PROMPT + text[:MAX_DIGEST_CHARS], max_tokens=300)
    if not out:
        return None
    topic = decisions = loops = ""
    for raw in out.splitlines():
        line = raw.strip().lstrip("-*• ").strip()
        low = line.lower()
        if low.startswith("topic:"):
            topic = line[len("topic:"):].strip()
        elif low.startswith("decisions:"):
            decisions = line[len("decisions:"):].strip()
        elif low.startswith("open loops:"):
            loops = line[len("open loops:"):].strip()
    if not topic:
        topic = out.strip().splitlines()[0][:200]
    return (
        f"- **Topic:** {topic or '—'}\n"
        f"- **Decisions:** {decisions or 'none'}\n"
        f"- **Open loops:** {loops or 'none'}"
    )


def index_dir(conv_dir: Path, label: str, *, force: bool = False) -> int:
    """Bring one conversations dir's ``index.md`` up to date. Returns # digested."""
    if not conv_dir.is_dir():
        return 0
    index_path = conv_dir / INDEX_NAME
    entries = parse_index(index_path)
    captures = {p.name: p for p in conv_dir.glob("*.md") if p.name != INDEX_NAME}

    changed = False
    for fn in [fn for fn in entries if fn not in captures]:  # prune missing files
        del entries[fn]
        changed = True

    now = time.time()
    digested = 0
    for fn, path in captures.items():
        try:
            st = path.stat()
        except OSError:
            continue
        if not force and (now - st.st_mtime) < SETTLE_SECONDS:
            continue  # still settling — next run
        prior = entries.get(fn)
        if prior is not None and abs(prior.mtime - st.st_mtime) < 1 and not force:
            # Already digested and unchanged — but its identity may still be
            # missing (a legacy entry, or one whose capture the healer has since
            # backfilled with a preserved mtime). Refreshing that is a file read,
            # not an LLM call, so it happens here instead of forcing a re-digest
            # of the whole archive (fleet-config#586).
            head = _read_header(path)
            if head and (prior.sid, prior.agent) != (head.get("sid", ""), head.get("agent", "")):
                prior.sid = head.get("sid", "")
                prior.agent = head.get("agent", "")
                changed = True
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        # The header is identity, not conversation — digesting it would spend
        # tokens on a uuid and invite the model to describe it.
        body = digest(strip_capture_header(text))
        if body is None:
            continue  # hub down — fail-open, retry next run
        turns = text.count("**You**:") + text.count("**Claude**:")
        head = parse_capture_header(text)
        entries[fn] = Entry(
            file=fn, mtime=st.st_mtime, turns=turns, body=body,
            sid=head.get("sid", ""), agent=head.get("agent", ""),
        )
        changed = True
        digested += 1

    # `changed` alone would never backfill index.json for a dir that was fully
    # indexed before this file existed, so a missing twin also triggers a write —
    # but only where there is something to write. Without the `entries` guard an
    # empty conversations/ (a skill nobody has used yet) gets a header-only
    # index.md and an empty index.json conjured into it on the next run.
    json_path = conv_dir / INDEX_JSON_NAME
    if changed or (entries and not json_path.exists()):
        try:
            index_path.write_text(
                render_index(label, entries, decay_tail(index_path)), encoding="utf-8"
            )
        except OSError as exc:
            logger.error("Could not write %s: %s", index_path, exc)
        try:
            json_path.write_text(
                json.dumps(index_json_payload(label, entries), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.error("Could not write %s: %s", json_path, exc)
    return digested


def _read_header(path: Path) -> dict:
    """The capture's identity header, read from its first lines only."""
    try:
        with path.open(encoding="utf-8", errors="ignore") as fh:
            head = "".join(next(fh, "") for _ in range(6))
    except OSError:
        return {}
    return parse_capture_header(head)


def conversations_dirs(cfg: CaptureConfig) -> "list[tuple[Path, str]]":
    """The (conversations_dir, label) pairs to index for a project's routing."""
    if cfg.routing == "skills":
        skills_root = cfg.root / cfg.skills_dir
        pairs = [
            (skills_root / skill / "conversations", skill)
            for skill in sorted(scan_known_skills(skills_root))
        ]
        archive = cfg.root / cfg.conversations_dir / "_archive"
        if archive.is_dir():
            pairs.append((archive, "_archive"))
        return pairs
    return [(cfg.root / cfg.conversations_dir, cfg.root.name)]


def index_project(cfg: CaptureConfig, *, force: bool = False) -> int:
    total = 0
    for conv_dir, label in conversations_dirs(cfg):
        total += index_dir(conv_dir, label, force=force)
    # Keep the search index current off the back of the run that already walked
    # these dirs (fleet-config#586) — no second schedule, and the db is a pure
    # derivative, so a failure here is logged and never fails the index run.
    try:
        import conversation_search

        conversation_search.sync(cfg)
    except Exception as exc:  # noqa: BLE001 - fail-open by design
        logger.error("search sync skipped: %s", exc)
    return total


def opted_in_projects() -> "list":
    """Every projects.toml project with ``capture = true``."""
    return [p for p in _lib.load_registry().projects if p.extra.get("capture")]


def apply_delay(seconds: float) -> None:
    """Sleep before indexing, when a Stop-triggered near-close run asks for it.

    Split out from :func:`main` so the acceptance suite can assert the delay is
    requested correctly (by stubbing :func:`time.sleep`) without an actual
    multi-second sleep in the test run (fleet-config#673). A no-op for the
    ``SessionStart`` trigger, which never passes ``--delay-seconds``.
    """
    if seconds > 0:
        time.sleep(seconds)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    ap = argparse.ArgumentParser(description="Build/refresh conversation indexes.")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--all", action="store_true", help="every opted-in project")
    g.add_argument("--project", help="one project by projects.toml name")
    g.add_argument("--cwd", help="resolve the project by a cwd path")
    ap.add_argument("--force", action="store_true",
                    help="ignore the settle window and re-digest existing entries")
    ap.add_argument("--delay-seconds", type=float, default=0.0,
                    help="sleep this long before indexing — used by the "
                         "Stop-triggered near-close trigger (fleet-config#673)")
    args = ap.parse_args()
    apply_delay(args.delay_seconds)

    reg = _lib.load_registry()
    if args.all:
        projects = opted_in_projects()
    elif args.project:
        projects = [p for p in reg.projects if p.name == args.project]
    else:  # --cwd
        proj = _lib.detect_project(Path(args.cwd), reg)
        projects = [proj] if proj else []

    total = 0
    for project in projects:
        cfg = capture_config_from_project(project)
        if cfg is None:
            logger.warning("%s is not opted into capture — skipping", project.name)
            continue
        n = index_project(cfg, force=args.force)
        if n:
            logger.info("%s: %d entr%s (re)digested", project.name, n, "y" if n == 1 else "ies")
        total += n
    logger.info("done — %d entr%s updated", total, "y" if total == 1 else "ies")
    return 0


if __name__ == "__main__":
    sys.exit(main())
