"""Deterministic skill-completion Telegram ping — one canonical format per skill.

Each ``issue-*`` skill ends by calling this with structured args instead of
hand-assembling a message. The format and the real GitHub URL are built **here,
in Python** — not by the model — so every completion ping is byte-identical and
carries a correct, live link. The leading mark (✅ 🆕 🚦 🏁 🚀 📊 🔄) is a
glanceable status cue. See `docs/telegram-workflow.md`.

Opt-in: a silent no-op unless a ``telegram_chat`` is configured (project
table or ``[global]``) in ``hooks/projects.toml``. Never blocks — any gh,
network, or config error is logged and the process still exits 0, so a
notification failure can't break or delay a skill.

Usage (invoke the resolved Python path directly — a bare ``py``/``python`` is
not reliably on ``PATH`` on this machine; see ``_lib.find_python_executable``)::

    E:/automation/fleet-config/.venv/Scripts/python.exe ~/.claude/hooks/notify_complete.py --kind finish --issue 30 --pr 31 --pr-url https://github.com/owner/repo/pull/31
    E:/automation/fleet-config/.venv/Scripts/python.exe ~/.claude/hooks/notify_complete.py --kind add    --issue 30
    E:/automation/fleet-config/.venv/Scripts/python.exe ~/.claude/hooks/notify_complete.py --kind add    --issue 496 --repo ferraroroberto/fleet-config
    E:/automation/fleet-config/.venv/Scripts/python.exe ~/.claude/hooks/notify_complete.py --kind start  --issue 30 --summary "review the diff, then /issue-finish"
    E:/automation/fleet-config/.venv/Scripts/python.exe ~/.claude/hooks/notify_complete.py --kind yolo   --issue 30 --pr 31 --pr-url https://github.com/owner/repo/pull/31
    E:/automation/fleet-config/.venv/Scripts/python.exe ~/.claude/hooks/notify_complete.py --kind batch  --passed 2 --total 3
    E:/automation/fleet-config/.venv/Scripts/python.exe ~/.claude/hooks/notify_complete.py --kind finish-batch --merged 4 --blocked 1
    E:/automation/fleet-config/.venv/Scripts/python.exe ~/.claude/hooks/notify_complete.py --kind audit  --comment-url https://github.com/ferraroroberto/fleet-config/issues/18#issuecomment-123 --summary "3 audited, 2 issues filed, 24 unchanged"
    E:/automation/fleet-config/.venv/Scripts/python.exe ~/.claude/hooks/notify_complete.py --kind cleanup --summary documentation --merged 5 --review 2
    E:/automation/fleet-config/.venv/Scripts/python.exe ~/.claude/hooks/notify_complete.py --kind recap --summary "3 skills swept - alt-text +2, journal-daily +1"   # automatic sweep (no proposals)
    E:/automation/fleet-config/.venv/Scripts/python.exe ~/.claude/hooks/notify_complete.py --kind recap --summary "2 skills consolidated, 4 promoted"               # explicit consolidation
    E:/automation/fleet-config/.venv/Scripts/python.exe ~/.claude/hooks/notify_complete.py --kind learning --comment-url https://github.com/ferraroroberto/fleet-config/issues/131#issuecomment-456 --summary "12 PRs / 8 issues distilled | 2/3 horizon shipped"
    E:/automation/fleet-config/.venv/Scripts/python.exe ~/.claude/hooks/notify_complete.py --kind security --issue 42 --pr 43 --pr-url https://github.com/owner/repo/pull/43 --summary "auto-merged, review the diff"
    E:/automation/fleet-config/.venv/Scripts/python.exe ~/.claude/hooks/notify_complete.py --kind design  --summary "8 swept | 3 drifted | 11 findings filed"

Keep every ``--summary`` **pure ASCII**: a Windows command line is not a
UTF-8-safe channel (fleet-config#507 — a literal ``·`` reached the chat as ``??``),
so spell a multi-part summary's separator with the ASCII token ``|`` and let
:func:`normalize_summary` render it as ``·`` from a Python literal. Mojibake that
is still recoverable is repaired on the way in; a boundary that already replaced
the character with ``?`` is not recoverable by anything.

For ``--kind cleanup`` (the closing roll-up of a ``/cleanup-fleet`` swarm) pass
``--summary`` (the bucket name), ``--merged`` (sonnet issues YOLO'd to a merged
PR) and ``--review`` (opus issues built and awaiting ``/issue-finish``). This is
the *final* aggregate ping — the per-issue ``🚀 Shipped`` pings each sonnet
agent already fired (carrying their own PR links) are kept, not suppressed.

For ``--kind finish-batch`` (the closing roll-up of a ``/issue-finish-batch``
swarm) pass ``--merged`` (branches the finishers shipped) and ``--blocked``
(branches that hit a blocker and need a human). Same contract as ``cleanup``:
the per-issue ``✅ Done`` pings each finisher already fired are kept; this is the
*additional* aggregate.

Pass ``--pr-url`` whenever the full PR URL is already known (e.g. from ``gh pr
create`` output). The helper will use that URL directly and look up the title
via the absolute URL — which works regardless of the caller's CWD. Without
``--pr-url`` the helper falls back to a CWD-relative ``gh pr view <N>`` lookup,
which fails silently when CWD is not the project repo.

Pass ``--repo owner/name`` whenever the issue (or PR-by-number) being pinged
does not necessarily live in the caller's CWD repo — e.g. a skill that just
filed or acted on an issue in an explicitly-named repo. It is threaded onto
the ``gh issue view`` / ``gh pr view`` call as ``-R owner/name`` so the lookup
targets the right repo regardless of CWD. Omitting it preserves today's
CWD-relative inference exactly.

For ``--kind audit`` pass ``--comment-url`` (the GitHub comment permalink posted
by ``/audit-fleet``) and ``--summary`` (e.g. "3 audited, 2 issues filed"). The
ping links directly to the comment so the user reaches the full digest in
one click.

``--kind learning`` is the same contract as ``audit`` — the weekly ``/learning-log``
run posts its narrative as a comment on the learning-log ledger issue, then fires
this ping with that ``--comment-url`` and a one-line ``--summary`` (PRs/issues
distilled · horizon grade) so the phone push links straight to the full log.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib  # noqa: E402
import notify_send  # noqa: E402
import work_summary  # noqa: E402

# Kinds that link a pull request (read from gh pr) vs. an issue (gh issue view).
# `security` links its (auto-merged) fix PR and carries the work-summary block so
# the private review chat shows the file/LOC shape of the change to inspect.
_PR_KINDS = ("finish", "yolo", "security")

# Action-needed kinds — the ping is a call to action the user must respond to,
# so it routes to the "attention" chat, not the activity log (issue #139).
# `cleanup` is conditional: it's action-needed only when issues await review.
# `security` is always action-needed — an audit auto-fix shipped to a public repo
# and the private after-the-fact diff review is the whole point (fleet-config#361).
# Everything else (add, finish, yolo, audit, recap, learning, finish-batch,
# design) is a completed-work record → the "log" chat.
_ATTENTION_KINDS = ("start", "batch", "security")

# The `·` a multi-part summary reads with is assembled **here, from a Python
# source literal**, never carried across the shell boundary as a `--summary`
# character. A skill writes the ASCII token instead — `"8 swept | 2 drifted"` —
# and gets `8 swept · 2 drifted` in the chat. See `_lib.repair_mojibake` for why the
# boundary can't be trusted (fleet-config#507): the emoji and em-dash in every
# message above are Python literals for exactly the same reason, which is why
# they always rendered while the argv-sourced `·` did not.
SUMMARY_SEPARATOR = " · "
_SUMMARY_SEPARATOR_TOKEN = re.compile(r"\s*\|\s*")


def normalize_summary(summary: Optional[str]) -> Optional[str]:
    """Make an argv-sourced ``--summary`` render correctly. Pure / testable.

    Repairs recoverable cp1252 mojibake, then expands the ASCII separator token
    ``|`` into :data:`SUMMARY_SEPARATOR`. No ``--summary`` in the fleet carries a
    literal pipe (they are short status lines, not markdown tables), so the
    expansion is unambiguous — unlike ``notify_send --text``, which does carry
    markdown and therefore only gets the repair.
    """
    if summary is None:
        return None
    return _SUMMARY_SEPARATOR_TOKEN.sub(
        SUMMARY_SEPARATOR, _lib.repair_mojibake(summary) or ""
    )


def category_for(kind: str, *, review: Optional[str] = None) -> str:
    """Map a completion ``--kind`` to its routing category ("attention" / "log").

    Pure / testable. ``start`` (🚦 ready to validate) and ``batch`` (🏁 finish
    each branch) are calls to action despite coming from the completion helper;
    a ``cleanup`` roll-up is action-needed only when ``review`` (issues awaiting
    a human) is a non-zero count.
    """
    if kind in _ATTENTION_KINDS:
        return "attention"
    if kind == "cleanup" and review is not None and str(review).strip() not in ("", "0"):
        return "attention"
    return "log"


# The gh shell-out lives in `_lib` (fleet-config#561) so `work_summary` — which
# this module imports, and which therefore could not import back — reaches the
# same implementation instead of keeping a copy. Re-exported under the original
# name because it is this module's public surface (callers and tests both reach
# `notify_complete.gh_json`).
gh_json = _lib.gh_json


def lookup(
    kind: str,
    issue: Optional[str],
    pr: Optional[str],
    pr_url: Optional[str] = None,
    comment_url: Optional[str] = None,
    repo: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """Best-effort ``(title, url)`` from GitHub for this ping.

    PR-linking kinds: if ``pr_url`` is supplied the URL is used as-is and the
    title is looked up via the absolute URL (works from any CWD, ``repo`` is
    irrelevant there). Without ``pr_url`` falls back to a ``gh pr view <N>``
    lookup, and issue-linking kinds use ``gh issue view <N>`` — both CWD-relative
    unless ``repo`` (``owner/name``) is supplied, in which case it is passed as
    ``-R repo`` so the lookup targets the right repo regardless of CWD. Audit
    kind returns the comment_url directly with no title lookup. ``(None, None)``
    on any gh / network error so the message still goes out link-less.
    """
    if kind in ("audit", "learning"):
        return None, comment_url
    repo_args = ["-R", repo] if repo else []
    if kind in _PR_KINDS:
        if pr_url:
            # Absolute URL: works from any directory.
            data = gh_json(["pr", "view", pr_url, "--json", "title"])
            return data.get("title"), pr_url
        if pr:
            data = gh_json(["pr", "view", str(pr), *repo_args, "--json", "title,url"])
            return data.get("title"), data.get("url")
        return None, None
    if issue:
        data = gh_json(["issue", "view", str(issue), *repo_args, "--json", "title,url"])
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
    blocked: Optional[str] = None,
) -> str:
    """Assemble the canonical ping text. Pure / testable.

    Leads with a glanceable status mark. A missing ``title`` or ``url`` is
    dropped cleanly — no dangling " · " or double spaces. ``summary`` is passed
    through :func:`normalize_summary` first, so an argv-mangled or ASCII-token
    separator renders as a real ``·`` (fleet-config#507).
    """
    summary = normalize_summary(summary)
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
    if kind == "security":
        # No vulnerability detail here — the redacted issue title + generic PR
        # title are all that ride. The call to action is the private diff review
        # (fleet-config#361); a public repo can't hide the fix commit, so the
        # mitigation is a short window + this after-the-fact review, not secrecy.
        tail = f" — {summary.strip()}" if summary and summary.strip() else " — review the diff"
        return f"🔒 Security #{issue}{name}{tail}{link}"
    if kind == "batch":
        return f"🏁 Batch done: {passed}/{total} passed — /issue-finish each branch to ship"
    if kind == "audit":
        summary_part = f" — {summary}" if summary else ""
        return f"📊 Fleet audit{summary_part}{link}"
    if kind == "recap":
        summary_part = f" — {summary}" if summary else ""
        return f"🔄 Weekly recap{summary_part}"
    if kind == "design":
        summary_part = f" — {summary}" if summary else ""
        return f"🎨 Design sweep{summary_part}"
    if kind == "learning":
        summary_part = f" — {summary}" if summary else ""
        return f"📓 Learning log{summary_part}{link}"
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
    if kind == "finish-batch":
        parts = []
        if merged is not None:
            parts.append(f"{merged} merged")
        # A 0/empty blocked count is the happy path — drop the clause, don't show "0 blocked".
        if blocked is not None and str(blocked).strip() not in ("", "0"):
            parts.append(f"{blocked} blocked")
        tail = f": {', '.join(parts)}" if parts else ""
        return f"🏁 Finished batch{tail}"
    return f"✅ Done #{issue}{name}{link}"  # defensive fallback


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Send a deterministic skill-completion Telegram ping."
    )
    parser.add_argument(
        "--kind", required=True,
        choices=["add", "start", "finish", "yolo", "batch", "audit", "cleanup", "recap", "finish-batch", "learning", "security", "design"]
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
        help="Full GitHub comment permalink, for audit / learning. Linked directly in the ping.",
    )
    parser.add_argument(
        "--repo",
        help="Repo the --issue / --pr lives in, as owner/name. Passed as `-R` to the "
             "gh issue view / gh pr view lookup so it works regardless of the caller's "
             "CWD. Omit to keep today's CWD-relative inference.",
    )
    parser.add_argument("--summary", help="One concise summary line, for start/audit/learning/design.")
    parser.add_argument("--passed", help="Passed count, for batch.")
    parser.add_argument("--total", help="Total count, for batch.")
    parser.add_argument("--merged", help="Merged-PR count, for cleanup / finish-batch.")
    parser.add_argument("--review", help="Awaiting-review count, for cleanup.")
    parser.add_argument("--blocked", help="Blocked-branch count, for finish-batch.")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)

    category = category_for(args.kind, review=args.review)
    chat, _name = _lib.resolve_notify_target(Path(os.getcwd()), category=category)
    if not chat:
        return 0  # opt-in: not configured → silent no-op

    title, url = (None, None)
    if args.kind not in ("batch", "cleanup", "recap", "finish-batch"):
        title, url = lookup(
            args.kind, args.issue, args.pr,
            pr_url=args.pr_url, comment_url=getattr(args, "comment_url", None),
            repo=args.repo,
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
        blocked=args.blocked,
    )
    # A merged PR (finish / yolo) carries a compact work-summary roll-up under the
    # canonical line — the file/LOC shape of the change. Built in work_summary
    # (never raises → "" on any gh error), so a stats hiccup degrades to the plain
    # ping. The per-file table is chat-only (a plain-text ping has no tables); only the
    # roll-up rides the ping. build_message stays untouched (its exact output is
    # asserted in tests) — the block is appended here.
    if args.kind in _PR_KINDS:
        block = work_summary.block_for(args.pr_url or args.pr or "")
        if block:
            text = f"{text}\n{block}"
    notify_send.notify(text, chat=str(chat))
    return 0


if __name__ == "__main__":
    sys.exit(main())
