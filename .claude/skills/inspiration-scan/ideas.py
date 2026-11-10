"""The ideas file, the ideas ledger and their one writer, for /inspiration-scan.

`ideas.json` holds the ideas the owner can still act on; `ideas-ledger.jsonl`
is the append-only record of what became of each one. The Board reads the
first, the chief writes the second only through `scan.py ideas`, and the next
scan never keeps an id the ledger holds. The field names and the Board's
message are the contract in docs/inspiration-ideas.md.

Everything lives in the machine-local state root, never in the repo.

stdlib only.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "skills" / "_lib"))
from active_issue import state_lock  # noqa: E402
from fleet_core.hooks_state import atomic_write_text  # noqa: E402
from render import KIND_LABELS  # noqa: E402
from score import Scored  # noqa: E402

VERSION = 1
IDEAS_FILE = "ideas.json"
LEDGER_FILE = "ideas-ledger.jsonl"
# The link carries the rest of the text; a longer quote would copy the source instead of citing it.
EXCERPT_WORDS = 25
# An idea the owner has not decided on stays on the Board this long; it is already in seen.json,
# so dropping it from the file would lose it for good.
RETENTION_DAYS = 30

STATE_NEW, STATE_APPLIED, STATE_FILTERED = "new", "applied", "filtered"
ATTRIBUTION = ("title", "link", "source", "author", "published", "digest", "score", "model")
_REF = re.compile(r"^([A-Za-z0-9_.-]+)#([0-9]+)$")
EXIT_REFUSED = 2
EXIT_UNKNOWN = 1


class IdeasError(Exception):
    """The ideas file or the ledger could not be read; the caller must not carry on as if it were empty."""


def now_utc() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def excerpt(text: str) -> str:
    words = (text or "").split()
    return " ".join(words) if len(words) <= EXCERPT_WORDS else " ".join(words[:EXCERPT_WORDS]) + "…"


def build_prompt(entry: Scored, digest: str) -> str:
    """The long `/issue-add` text: the idea, why it fits, the experiment, then the `## Source` trace."""
    item = entry.item
    parts = [entry.issue_idea or item.title]
    if entry.why_fits:
        parts.append(f"Why it fits the fleet: {entry.why_fits}")
    if entry.experiment:
        parts.append(f"Experiment to try: {entry.experiment}")
    published = item.published.date().isoformat() if item.published else "not given"
    parts.append("\n".join([
        "## Source", "",
        f"- Source: {item.source} ({KIND_LABELS.get(item.kind, item.kind)})",
        f"- Author: {item.author or 'not given'}",
        f"- Published: {published}",
        f"- Link: {item.link}",
        f"- Digest: {digest}",
        f"- Score: {entry.score}/10, scored by {entry.model or 'unknown model'}"]))
    return "\n\n".join(parts)


def build_idea(entry: Scored, digest: str) -> Dict[str, Any]:
    item = entry.item
    return {"id": item.id, "title": item.title, "link": item.link,
            "source": {"name": item.source, "kind": item.kind}, "author": item.author,
            "published": item.published.isoformat() if item.published else "",
            "excerpt": excerpt(item.text), "digest": digest, "score": entry.score, "model": entry.model,
            "why_fits": entry.why_fits, "experiment": entry.experiment, "suggested_repo": entry.suggested_repo,
            "prompt": build_prompt(entry, digest)}


def ledger_path(root: Path) -> Path:
    return root / LEDGER_FILE


def read_ledger(root: Path) -> List[Dict[str, Any]]:
    """Every ledger row; a line that does not parse is `IdeasError`, because skipping it would re-offer an idea."""
    path = ledger_path(root)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise IdeasError(f"{type(exc).__name__} reading {path.name}") from exc
    rows = []
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError as exc:
            raise IdeasError(f"{path.name} line {number} is not JSON") from exc
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            raise IdeasError(f"{path.name} line {number} is not a ledger row")
        rows.append(row)
    return rows


def read_ideas(root: Path) -> List[Dict[str, Any]]:
    path = root / IDEAS_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as exc:
        raise IdeasError(f"{type(exc).__name__} reading {path.name}") from exc
    rows = data.get("ideas") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise IdeasError(f"{path.name} has no ideas list")
    return [r for r in rows if isinstance(r, dict) and isinstance(r.get("id"), str)]


def write_ideas(root: Path, fresh: List[Dict[str, Any]], decided: set, today: _dt.date) -> int:
    """Rewrite `ideas.json` with today's ideas plus earlier undecided ones; returns how many it holds."""
    cutoff = (today - _dt.timedelta(days=RETENTION_DAYS)).isoformat()
    fresh_ids = {i["id"] for i in fresh}
    carried = [i for i in read_ideas(root)
               if i["id"] not in decided and i["id"] not in fresh_ids and str(i.get("digest", "")) >= cutoff]
    ideas = [i for i in fresh if i["id"] not in decided] + carried
    # runtime-write-exempt: root is the machine-local state root scan.py passes in
    root.mkdir(parents=True, exist_ok=True)
    payload = {"version": VERSION, "digest": today.isoformat(), "ideas": ideas}
    atomic_write_text(root / IDEAS_FILE, json.dumps(payload, indent=1, ensure_ascii=False))
    return len(ideas)


def ledger_ids(root: Path) -> Dict[str, str]:
    """Id to outcome state for everything the ledger holds."""
    return {str(r["id"]): str(r.get("state", "")) for r in read_ledger(root)}


# ---- the writer -----------------------------------------------------------------

def _refused(idea_id: str, reason: str) -> int:
    print(f"IDEAS_REFUSED={idea_id} reason={reason}")
    return EXIT_REFUSED


def _row(idea: Dict[str, Any], state: str, extra: Dict[str, str]) -> Dict[str, Any]:
    row: Dict[str, Any] = {"id": idea["id"], "state": state,
                           "at": now_utc().strftime("%Y-%m-%dT%H:%M:%SZ")}
    row.update(extra)
    row.update({k: idea.get(k, "") for k in ATTRIBUTION})
    return row


def _record(root: Path, idea_id: str, state: str, extra: Dict[str, str]) -> int:
    """Append one outcome row after the refusals; nothing is written on a refusal."""
    try:
        with state_lock(ledger_path(root)):
            done = ledger_ids(root).get(idea_id)
            if done:
                return _refused(idea_id, f"already-{done}")
            match = [i for i in read_ideas(root) if i["id"] == idea_id]
            if not match:
                return _refused(idea_id, "unknown-id")
            line = json.dumps(_row(match[0], state, extra), ensure_ascii=False)
            # runtime-write-exempt: the ledger sits in the same state root
            with ledger_path(root).open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(line + "\n")
    except IdeasError as exc:
        print(f"IDEAS=unknown reason={exc}")
        return EXIT_UNKNOWN
    except OSError as exc:
        print(f"IDEAS=unknown reason={type(exc).__name__} writing the ledger")
        return EXIT_UNKNOWN
    return 0


def cmd_apply(root: Path, idea_id: str, issue: str) -> int:
    if not _REF.match(issue or ""):
        return _refused(idea_id, f"bad-ref ({issue}; want <repo>#<n>)")
    code = _record(root, idea_id, STATE_APPLIED, {"issue": issue})
    if code == 0:
        print(f"IDEA_APPLIED={idea_id} issue={issue}")
    return code


def cmd_filter(root: Path, idea_id: str, note: Optional[str]) -> int:
    code = _record(root, idea_id, STATE_FILTERED, {"note": (note or "").strip()})
    if code == 0:
        print(f"IDEA_FILTERED={idea_id}")
    return code


def _line(idea_id: str, state: str, source: Dict[str, Any], tail: str) -> str:
    return (f"IDEA={idea_id} state={state} score={source.get('score')} "
            f"repo={source.get('suggested_repo') or '-'} {tail} title={source.get('title')}")


def cmd_list(root: Path, state: Optional[str]) -> int:
    try:
        ledger = read_ledger(root)
        pending = read_ideas(root)
    except IdeasError as exc:
        print(f"IDEAS=unknown reason={exc}")
        return EXIT_UNKNOWN
    decided = {r["id"] for r in ledger}
    lines = []
    if state in (None, STATE_NEW):
        lines += [_line(i["id"], STATE_NEW, i, f"digest={i.get('digest')}") for i in pending if i["id"] not in decided]
    for row in ledger:
        if state in (None, row.get("state")):
            applied = row.get("state") == STATE_APPLIED
            tail = f"issue={row.get('issue')}" if applied else f"note={row.get('note') or '-'}"
            lines.append(_line(row["id"], str(row.get("state")), row, tail))
    print("\n".join(lines + [f"IDEAS_ENTRIES={len(lines)}"]))
    return 0


def add_parser(sub: argparse._SubParsersAction) -> None:
    """`ideas list|apply|filter`, registered by `scan.main`."""
    ideas = sub.add_parser("ideas", help="the ideas the Board shows, and the ledger of what became of them")
    verbs = ideas.add_subparsers(dest="verb", required=True)
    listing = verbs.add_parser("list", help="print ideas and their outcome")
    listing.add_argument("--state", choices=(STATE_NEW, STATE_APPLIED, STATE_FILTERED))
    apply = verbs.add_parser("apply", help="record that an idea became an issue")
    apply.add_argument("id")
    apply.add_argument("--issue", required=True, help="<repo>#<n>")
    drop = verbs.add_parser("filter", help="record that the owner turned an idea down")
    drop.add_argument("id")
    drop.add_argument("--note", help="the owner's words")


def run_command(args: argparse.Namespace, root: Path) -> int:
    if args.verb == "list":
        return cmd_list(root, args.state)
    if args.verb == "apply":
        return cmd_apply(root, args.id, args.issue)
    return cmd_filter(root, args.id, args.note)
