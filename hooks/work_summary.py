"""Deterministic work-summary for a merged PR — file/LOC shape, no LLM.

Renders a glanceable "shape of the work" from a PR's file list using only
``gh`` (no LLM; stdlib only — hooks run on system Python with no venv). Two
renderings, both built **here in Python** so they're byte-stable:

* :func:`format_block` — the compact roll-up, for the Slack completion ping and
  as the chat report's header::

      📊 +312 −47 · 8 files
         🆕 3 new (+210)  ✏️ 4 changed (+98 −40)  🗑️ 1 deleted (−7)

* :func:`format_table` — a per-file markdown table (status · file · + · −,
  churn-sorted), for the in-chat finish/yolo report where a renderer shows it as
  a real table. Left out of Slack on purpose: Slack mrkdwn has no table support
  and a long file list bloats the mobile push.

Data source: ``gh pr view <ref> --json files,additions,deletions,changedFiles``,
which accepts a PR **number or full URL** (the URL form is CWD-independent, the
way ``notify_complete`` already calls ``gh pr view <pr_url>``). Each file carries
a ``changeType`` (``ADDED`` / ``MODIFIED`` / ``RENAMED`` / ``DELETED`` …) plus
``additions`` / ``deletions``; the authoritative totals come from the top-level
``additions`` / ``deletions`` / ``changedFiles``.

Contract mirrors ``notify_complete``: never raises. Any gh / parse error yields
an empty string from :func:`block_for` / :func:`table_for`, so a caller degrades
to a block-less message instead of crashing a finish.
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from typing import Dict, List, Optional

logger = logging.getLogger("work_summary")

MINUS = "−"  # U+2212 MINUS SIGN — matches the roll-up's "−47", not ASCII "-"

# changeType (GraphQL PatchStatus, via `gh pr view --json files`) → bucket. A new
# or copied file is "new"; a delete is "deleted"; everything else (modified,
# renamed, changed) folds into "changed". Both the GraphQL spelling (DELETED) and
# the REST spelling (removed) are accepted so the bucketing is source-agnostic.
_NEW = {"ADDED", "COPIED"}
_DELETED = {"DELETED", "REMOVED"}

_BUCKET_ICON = {"new": "🆕", "changed": "✏️", "deleted": "🗑️"}
_BUCKET_ORDER = ("new", "changed", "deleted")


def bucket_for(change_type: Optional[str]) -> str:
    """Map a file's ``changeType`` to ``"new"`` / ``"changed"`` / ``"deleted"``.

    Pure / testable. An unknown or missing type folds into ``"changed"`` — the
    safe default, since the only thing that matters visually is whether the file
    was born, killed, or edited, and "edited" is the catch-all.
    """
    ct = (change_type or "").upper()
    if ct in _NEW:
        return "new"
    if ct in _DELETED:
        return "deleted"
    return "changed"


def _plural(n: int, noun: str) -> str:
    """``"1 file"`` / ``"8 files"`` — the one place pluralisation lives."""
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def format_block(data: Dict) -> str:
    """The compact roll-up block. Pure / testable.

    Line 1 is the authoritative total (``+add −del · N files``); line 2 breaks it
    into new / changed / deleted buckets, each dropped cleanly when its count is
    zero. Returns ``""`` when the PR touched no files (so a caller appends
    nothing rather than an empty header).
    """
    files = data.get("files") or []
    if not files:
        return ""

    total_add = int(data.get("additions") or 0)
    total_del = int(data.get("deletions") or 0)
    n_files = int(data.get("changedFiles") or len(files))

    # Per-bucket counts + churn, in one pass.
    counts = {b: {"n": 0, "add": 0, "del": 0} for b in _BUCKET_ORDER}
    for f in files:
        b = counts[bucket_for(f.get("changeType"))]
        b["n"] += 1
        b["add"] += int(f.get("additions") or 0)
        b["del"] += int(f.get("deletions") or 0)

    head = f"📊 +{total_add} {MINUS}{total_del} · {_plural(n_files, 'file')}"

    parts: List[str] = []
    if counts["new"]["n"]:
        parts.append(f"🆕 {counts['new']['n']} new (+{counts['new']['add']})")
    if counts["changed"]["n"]:
        c = counts["changed"]
        parts.append(f"✏️ {c['n']} changed (+{c['add']} {MINUS}{c['del']})")
    if counts["deleted"]["n"]:
        parts.append(f"🗑️ {counts['deleted']['n']} deleted ({MINUS}{counts['deleted']['del']})")

    return head if not parts else f"{head}\n   {'  '.join(parts)}"


def format_table(data: Dict) -> str:
    """A per-file markdown table — status · file · + · −, churn-sorted. Pure.

    For the in-chat report only (a markdown renderer shows it as a real table).
    Rows are ordered by total churn (additions + deletions) descending, ties
    broken by path, so the biggest changes read first. Returns ``""`` when the PR
    touched no files.
    """
    files = data.get("files") or []
    if not files:
        return ""

    rows = sorted(
        files,
        key=lambda f: (-(int(f.get("additions") or 0) + int(f.get("deletions") or 0)),
                       f.get("path") or ""),
    )
    lines = ["| | File | + | − |", "|---|---|--:|--:|"]
    for f in rows:
        icon = _BUCKET_ICON[bucket_for(f.get("changeType"))]
        add = int(f.get("additions") or 0)
        dele = int(f.get("deletions") or 0)
        lines.append(f"| {icon} | `{f.get('path') or '?'}` | +{add} | {MINUS}{dele} |")
    return "\n".join(lines)


def _gh_pr(ref: str) -> Dict:
    """``gh pr view <ref> --json files,additions,deletions,changedFiles`` → dict.

    ``ref`` is a PR number or a full URL (URL works from any CWD). Returns ``{}``
    on a missing gh, a non-zero exit, or unparseable output — never raises. Has
    its own gh shell-out rather than importing ``notify_complete.gh_json`` to
    avoid an import cycle (``notify_complete`` imports this module). Decodes
    stdout as UTF-8 explicitly (Windows ``text=True`` falls back to cp1252 and
    mangles a UTF-8 path before it reaches Slack/the report).
    """
    try:
        proc = subprocess.run(
            ["gh", "pr", "view", str(ref), "--json",
             "files,additions,deletions,changedFiles"],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=20,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.error("gh call failed: %s", exc)
        return {}
    if proc.returncode != 0:
        logger.error("gh exited %s: %s", proc.returncode, proc.stderr.strip()[:200])
        return {}
    try:
        data = json.loads(proc.stdout)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def block_for(ref: str) -> str:
    """The Slack roll-up block for a PR ``ref`` (number or URL). ``""`` on error."""
    return format_block(_gh_pr(ref))


def table_for(ref: str) -> str:
    """The chat per-file table for a PR ``ref`` (number or URL). ``""`` on error."""
    return format_table(_gh_pr(ref))


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Print a deterministic work-summary (roll-up + per-file table) for a PR."
    )
    parser.add_argument("--pr", required=True, help="PR number or full URL.")
    parser.add_argument(
        "--block-only", action="store_true",
        help="Print only the compact roll-up block (no per-file table).",
    )
    args = parser.parse_args(argv)

    # The block is emoji-laden and this CLI exists to be captured (piped into a
    # Slack ping / echoed into the chat report), where Windows stdout falls back
    # to cp1252 and a UTF-8 emoji throws UnicodeEncodeError. Force UTF-8 at the
    # entry point (the documented durable fix).
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8")

    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)

    data = _gh_pr(args.pr)
    block = format_block(data)
    if not block:
        return 0  # no files / lookup failed — print nothing, exit clean
    out = block if args.block_only else f"{block}\n\n{format_table(data)}"
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
