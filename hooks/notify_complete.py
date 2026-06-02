"""Deterministic skill-completion Slack ping — one canonical format per skill.

Each ``issue-*`` skill ends by calling this with structured args instead of
hand-assembling a message. The format and the real GitHub URL are built **here,
in Python** — not by the model — so every completion ping is byte-identical and
carries a correct, live link. The leading mark (✅ 🆕 🚦 🏁 🚀 📊 🔄) is a
glanceable status cue. See `docs/slack-workflow.md`.

Opt-in: a silent no-op unless a ``slack_notify_channel`` is configured (project
table or ``[global]``) in ``hooks/projects.toml``. Never blocks — any gh,
network, or config error is logged and the process still exits 0, so a
notification failure can't break or delay a skill.

Usage::

    py ~/.claude/hooks/notify_complete.py --kind finish --issue 30 --pr 31 --pr-url https://github.com/owner/repo/pull/31
    py ~/.claude/hooks/notify_complete.py --kind add    --issue 30
    py ~/.claude/hooks/notify_complete.py --kind start  --issue 30 --summary "review the diff, then /issue-finish"
    py ~/.claude/hooks/notify_complete.py --kind yolo   --issue 30 --pr 31 --pr-url https://github.com/owner/repo/pull/31
    py ~/.claude/hooks/notify_complete.py --kind batch  --passed 2 --total 3
    py ~/.claude/hooks/notify_complete.py --kind audit  --comment-url https://github.com/ferraroroberto/claude-config/issues/18#issuecomment-123 --summary "3 audited, 2 issues filed, 24 unchanged"
    py ~/.claude/hooks/notify_complete.py --kind cleanup --summary documentation --merged 5 --review 2
    py ~/.claude/hooks/notify_complete.py --kind recap --summary "5 skills swept, 3 proposals"

For ``--kind cleanup`` (the closing roll-up of a ``/cleanup-fleet`` swarm) pass
``--summary`` (the bucket name), ``--merged`` (sonnet issues YOLO'd to a merged
PR) and ``--review`` (opus issues built and awaiting ``/issue-finish``). This is
the *final* aggregate ping — the per-issue ``🚀 Shipped`` pings each sonnet
agent already fired (carrying their own PR links) are kept, not suppressed.

Pass ``--pr-url`` whenever the full PR URL is already known (e.g. from ``gh pr
create`` output). The helper will use that URL directly and look up the title
via the absolute URL — which works regardless of the caller's CWD. Without
``--pr-url`` the helper falls back to a CWD-relative ``gh pr view <N>`` lookup,
which fails silently when CWD is not the project repo.

For ``--kind audit`` pass ``--comment-url`` (the GitHub comment permalink posted
by ``/audit-fleet``) and ``--summary`` (e.g. "3 audited, 2 issues filed"). The
Slack ping links directly to the comment so the user reaches the full digest in
one click.
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


def lookup(
    kind: str,
    issue: Optional[str],
    pr: Optional[str],
    pr_url: Optional[str] = None,
    comment_url: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """Best-effort ``(title, url)`` from GitHub for this ping.

    PR-linking kinds: if ``pr_url`` is supplied the URL is used as-is and the
    title is looked up via the absolute URL (works from any CWD). Without
    ``pr_url`` falls back to a CWD-relative ``gh pr view <N>`` which fails
    silently when the caller is not inside the project repo. Issue-linking kinds
    always use a CWD-relative ``gh issue view`` lookup. Audit kind returns the
    comment_url directly with no title lookup. ``(None, None)`` on any gh /
    network error so the message still goes out link-less.
    """
    if kind == "audit":
        return None, comment_url
    if kind in _PR_KINDS:
        if pr_url:
            # Absolute URL: works from any directory.
            data = gh_json(["pr", "view", pr_url, "--json", "title"])
            return data.get("title"), pr_url
        if pr:
            data = gh_json(["pr", "view", str(pr), "--json", "title,url"])
            return data.get("title"), data.get("url")
        return None, None
    if issue:
        data = gh_json(["issue", "view", str(issue), "--json", "title,url"])
        return data.get("title"), data.get("url")
    return None, None


def build_message(
    kind: str,
    *,
    issue: Optional[str] = None,
    title: Optional[str] = None,
    url: Optional[str] = None,
    summary: Optional[str] = None,
    passed: Optional[str] = None,
    total: Optional[str] = None,
    merged: Optional[str] = None,
    review: Optional[str] = None,
) -> str:
    """Assemble the canonical ping text (no @mention prefix). Pure / testable.

    Leads with a glanceable status mark. A missing ``title`` or ``url`` is
    dropped cleanly — no dangling " · " or double spaces.
    """
    name = f" {title}" if title else ""
    link = f" · {url}" if url else ""
    if kind == "add":
        return f"🆕 Filed #{issue}{name}{link}"
    if kind == "start":
        tail = f" {summary.strip()}" if summary and summary.strip() else ""
        return f"🚦 #{issue}{name} — ready to validate.{tail}{link}"
    if kind == "finish":
        return f"✅ Done #{issue}{name} — PR merged{link}"
    if kind == "yolo":
        return f"🚀 Shipped #{issue}{name} — PR{link}"
    if kind == "batch":
        return f"🏁 Batch done: {passed}/{total} passed — /issue-finish each branch to ship"
    if kind == "audit":
        summary_part = f" — {summary}" if summary else ""
        return f"📊 Fleet audit{summary_part}{link}"
    if kind == "recap":
        summary_part = f" — {summary}" if summary else ""
        return f"🔄 Weekly recap{summary_part}"
    if kind == "cleanup":
        bucket = f" {summary.strip()}" if summary and summary.strip() else ""
        parts: List[str] = []
        if merged is not None:
            parts.append(f"{merged} merged")
        # Easy/silent runs spawn no opus agents, so a 0 review count is noise — drop it.
        if review is not None and str(review).strip() not in ("", "0"):
            parts.append(f"{review} awaiting review")
        tail = f": {', '.join(parts)}" if parts else ""
        return f"🧹 Cleanup{bucket}{tail}"
    return f"✅ Done #{issue}{name}{link}"  # defensive fallback


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Send a deterministic skill-completion Slack ping."
    )
    parser.add_argument(
        "--kind", required=True,
        choices=["add", "start", "finish", "yolo", "batch", "audit", "cleanup", "recap"]
    )
    parser.add_argument("--issue", help="Issue number (shown as #N).")
    parser.add_argument("--pr", help="PR number, for finish/yolo (linked).")
    parser.add_argument(
        "--pr-url",
        dest="pr_url",
        help="Full PR URL (e.g. https://github.com/owner/repo/pull/31). "
             "When supplied the URL is used directly and the title lookup uses "
             "the absolute URL, so it works regardless of CWD.",
    )
    parser.add_argument(
        "--comment-url",
        dest="comment_url",
        help="Full GitHub comment permalink, for audit. Linked directly in the ping.",
    )
    parser.add_argument("--summary", help="One concise summary line, for start/audit.")
    parser.add_argument("--passed", help="Passed count, for batch.")
    parser.add_argument("--total", help="Total count, for batch.")
    parser.add_argument("--merged", help="Merged-PR count, for cleanup.")
    parser.add_argument("--review", help="Awaiting-review count, for cleanup.")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)

    channel, user, _name = _lib.resolve_slack_target(Path(os.getcwd()))
    if not channel:
        return 0  # opt-in: not configured → silent no-op

    title, url = (None, None)
    if args.kind not in ("batch", "cleanup", "recap"):
        title, url = lookup(
            args.kind, args.issue, args.pr,
            pr_url=args.pr_url, comment_url=getattr(args, "comment_url", None),
        )

    text = build_message(
        args.kind,
        issue=args.issue,
        title=title,
        url=url,
        summary=args.summary,
        passed=args.passed,
        total=args.total,
        merged=args.merged,
        review=args.review,
    )
    # The @mention decision is single-sourced in slack_notify.notify() (off by
    # default); pass the resolved user id and let it decide.
    slack_notify.notify(text, channel=str(channel), user=user)
    return 0


if __name__ == "__main__":
    sys.exit(main())
