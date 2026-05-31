"""Deterministic skill-completion Slack ping — one canonical format per skill.

Each ``issue-*`` skill ends by calling this with structured args instead of
hand-assembling a message. The format and the real GitHub URL are built **here,
in Python** — not by the model — so every completion ping is byte-identical and
carries a correct, live link. The leading mark (✅ 🆕 🚦 🏁 🚀) also tells
``notify_on_idle`` a job just finished, so it suppresses the redundant idle ping
that would otherwise follow ~60 s later. See `docs/slack-workflow.md`.

Opt-in: a silent no-op unless a ``slack_notify_channel`` is configured (project
table or ``[global]``) in ``hooks/projects.toml``. Never blocks — any gh,
network, or config error is logged and the process still exits 0, so a
notification failure can't break or delay a skill.

Usage::

    py ~/.claude/hooks/notify_complete.py --kind finish --issue 30 --pr 31
    py ~/.claude/hooks/notify_complete.py --kind add    --issue 30
    py ~/.claude/hooks/notify_complete.py --kind start  --issue 30 --summary "review the diff, then /issue-finish"
    py ~/.claude/hooks/notify_complete.py --kind yolo   --issue 30 --pr 31
    py ~/.claude/hooks/notify_complete.py --kind batch  --passed 2 --total 3
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib  # noqa: E402
import slack_notify  # noqa: E402

logger = logging.getLogger("notify_complete")

# Kinds that link a pull request (read from gh pr) vs. an issue (gh issue view).
_PR_KINDS = ("finish", "yolo")


def gh_json(args: List[str]) -> dict:
    """Run ``gh <args>`` and parse its JSON stdout. Returns ``{}`` on any error.

    Never raises: a missing gh, a non-zero exit, or unparseable output all yield
    an empty dict so the caller degrades to a link-less message instead of
    crashing a skill mid-run.
    """
    try:
        proc = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=20)
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


def lookup(kind: str, issue: Optional[str], pr: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """Best-effort ``(title, url)`` from GitHub for this ping.

    PR-linking kinds read the PR; the rest read the issue. ``(None, None)`` when
    gh is unavailable, so the message still goes out — just without title/link.
    """
    if kind in _PR_KINDS and pr:
        data = gh_json(["pr", "view", str(pr), "--json", "title,url"])
    elif issue:
        data = gh_json(["issue", "view", str(issue), "--json", "title,url"])
    else:
        data = {}
    return data.get("title"), data.get("url")


def build_message(
    kind: str,
    *,
    issue: Optional[str] = None,
    title: Optional[str] = None,
    url: Optional[str] = None,
    summary: Optional[str] = None,
    passed: Optional[str] = None,
    total: Optional[str] = None,
) -> str:
    """Assemble the canonical ping text (no @mention prefix). Pure / testable.

    Leads with a terminal mark so ``notify_on_idle`` suppresses the follow-up
    idle ping. A missing ``title`` or ``url`` is dropped cleanly — no dangling
    " · " or double spaces.
    """
    name = f" {title}" if title else ""
    link = f" · {url}" if url else ""
    if kind == "add":
        return f"🆕 Filed #{issue}{name}{link}"
    if kind == "start":
        tail = f" {summary.strip()}" if summary and summary.strip() else ""
        return f"🚦 #{issue}{name} — ready to validate.{tail}"
    if kind == "finish":
        return f"✅ Done #{issue}{name} — PR merged{link}"
    if kind == "yolo":
        return f"🚀 Shipped #{issue}{name} — PR{link}"
    if kind == "batch":
        return f"🏁 Batch done: {passed}/{total} passed — /issue-finish each branch to ship"
    return f"✅ Done #{issue}{name}{link}"  # defensive fallback


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Send a deterministic skill-completion Slack ping."
    )
    parser.add_argument(
        "--kind", required=True, choices=["add", "start", "finish", "yolo", "batch"]
    )
    parser.add_argument("--issue", help="Issue number (shown as #N).")
    parser.add_argument("--pr", help="PR number, for finish/yolo (linked).")
    parser.add_argument("--summary", help="One concise next-step line, for start.")
    parser.add_argument("--passed", help="Passed count, for batch.")
    parser.add_argument("--total", help="Total count, for batch.")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)

    channel, user, _name = _lib.resolve_slack_target(Path(os.getcwd()))
    if not channel:
        return 0  # opt-in: not configured → silent no-op

    title, url = (None, None)
    if args.kind != "batch":
        title, url = lookup(args.kind, args.issue, args.pr)

    text = build_message(
        args.kind,
        issue=args.issue,
        title=title,
        url=url,
        summary=args.summary,
        passed=args.passed,
        total=args.total,
    )
    mention = f"<@{user}> " if user else ""
    slack_notify.notify(f"{mention}{text}", channel=str(channel))
    return 0


if __name__ == "__main__":
    sys.exit(main())
