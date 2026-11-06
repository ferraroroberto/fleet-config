"""`chief_ops.py proposals`: the weekly learning-log's workflow proposals and their answers ledger.

The weekly `/learning-log` run drafts candidate issues for the owner. This
command owns what must not rest on a model's memory:

1. **Switch.** `chief_weekly_proposals` from `/api/board/chief/settings`, read on
   every call. Only a literal `true` is on. Off, missing or unreadable prints
   `PROPOSALS=off reason=...` and writes nothing, so a run drafts nothing.
2. **Screening.** `publish` keeps at most `MAX_CANDIDATES` candidates, each in a
   public fleet repo with a reason and evidence, and drops one that overlaps an
   open issue's title, repeats another candidate, or was proposed before: a
   declined proposal returns only with evidence its first proposal did not cite.
3. **Delivery.** One `kind=proposals` event goes into the chief mailbox
   (`docs/chief-mailbox.md`), once per ISO week.
4. **Ledger.** `chief-proposals-ledger.json` in the hooks state dir holds every
   proposal and the owner's answer. `context` hands the next run the declined
   list and the accept rates; it never holds private data, because only public
   repos pass screening.

Subcommands: `context`, `publish`, `answer`, `show`.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
import urllib.error
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

import git_run
from active_issue import _iso_z, _now, write_rows
from fleet_core.hooks_state import state_dir

from . import launcher, mailbox
from .heal import load_ledger, update_ledger

LEDGER_FILENAME = "chief-proposals-ledger.json"
SWITCH_KEY = "chief_weekly_proposals"

# The Board's answer sheet takes 0-4 options (docs/chief-plan.md), so one question holds four.
MAX_CANDIDATES = 4
TYPE_LABELS = ("bug", "enhancement", "refactor", "docs", "chore", "test", "perf")
TITLE_MAX = 120
REASON_MAX = 300

# Two titles sharing this fraction of their word tokens read as the same idea.
OVERLAP_THRESHOLD = 0.6
# A proposal the owner never answered counts as unanswered, not declined: silence is no verdict.
UNANSWERED_AFTER = timedelta(days=14)
# The newest declined titles shown to the drafter; older ones stay in the ledger and still screen.
DECLINED_SHOWN = 40

_REF = re.compile(r"^([A-Za-z0-9_.-]+)#([0-9]+)$")
_WORD = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset({"the", "and", "for", "with", "from", "into", "that", "this", "when", "than",
                        "are", "not", "its", "per", "via", "all", "any", "add", "use"})
_OPEN_ISSUE_LIMIT = 300
_REPO_LIMIT = 500


# ---- pure decisions -----------------------------------------------------------

def proposals_switch(reply: Any) -> Optional[str]:
    """None when weekly proposals are on, else the `PROPOSALS=off` line. Unknown is never on."""
    settings = reply.get("settings") if isinstance(reply, dict) else None
    if not isinstance(settings, dict):
        return "PROPOSALS=off reason=settings-unreadable"
    if SWITCH_KEY not in settings:
        return "PROPOSALS=off reason=setting-missing"
    value = settings[SWITCH_KEY]
    if value is True:
        return None
    if value is False:
        return "PROPOSALS=off reason=switch-off"
    return f"PROPOSALS=off reason=setting-not-boolean value={json.dumps(value)}"


def parse_ref(ref: str) -> Optional[Tuple[str, int]]:
    match = _REF.match(ref or "")
    return (match.group(1), int(match.group(2))) if match else None


def week_id(moment: datetime) -> str:
    year, week, _ = moment.isocalendar()
    return f"{year}-W{week:02d}"


def title_tokens(title: str) -> Set[str]:
    return {w for w in _WORD.findall(title.lower()) if len(w) >= 3 and w not in _STOPWORDS}


def title_overlap(a: str, b: str) -> float:
    """Jaccard overlap of two titles' word tokens; 0.0 when either has none."""
    left, right = title_tokens(a), title_tokens(b)
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def same_idea(a: str, b: str) -> bool:
    return title_overlap(a, b) >= OVERLAP_THRESHOLD


def candidate_problem(cand: Any, public_repos: Iterable[str]) -> Optional[str]:
    """Why a candidate cannot be offered, or None when it is well formed and in a public repo."""
    public = set(public_repos)
    if not isinstance(cand, dict):
        return "not-an-object"
    repo, title, reason = cand.get("repo"), cand.get("title"), cand.get("reason")
    if repo not in public:
        return "repo-not-public"
    if not isinstance(title, str) or not title.strip() or len(title) > TITLE_MAX:
        return "bad-title"
    if cand.get("type") not in TYPE_LABELS:
        return "bad-type"
    if not isinstance(reason, str) or not reason.strip() or len(reason) > REASON_MAX:
        return "bad-reason"
    evidence = cand.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        return "no-evidence"
    for ref in evidence:
        parsed = parse_ref(ref) if isinstance(ref, str) else None
        if parsed is None or parsed[0] not in public:
            return "bad-evidence-ref"
    return None


def effective_answer(entry: Dict[str, Any], now: datetime) -> str:
    """`accepted`, `declined`, `pending` or `unanswered` (never answered past the window)."""
    answer = entry.get("answer")
    if answer in ("accepted", "declined"):
        return answer
    try:
        stamp = datetime.fromisoformat(str(entry.get("proposed_at")).replace("Z", "+00:00"))
    except ValueError:
        return "unanswered"
    return "unanswered" if now - stamp > UNANSWERED_AFTER else "pending"


def _prior_problem(cand: Dict[str, Any], entries: List[Dict[str, Any]], now: datetime) -> Optional[str]:
    """Why a ledger row for the same idea blocks this candidate, or None."""
    for entry in entries:
        if entry.get("repo") != cand["repo"] or not same_idea(entry.get("title", ""), cand["title"]):
            continue
        state = effective_answer(entry, now)
        if state == "declined":
            if set(cand["evidence"]) - set(entry.get("evidence") or []):
                continue  # new evidence reopens a declined idea
            return "declined-no-new-evidence"
        if state in ("accepted", "pending"):
            return f"already-{state}"
    return None


def screen(cands: List[Any], public_repos: Iterable[str], entries: List[Dict[str, Any]],
           open_titles: Dict[str, List[str]], now: datetime) -> Tuple[List[Dict[str, Any]], List[Tuple[str, str]]]:
    """`(kept, dropped)`: at most `MAX_CANDIDATES` kept, each dropped one as `(title, reason)`."""
    kept: List[Dict[str, Any]] = []
    dropped: List[Tuple[str, str]] = []
    for cand in cands:
        label = str(cand.get("title")) if isinstance(cand, dict) else "?"
        why = candidate_problem(cand, public_repos)
        if why is None and any(k["repo"] == cand["repo"] and same_idea(k["title"], cand["title"]) for k in kept):
            why = "duplicate-in-batch"
        if why is None and any(same_idea(t, cand["title"]) for t in open_titles.get(cand["repo"], [])):
            why = "matches-open-issue"
        if why is None:
            why = _prior_problem(cand, entries, now)
        if why is None and len(kept) >= MAX_CANDIDATES:
            why = "over-cap"
        if why is None:
            kept.append(cand)
        else:
            dropped.append((label, why))
    return kept, dropped


def accept_rates(entries: List[Dict[str, Any]], now: datetime) -> Dict[str, Dict[str, Tuple[int, int]]]:
    """`{"type": {label: (accepted, answered)}, "repo": {...}}` over answered proposals only."""
    rates: Dict[str, Dict[str, List[int]]] = {"type": {}, "repo": {}}
    for entry in entries:
        state = effective_answer(entry, now)
        if state not in ("accepted", "declined"):
            continue
        for axis in ("type", "repo"):
            cell = rates[axis].setdefault(str(entry.get(axis)), [0, 0])
            cell[1] += 1
            cell[0] += state == "accepted"
    return {axis: {k: (v[0], v[1]) for k, v in cells.items()} for axis, cells in rates.items()}


def last_week_line(entries: List[Dict[str, Any]], current_week: str) -> str:
    """The digest's `Last week: N proposed, M filed.` from the newest earlier week with proposals."""
    earlier = sorted({e["week"] for e in entries if e.get("week") and e["week"] < current_week})
    if not earlier:
        return "Last week: 0 proposed, 0 filed."
    rows = [e for e in entries if e.get("week") == earlier[-1]]
    return f"Last week: {len(rows)} proposed, {sum(e.get('answer') == 'accepted' for e in rows)} filed."


def build_event(week: str, kept: List[Dict[str, Any]], now: datetime) -> Dict[str, Any]:
    return {
        "v": 2, "id": uuid.uuid4().hex, "kind": "proposals", "event": "WeeklyProposals", "week": week,
        "launcher_sid": None, "repo": None, "number": None, "agent": None, "dispatch_id": None,
        "ts": _iso_z(now),
        "candidates": [{"pid": uuid.uuid4().hex, "repo": c["repo"], "title": c["title"].strip(),
                        "type": c["type"], "reason": c["reason"].strip(), "evidence": list(c["evidence"])}
                       for c in kept],
    }


def ledger_rows(event: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [{**c, "week": event["week"], "event_id": event["id"], "proposed_at": event["ts"], "answer": "pending"}
            for c in event["candidates"]]


# ---- outside reads and the event write (tests replace these) ----------------------

def fetch_public_repos() -> Tuple[Optional[List[str]], str]:
    res = git_run.run_gh(["repo", "list", launcher.DEFAULT_OWNER, "--no-archived", "--source",
                          "--visibility", "public", "--limit", str(_REPO_LIMIT), "--json", "name"],
                         timeout=60, stdin=subprocess.DEVNULL)
    try:
        data = json.loads(res.stdout) if res.returncode == 0 else None
    except ValueError:
        data = None
    if not isinstance(data, list):
        return None, f"gh repo list exit {res.returncode}"
    return [r["name"] for r in data if isinstance(r, dict) and "name" in r], ""


def fetch_open_titles(repo: str) -> Tuple[Optional[List[str]], str]:
    res = git_run.run_gh(["issue", "list", "--repo", f"{launcher.DEFAULT_OWNER}/{repo}", "--state", "open",
                          "--limit", str(_OPEN_ISSUE_LIMIT), "--json", "title"], timeout=60, stdin=subprocess.DEVNULL)
    try:
        data = json.loads(res.stdout) if res.returncode == 0 else None
    except ValueError:
        data = None
    if not isinstance(data, list):
        return None, f"gh issue list {repo} exit {res.returncode}"
    return [i["title"] for i in data if isinstance(i, dict) and isinstance(i.get("title"), str)], ""


def write_event(event: Dict[str, Any]) -> Path:
    """Publish `event` into the chief inbox as `<ms>-<id>.json`, atomically (docs/chief-mailbox.md).

    `write_rows` creates the directory and writes through a `.<name>.<random>.tmp` sibling that
    `os.replace` lands, so a reader never sees a partial file.
    """
    target = mailbox.chief_inbox_dir() / f"{time.time_ns() // 1_000_000}-{event['id']}.json"
    write_rows(target, event)
    return target


# ---- ledger -------------------------------------------------------------------------

def ledger_file() -> Path:
    return state_dir() / LEDGER_FILENAME


def _entry_line(entry: Dict[str, Any], now: datetime) -> str:
    return (f"PROPOSAL={str(entry.get('pid'))[:8]} week={entry.get('week')} {entry.get('repo')} "
            f"type={entry.get('type')} answer={effective_answer(entry, now)} "
            f"issue={entry.get('issue') or '-'} title={entry.get('title')}")


# ---- the commands -------------------------------------------------------------------

def _switch_off(args: argparse.Namespace) -> Optional[str]:
    try:
        reply = launcher._request(args.base_url, "/api/board/chief/settings")
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        return f"PROPOSALS=off reason=settings-unreadable ({exc})"
    return proposals_switch(reply)


def cmd_context(args: argparse.Namespace) -> int:
    off = _switch_off(args)
    if off is not None:
        print(off)
        return 0
    try:
        entries = load_ledger(ledger_file())
    except ValueError as exc:
        print(f"PROPOSALS=unknown reason=ledger ({exc})")
        return 1
    now = _now()
    week = week_id(now)
    print(f"PROPOSALS=on week={week} max={MAX_CANDIDATES} types={','.join(TYPE_LABELS)}")
    print(f"PROPOSALS_LAST_WEEK={last_week_line(entries, week)}")
    declined = [e for e in entries if effective_answer(e, now) == "declined"][-DECLINED_SHOWN:]
    for entry in declined:
        note = f" note={entry['note']}" if entry.get("note") else ""
        print(f"PROPOSALS_DECLINED={entry['repo']} | {entry['title']} | evidence={','.join(entry.get('evidence') or [])}{note}")
    for axis, cells in accept_rates(entries, now).items():
        for label, (accepted, answered) in sorted(cells.items()):
            print(f"PROPOSALS_RATE={axis}:{label} accepted={accepted}/{answered}")
    return 0


def _read_candidates(path: str) -> Tuple[Optional[List[Any]], str]:
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, f"candidates-unreadable ({exc})"
    cands = doc.get("candidates") if isinstance(doc, dict) else doc
    return (cands, "") if isinstance(cands, list) else (None, "candidates-not-a-list")


def _open_titles(repos: Iterable[str]) -> Tuple[Optional[Dict[str, List[str]]], str]:
    titles: Dict[str, List[str]] = {}
    for repo in sorted(set(repos)):
        found, why = fetch_open_titles(repo)
        if found is None:
            return None, why
        titles[repo] = found
    return titles, ""


def cmd_publish(args: argparse.Namespace) -> int:
    off = _switch_off(args)
    if off is not None:
        print(off)
        return 0
    cands, why = _read_candidates(args.file)
    if cands is None:
        print(f"PROPOSALS=unknown reason={why}")
        return 1
    public, why = fetch_public_repos()
    if public is None:
        print(f"PROPOSALS=unknown reason=public-repos ({why})")
        return 1
    # A candidate that is not well formed is dropped before its repo's open issues are read.
    pre_ok = [c for c in cands if candidate_problem(c, public) is None]
    titles, why = _open_titles(c["repo"] for c in pre_ok)
    if titles is None:
        print(f"PROPOSALS=unknown reason=open-issues ({why})")
        return 1
    now = _now()
    week = args.week or week_id(now)
    path = ledger_file()

    def publish_once(entries: List[Dict[str, Any]]) -> Tuple[Optional[Dict[str, Any]], List[Tuple[str, str]]]:
        if any(e.get("week") == week for e in entries):
            return None, []
        kept, dropped = screen(cands, public, entries, titles, now)
        if not kept:
            return {}, dropped
        event = build_event(week, kept, now)
        entries.extend(ledger_rows(event))
        write_event(event)  # raises before the ledger is written, so no row names an undelivered event
        return event, dropped

    try:
        event, dropped = update_ledger(path, publish_once)
    except (ValueError, OSError) as exc:
        print(f"PROPOSALS=unknown reason=ledger-or-inbox ({exc})")
        return 1
    if event is None:
        print(f"PROPOSALS_REFUSED reason=already-published week={week}")
        return 2
    for label, reason in dropped:
        print(f"PROPOSALS_DROPPED={label} reason={reason}")
    if not event:
        print(f"PROPOSALS_PUBLISHED=0 week={week} dropped={len(dropped)}")
        return 0
    print(f"PROPOSALS_PUBLISHED={len(event['candidates'])} event={event['id']} week={week} dropped={len(dropped)}")
    return 0


def cmd_answer(args: argparse.Namespace) -> int:
    if args.issue and parse_ref(args.issue) is None:
        print(f"PROPOSALS_REFUSED={args.pid} reason=bad-ref ({args.issue}; want <repo>#<n>)")
        return 2

    def apply(entries: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        matches = [e for e in entries if str(e.get("pid", "")).startswith(args.pid)]
        if len(matches) != 1:
            return None
        entry = matches[0]
        entry["answer"] = "accepted" if args.accepted else "declined"
        entry["answered_at"] = _iso_z(_now())
        if args.note:
            entry["note"] = args.note
        if args.issue:
            entry["issue"] = args.issue
        return dict(entry)

    try:
        entry = update_ledger(ledger_file(), apply)
    except (ValueError, OSError) as exc:
        print(f"PROPOSALS=unknown reason=ledger ({exc})")
        return 1
    if entry is None:
        print(f"PROPOSALS_REFUSED={args.pid} reason=no-single-match")
        return 2
    print(_entry_line(entry, _now()).replace("PROPOSAL=", "PROPOSAL_ANSWERED=", 1))
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    try:
        entries = load_ledger(ledger_file())
    except ValueError as exc:
        print(f"PROPOSALS=unknown reason=ledger ({exc})")
        return 1
    shown = [e for e in entries if not args.week or e.get("week") == args.week]
    now = _now()
    for entry in shown:
        print(_entry_line(entry, now))
    print(f"PROPOSALS_ENTRIES={len(shown)}")
    return 0


def add_parser(sub: argparse._SubParsersAction) -> None:
    """`proposals context|publish|answer|show`, registered by `chief.cli`."""
    prop = sub.add_parser("proposals", help="weekly learning-log workflow proposals and their answers ledger (fleet-config#1896)")
    verbs = prop.add_subparsers(dest="proposals_cmd", required=True)

    ctx = verbs.add_parser("context", help="switch state, last week's line, declined proposals and accept rates")
    ctx.set_defaults(func=cmd_context)

    pub = verbs.add_parser("publish", help="screen candidates and write one kind=proposals mailbox event")
    pub.add_argument("--file", required=True, help="JSON list of {repo,title,type,reason,evidence[]}")
    pub.add_argument("--week", default=None, help="ISO week label, default the current one (tests)")
    pub.set_defaults(func=cmd_publish)

    ans = verbs.add_parser("answer", help="record the owner's answer to one proposal")
    ans.add_argument("pid", help="the proposal id (a unique prefix is enough)")
    group = ans.add_mutually_exclusive_group(required=True)
    group.add_argument("--accepted", action="store_true")
    group.add_argument("--declined", action="store_true")
    ans.add_argument("--note", default=None, help="the owner's note, public-safe text")
    ans.add_argument("--issue", default=None, help="<repo>#<n> of the filed issue")
    ans.set_defaults(func=cmd_answer)

    sh = verbs.add_parser("show", help="print the ledger rows")
    sh.add_argument("--week", default=None)
    sh.set_defaults(func=cmd_show)

    for verb in (ctx, pub, ans, sh):
        verb.add_argument("--base-url", default=launcher.DEFAULT_BASE_URL)
