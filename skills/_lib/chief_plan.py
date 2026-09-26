"""The chief's structured backlog plan -- the writer (fleet-config#1034).

The Board's "Chief's plan" card (app-launcher#1279) renders what the chief
intends to do next: the live lanes, the ordered queue and what is waiting on
Roberto. The handover log (`chief-handover.md`) carries the same intent as
prose for the chief itself; this file carries it as data for a parser. The
format is one contract, documented in `docs/chief-plan.md` -- this module is
its only writer, and `chief_ops.py plan ...` is the only way the chief calls it.

Every write is a locked read-modify-write: load, apply one mutation, validate
the *whole* document, then replace the file atomically (temp file +
`os.replace`, `active_issue.write_rows`). A mutation that would leave an
invalid document raises before anything is written, so the reader never sees
a half-applied or malformed plan. A file this writer cannot parse is refused
too, never silently overwritten -- `plan clear` is the explicit reset.

The writer is deliberately stricter than the readers: it writes only v1's
known fields, single-line strings, and the enumerated statuses. Readers are
tolerant of all of that (unknown fields, unknown statuses) so a future writer
can add to v1 without breaking an older card.

stdlib only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from active_issue import _iso_z, _now, state_lock, write_rows  # noqa: E402
from hooks_state import state_dir  # noqa: E402

STATE_FILENAME = "chief-plan.json"
VERSION = 1

LANE_STATUSES = ("building", "gate", "idle", "waiting")
QUEUE_STATUSES = ("queued", "building", "gate", "merged", "parked", "waiting-roberto")

# "One short line" -- long enough for a real title, short enough for a phone chip row.
MAX_TEXT = 200

TOP_FIELDS = {"version", "updated_at", "lanes", "queue", "waiting_on_roberto"}
LANE_FIELDS = {"repo", "session", "item", "status"}
QUEUE_FIELDS = {"repo", "ref", "title", "status", "note"}
WAITING_FIELDS = {"text", "ref"}

_LOCAL_REF = re.compile(r"#[1-9][0-9]*")
_REPO = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_QUALIFIED_REF = re.compile(r"([A-Za-z0-9][A-Za-z0-9._-]*)(#[1-9][0-9]*)")
_ISO_Z = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def plan_file() -> Path:
    """`~/.claude/hooks/state/chief-plan.json`, honouring `CLAUDE_HOOKS_STATE_DIR`."""
    return state_dir() / STATE_FILENAME


def empty_plan() -> Dict[str, Any]:
    return {"version": VERSION, "updated_at": "", "lanes": [], "queue": [],
            "waiting_on_roberto": []}


# ---- validation --------------------------------------------------------------

def _check_text(errors: List[str], where: str, value: Any, *, required: bool) -> None:
    if not isinstance(value, str):
        errors.append(f"{where}: must be a string")
        return
    if required and not value.strip():
        errors.append(f"{where}: must not be empty")
    if _CONTROL.search(value):
        errors.append(f"{where}: must be one line with no control characters")
    if len(value) > MAX_TEXT:
        errors.append(f"{where}: longer than {MAX_TEXT} characters")


def _check_fields(errors: List[str], where: str, row: Any, allowed: set, required: set) -> bool:
    if not isinstance(row, dict):
        errors.append(f"{where}: must be an object")
        return False
    unknown = sorted(set(row) - allowed)
    if unknown:
        errors.append(f"{where}: unknown field(s) {', '.join(unknown)}")
    missing = sorted(required - set(row))
    if missing:
        errors.append(f"{where}: missing field(s) {', '.join(missing)}")
    return True


def _check_repo(errors: List[str], where: str, value: Any) -> None:
    if not isinstance(value, str) or not _REPO.fullmatch(value):
        errors.append(f"{where}: must be a repo name")


def validate(doc: Any) -> List[str]:
    """Every reason `doc` is not a v1 plan this writer may publish; empty = valid."""
    errors: List[str] = []
    if not _check_fields(errors, "plan", doc, TOP_FIELDS, TOP_FIELDS):
        return errors
    if doc.get("version") != VERSION:
        errors.append(f"version: must be {VERSION}")
    stamp = doc.get("updated_at")
    if not isinstance(stamp, str) or not _ISO_Z.fullmatch(stamp):
        errors.append("updated_at: must be a UTC timestamp like 2026-09-26T14:40:00Z")
    for key in ("lanes", "queue", "waiting_on_roberto"):
        if key in doc and not isinstance(doc[key], list):
            errors.append(f"{key}: must be a list")
    if errors:
        return errors

    lane_repos = set()
    for i, lane in enumerate(doc["lanes"]):
        where = f"lanes[{i}]"
        if not _check_fields(errors, where, lane, LANE_FIELDS, {"repo", "status"}):
            continue
        _check_repo(errors, f"{where}.repo", lane.get("repo"))
        if lane.get("repo") in lane_repos:
            errors.append(f"{where}: a second lane for {lane.get('repo')}")
        lane_repos.add(lane.get("repo"))
        if lane.get("status") not in LANE_STATUSES:
            errors.append(f"{where}.status: must be one of {', '.join(LANE_STATUSES)}")
        if "session" in lane:
            _check_text(errors, f"{where}.session", lane["session"], required=False)
        item = lane.get("item", "")
        if item and (not isinstance(item, str) or not _LOCAL_REF.fullmatch(item)):
            errors.append(f"{where}.item: must be #N or empty")

    seen = set()
    for i, row in enumerate(doc["queue"]):
        where = f"queue[{i}]"
        if not _check_fields(errors, where, row, QUEUE_FIELDS,
                             {"repo", "ref", "title", "status"}):
            continue
        _check_repo(errors, f"{where}.repo", row.get("repo"))
        ref = row.get("ref")
        if not isinstance(ref, str) or not _LOCAL_REF.fullmatch(ref):
            errors.append(f"{where}.ref: must be #N")
        key = (row.get("repo"), ref)
        if key in seen:
            errors.append(f"{where}: {row.get('repo')}{ref} is already queued")
        seen.add(key)
        _check_text(errors, f"{where}.title", row.get("title"), required=True)
        if row.get("status") not in QUEUE_STATUSES:
            errors.append(f"{where}.status: must be one of {', '.join(QUEUE_STATUSES)}")
        if "note" in row:
            _check_text(errors, f"{where}.note", row["note"], required=False)

    for i, row in enumerate(doc["waiting_on_roberto"]):
        where = f"waiting_on_roberto[{i}]"
        if not _check_fields(errors, where, row, WAITING_FIELDS, {"text"}):
            continue
        _check_text(errors, f"{where}.text", row.get("text"), required=True)
        ref = row.get("ref", "")
        if ref and (not isinstance(ref, str) or not _QUALIFIED_REF.fullmatch(ref)):
            errors.append(f"{where}.ref: must be repo#N or empty")
    return errors


# ---- load / write ------------------------------------------------------------

def load(path: Path) -> Dict[str, Any]:
    """The current plan; a missing file is the empty plan.

    Raises ValueError for a file that exists but is not a valid v1 plan -- the
    writer refuses to build on it rather than overwrite it silently.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return empty_plan()
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path.name} is not valid JSON ({exc}); `plan clear` resets it") from exc
    problems = validate(doc)
    if problems:
        raise ValueError(f"{path.name} is not a valid v1 plan ({problems[0]}); "
                         "`plan clear` resets it")
    return doc


def update(
    path: Path,
    mutate: Callable[[Dict[str, Any]], None],
    *,
    now: Optional[datetime] = None,
    reset: bool = False,
) -> Dict[str, Any]:
    """Apply one mutation under the state lock and publish the result atomically.

    Nothing is written unless the mutated document validates. `reset` starts
    from the empty plan instead of loading (the `clear` escape hatch for a
    file this writer cannot parse).
    """
    with state_lock(path):
        doc = empty_plan() if reset else load(path)
        mutate(doc)
        doc["updated_at"] = _iso_z(now or _now())
        problems = validate(doc)
        if problems:
            raise ValueError("; ".join(problems))
        write_rows(path, doc)
    return doc


# ---- mutations (pure, on a loaded document) ----------------------------------

def split_ref(ref: str) -> Tuple[str, str]:
    """`app-launcher#1273` -> (`app-launcher`, `#1273`)."""
    match = _QUALIFIED_REF.fullmatch(ref.strip())
    if not match:
        raise ValueError(f"expected <repo>#<number>, got {ref!r}")
    return match.group(1), match.group(2)


def _index(doc: Dict[str, Any], ref: str) -> int:
    repo, local = split_ref(ref)
    for i, row in enumerate(doc["queue"]):
        if row["repo"] == repo and row["ref"] == local:
            return i
    raise ValueError(f"{ref} is not in the queue")


def set_lane(doc: Dict[str, Any], repo: str, status: str,
             item: Optional[str] = None, session: Optional[str] = None) -> None:
    """Upsert the lane for `repo` (one lane per repo)."""
    lane: Dict[str, Any] = {"repo": repo, "status": status}
    if item:
        lane["item"] = item
    if session:
        lane["session"] = session
    for i, existing in enumerate(doc["lanes"]):
        if existing["repo"] == repo:
            doc["lanes"][i] = lane
            return
    doc["lanes"].append(lane)


def drop_lane(doc: Dict[str, Any], repo: str) -> None:
    kept = [lane for lane in doc["lanes"] if lane["repo"] != repo]
    if len(kept) == len(doc["lanes"]):
        raise ValueError(f"no lane for {repo}")
    doc["lanes"] = kept


def add_item(doc: Dict[str, Any], ref: str, title: str, status: str = "queued",
             note: Optional[str] = None, at: Optional[int] = None) -> None:
    """Queue `ref`; `at` is a 1-based position (default: the end)."""
    repo, local = split_ref(ref)
    row: Dict[str, Any] = {"repo": repo, "ref": local, "title": title, "status": status}
    if note:
        row["note"] = note
    position = len(doc["queue"]) if at is None else _position(at, len(doc["queue"]) + 1)
    doc["queue"].insert(position, row)


def set_item(doc: Dict[str, Any], ref: str, status: Optional[str] = None,
             note: Optional[str] = None, title: Optional[str] = None) -> None:
    """Change an item in place; an empty `note` clears it."""
    if status is None and note is None and title is None:
        raise ValueError("nothing to set: pass --status, --note or --title")
    row = doc["queue"][_index(doc, ref)]
    if status is not None:
        row["status"] = status
    if title is not None:
        row["title"] = title
    if note is not None:
        if note:
            row["note"] = note
        else:
            row.pop("note", None)


def _position(at: int, slots: int) -> int:
    if not 1 <= at <= slots:
        raise ValueError(f"position must be 1..{slots}, got {at}")
    return at - 1


def move_item(doc: Dict[str, Any], ref: str, to: int) -> None:
    """Move an item to 1-based position `to`."""
    row = doc["queue"].pop(_index(doc, ref))
    doc["queue"].insert(_position(to, len(doc["queue"]) + 1), row)


def remove_item(doc: Dict[str, Any], ref: str) -> None:
    doc["queue"].pop(_index(doc, ref))


def add_waiting(doc: Dict[str, Any], text: str, ref: Optional[str] = None) -> None:
    row: Dict[str, Any] = {"text": text}
    if ref:
        split_ref(ref)
        row["ref"] = ref.strip()
    doc["waiting_on_roberto"].append(row)


def clear_waiting(doc: Dict[str, Any], key: str) -> None:
    """Drop a waiting item by 1-based index, by its `repo#N` ref, or by exact text."""
    rows = doc["waiting_on_roberto"]
    if key.isdigit():
        rows.pop(_position(int(key), len(rows)))
        return
    kept = [row for row in rows if row.get("ref") != key and row["text"] != key]
    if len(kept) == len(rows):
        raise ValueError(f"nothing waiting matches {key!r}")
    doc["waiting_on_roberto"] = kept


# ---- CLI (mounted under `chief_ops.py plan`) ---------------------------------

def _summary(doc: Dict[str, Any]) -> str:
    return (f"PLAN=written lanes={len(doc['lanes'])} queue={len(doc['queue'])} "
            f"waiting={len(doc['waiting_on_roberto'])} updated_at={doc['updated_at']}")


def _run(args: argparse.Namespace) -> int:
    path = Path(args.plan_file) if args.plan_file else plan_file()
    action = args.plan_action
    if action == "show":
        print(json.dumps(load(path), indent=2))
        return 0
    mutations: Dict[str, Callable[[Dict[str, Any]], None]] = {
        "lane": lambda d: set_lane(d, args.repo, args.status, args.item, args.session),
        "drop-lane": lambda d: drop_lane(d, args.repo),
        "add": lambda d: add_item(d, args.ref, args.title, args.status, args.note, args.at),
        "set": lambda d: set_item(d, args.ref, args.status, args.note, args.title),
        "move": lambda d: move_item(d, args.ref, args.to),
        "remove": lambda d: remove_item(d, args.ref),
        "wait": lambda d: add_waiting(d, args.text, args.ref),
        "unwait": lambda d: clear_waiting(d, args.key),
        "clear": lambda d: None,
    }
    doc = update(path, mutations[action], reset=action == "clear")
    print(_summary(doc))
    return 0


def add_cli(parser: argparse.ArgumentParser) -> None:
    """Mount the plan actions on `chief_ops.py plan`."""
    parser.add_argument("--plan-file", default=None,
                        help="override the plan path (tests); default hooks/state/chief-plan.json")
    acts = parser.add_subparsers(dest="plan_action", required=True)

    acts.add_parser("show", help="print the current plan")

    lane = acts.add_parser("lane", help="set the lane for a repo (upsert)")
    lane.add_argument("repo")
    lane.add_argument("status", choices=LANE_STATUSES)
    lane.add_argument("--item", default=None, help="#N the lane is on")
    lane.add_argument("--session", default=None)

    drop = acts.add_parser("drop-lane", help="remove a repo's lane")
    drop.add_argument("repo")

    add = acts.add_parser("add", help="queue <repo>#<N>")
    add.add_argument("ref")
    add.add_argument("title")
    add.add_argument("--status", choices=QUEUE_STATUSES, default="queued")
    add.add_argument("--note", default=None)
    add.add_argument("--at", type=int, default=None, help="1-based position (default: end)")

    set_p = acts.add_parser("set", help="change a queued item's status, note or title")
    set_p.add_argument("ref")
    set_p.add_argument("--status", choices=QUEUE_STATUSES, default=None)
    set_p.add_argument("--note", default=None, help='"" clears it')
    set_p.add_argument("--title", default=None)

    move = acts.add_parser("move", help="move a queued item to a 1-based position")
    move.add_argument("ref")
    move.add_argument("--to", type=int, required=True)

    rm = acts.add_parser("remove", help="drop an item from the queue")
    rm.add_argument("ref")

    wait = acts.add_parser("wait", help="add a waiting-on-Roberto item")
    wait.add_argument("text")
    wait.add_argument("--ref", default=None, help="<repo>#<N>")

    unwait = acts.add_parser("unwait", help="clear a waiting item (index, repo#N or text)")
    unwait.add_argument("key")

    acts.add_parser("clear", help="reset to an empty plan")
    parser.set_defaults(func=_run)
