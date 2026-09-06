"""Recover a stored transcript no live Stop hook ever captured (fleet-config#785).

A session is lost forever when its Stop hook never fires, or fires against a
hosting process that had not yet loaded the capture hook — the concrete cause
recovered by hand for two Codex sessions on 2026-09-06, whose hosting
``codex app-server`` had been running for 26 hours before ``codex-hooks.json``
gained a ``Stop`` entry. This is the supported, documented replacement for that
hand-rolled recovery: it walks a harness's own stored-transcript store, finds
sessions whose recorded ``cwd`` resolves to a registered ``capture = true``
project, and runs each one through the same
:func:`conversation_capture.write_capture` the live hook uses — so a backfilled
capture is identical in shape to a live one, and re-running the backfill over
an already-captured session is the same idempotent no-op ``write_capture``
already gives the live hook (unchanged digest -> no write).

Unlike a live capture, a backfilled session's filename is stamped from the
transcript's own recorded start time, not ``datetime.now()`` — recovering a
session from days ago must not file it as though it happened during the
recovery run.

Skill routing for a backfilled session has no ``.active-skill`` marker to read
(the session that would have written it is long over), so it always falls
through to :func:`conversation_capture.infer_skill_from_transcript`.

Usage (invoke the resolved Python path directly — a bare ``py``/``python`` is
not reliably on ``PATH`` here; see ``_lib.find_python_executable``)::

    …/python.exe hooks/conversation_backfill.py --project life-os
    …/python.exe hooks/conversation_backfill.py --project life-os --harness codex
    …/python.exe hooks/conversation_backfill.py --project life-os --dry-run
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Iterator, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib  # noqa: E402
from transcript_readers import read_transcript  # noqa: E402
from conversation_capture import (  # noqa: E402
    CaptureConfig,
    capture_config_from_project,
    infer_skill_from_transcript,
    scan_known_skills,
    transcript_start_time,
    write_capture,
)

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except (AttributeError, ValueError):
    pass

logger = logging.getLogger("conversation_backfill")

# Overridable so acceptance tests stay hermetic (same pattern as
# session_state.sessions_registry_dir's CLAUDE_SESSIONS_DIR).


def claude_store() -> Path:
    root = os.environ.get("CLAUDE_TRANSCRIPTS_DIR")
    return Path(root) if root else Path.home() / ".claude" / "projects"


def codex_store() -> Path:
    root = os.environ.get("CODEX_SESSIONS_DIR")
    return Path(root) if root else Path.home() / ".codex" / "sessions"


def _peek_cwd(path: Path, *, max_lines: int = 50) -> str:
    """Best-effort ``cwd`` from the first few JSON lines of a stored transcript.

    A cheap peek — cwd identification doesn't need the full parse
    :func:`transcript_readers.read_transcript` does, and these stores can hold
    thousands of files. Checks both a top-level ``cwd`` (Claude entries) and a
    ``payload.cwd`` (Codex ``session_meta``).
    """
    try:
        with path.open(encoding="utf-8-sig") as fh:
            for _ in range(max_lines):
                line = fh.readline()
                if not line:
                    return ""
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(entry, dict):
                    continue
                value = entry.get("cwd")
                if isinstance(value, str) and value:
                    return value
                payload = entry.get("payload")
                if isinstance(payload, dict):
                    value = payload.get("cwd")
                    if isinstance(value, str) and value:
                        return value
    except OSError:
        return ""
    return ""


def _belongs_to_project(cwd_value: str, project_name: str, registry) -> bool:
    match = _lib.detect_project(Path(cwd_value), registry)
    return match is not None and match.name == project_name


def find_claude_sources(project_name: str, registry) -> "list[Path]":
    store = claude_store()
    if not store.is_dir():
        return []
    found = []
    for project_dir in store.iterdir():
        if not project_dir.is_dir():
            continue
        for path in project_dir.glob("*.jsonl"):
            cwd_value = _peek_cwd(path)
            if cwd_value and _belongs_to_project(cwd_value, project_name, registry):
                found.append(path)
    return found


def find_codex_sources(project_name: str, registry) -> "list[Path]":
    store = codex_store()
    if not store.is_dir():
        return []
    found = []
    for path in store.rglob("*.jsonl"):
        cwd_value = _peek_cwd(path)
        if cwd_value and _belongs_to_project(cwd_value, project_name, registry):
            found.append(path)
    return found


_FINDERS = {"claude": find_claude_sources, "codex": find_codex_sources}


def backfill_project(
    project, cfg: CaptureConfig, *, harnesses: Optional[list] = None, dry_run: bool = False,
    registry=None,
) -> "dict[str, int]":
    """Capture every stored transcript for ``project`` that has no capture yet.

    Returns ``{"scanned": n, "written": n}``. Routes through the same
    ``skill``/``_archive`` decision the live hook makes, using transcript
    inference in place of the (long-gone) ``.active-skill`` marker. Never
    fabricates a resume; never overwrites an unrelated capture — all of that
    stays owned by :func:`conversation_capture.write_capture`.
    """
    registry = registry or _lib.load_registry()
    allowed = harnesses or project.extra.get("capture_harnesses", ["claude"])
    known_skills = scan_known_skills(cfg.root / cfg.skills_dir) if cfg.routing == "skills" else set()
    stats = {"scanned": 0, "written": 0}
    for harness in allowed:
        finder = _FINDERS.get(harness)
        if finder is None:
            logger.warning("no transcript store known for harness %r — skipping", harness)
            continue
        for source in finder(project.name, registry):
            stats["scanned"] += 1
            transcript = read_transcript(source, harness=harness)
            if transcript.status != "ok" or not transcript.messages:
                continue
            out_dir = cfg.root / cfg.conversations_dir
            if cfg.routing == "skills":
                skill = infer_skill_from_transcript(transcript, known_skills, cfg.skills_dir)
                out_dir = (cfg.root / cfg.skills_dir / skill / "conversations") if skill else out_dir / "_archive"
            stamp = transcript_start_time(transcript.entries)
            if write_capture(cfg, out_dir, source, transcript, filename_time=stamp, dry_run=dry_run):
                stats["written"] += 1
                logger.info("%s %s session %s -> %s",
                            "would backfill" if dry_run else "backfilled", harness,
                            transcript.session_id or source.name, out_dir)
    return stats


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    ap = argparse.ArgumentParser(
        description="Capture stored transcripts a live Stop hook never captured."
    )
    ap.add_argument("--project", required=True, help="project name from projects.toml")
    ap.add_argument("--harness", action="append", choices=sorted(_FINDERS),
                     help="restrict to one harness (repeatable); default: the "
                          "project's capture_harnesses")
    ap.add_argument("--dry-run", action="store_true",
                     help="report what would be captured, write nothing")
    args = ap.parse_args()

    registry = _lib.load_registry()
    project = next((p for p in registry.projects if p.name == args.project), None)
    if project is None:
        print(f"unknown project: {args.project}", file=sys.stderr)
        return 1
    cfg = capture_config_from_project(project)
    if cfg is None:
        print(f"{args.project} is not opted into capture", file=sys.stderr)
        return 1

    stats = backfill_project(project, cfg, harnesses=args.harness, dry_run=args.dry_run,
                              registry=registry)

    if stats["written"] and not args.dry_run:
        # Bring index/search up to date immediately — a one-off manual run, not
        # the Stop hook's cheap-fire-every-turn path, so a synchronous index
        # here (rather than conversation_capture's detached trigger) is fine.
        try:
            import conversation_index as ci
            ci.index_project(cfg)
        except Exception as exc:  # noqa: BLE001 - fail-open, matches index_project's own sync
            logger.error("post-backfill indexing skipped: %s", exc)

    verb = "would capture" if args.dry_run else "captured"
    logger.info("scanned %d transcript(s), %s %d", stats["scanned"], verb, stats["written"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
