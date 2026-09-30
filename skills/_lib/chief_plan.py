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
# The model a lane or build item runs on, picked per task by docs/model-tiers.md (fleet-config#1101).
MODELS = ("opus", "sonnet")

# "One short line" -- long enough for a real title, short enough for a phone chip row.
MAX_TEXT = 200
# A question's prose (question, detail, recommendation) is still one line, but
# it is read on the Board's answer sheet, not in a chip row (fleet-config#1049).
MAX_PROSE = 1000
MAX_LABEL = 80
MAX_OPTIONS = 4

TOP_FIELDS = {"version", "updated_at", "lanes", "queue", "waiting_on_roberto"}
LANE_FIELDS = {"repo", "session", "item", "status", "model"}
QUEUE_FIELDS = {"repo", "ref", "title", "status", "note", "model"}
WAITING_FIELDS = {"text", "ref", "id", "repo", "question", "detail", "recommendation",
                  "options", "multi"}
OPTION_FIELDS = {"label", "description", "recommended"}

_LOCAL_REF = re.compile(r"#[1-9][0-9]*")
_REPO = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_QUALIFIED_REF = re.compile(r"([A-Za-z0-9][A-Za-z0-9._-]*)(#[1-9][0-9]*)")
_ISO_Z = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
# Letter-first, so `unwait <key>` can still tell an id from a 1-based index.
_ID = re.compile(r"[a-z][a-z0-9-]{0,31}")
_AUTO_ID = re.compile(r"q([1-9][0-9]*)")


def plan_file() -> Path:
    """`~/.claude/hooks/state/chief-plan.json`, honouring `CLAUDE_HOOKS_STATE_DIR`."""
    return state_dir() / STATE_FILENAME


def empty_plan() -> Dict[str, Any]:
    return {"version": VERSION, "updated_at": "", "lanes": [], "queue": [],
            "waiting_on_roberto": []}


# ---- validation --------------------------------------------------------------

def _check_text(errors: List[str], where: str, value: Any, *, required: bool,
                limit: int = MAX_TEXT) -> None:
    if not isinstance(value, str):
        errors.append(f"{where}: must be a string")
        return
    if required and not value.strip():
        errors.append(f"{where}: must not be empty")
    if _CONTROL.search(value):
        errors.append(f"{where}: must be one line with no control characters")
    if len(value) > limit:
        errors.append(f"{where}: longer than {limit} characters")


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


def _check_model(errors: List[str], where: str, row: Dict[str, Any]) -> None:
    if "model" in row and row["model"] not in MODELS:
        errors.append(f"{where}.model: must be one of {', '.join(MODELS)}")


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
        _check_model(errors, where, lane)

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
        _check_model(errors, where, row)

    ids: set = set()
    for i, row in enumerate(doc["waiting_on_roberto"]):
        where = f"waiting_on_roberto[{i}]"
        if not _check_fields(errors, where, row, WAITING_FIELDS, {"text"}):
            continue
        _check_waiting(errors, where, row, ids)
    return errors


def _check_waiting(errors: List[str], where: str, row: Dict[str, Any], ids: set) -> None:
    """One waiting item: v1's `text`/`ref` plus the structured question (fleet-config#1049)."""
    _check_text(errors, f"{where}.text", row.get("text"), required=True)
    ref = row.get("ref", "")
    ref_match = _QUALIFIED_REF.fullmatch(ref) if isinstance(ref, str) else None
    if ref and not ref_match:
        errors.append(f"{where}.ref: must be repo#N or empty")
    if "id" in row:
        if not isinstance(row["id"], str) or not _ID.fullmatch(row["id"]):
            errors.append(f"{where}.id: must be a lowercase letter then up to 31 "
                          "letters, digits or hyphens")
        elif row["id"] in ids:
            errors.append(f"{where}.id: {row['id']} is already used by another waiting item")
        else:
            ids.add(row["id"])
    if "repo" in row:
        _check_repo(errors, f"{where}.repo", row["repo"])
        if ref_match and row["repo"] != ref_match.group(1):
            errors.append(f"{where}.repo: {row['repo']} does not match ref {ref}")
    for key in ("question", "detail", "recommendation"):
        if key in row:
            _check_text(errors, f"{where}.{key}", row[key], required=True, limit=MAX_PROSE)
    multi = row.get("multi", False)
    if not isinstance(multi, bool):
        errors.append(f"{where}.multi: must be true or false")
    if "options" not in row:
        if multi is True:
            errors.append(f"{where}.multi: needs options to choose from")
        return
    options = row["options"]
    if not isinstance(options, list):
        errors.append(f"{where}.options: must be a list")
        return
    if len(options) > MAX_OPTIONS:
        errors.append(f"{where}.options: at most {MAX_OPTIONS} options, got {len(options)}")
    labels: set = set()
    recommended = 0
    for j, option in enumerate(options):
        at = f"{where}.options[{j}]"
        if not _check_fields(errors, at, option, OPTION_FIELDS, {"label"}):
            continue
        label = option.get("label")
        _check_text(errors, f"{at}.label", label, required=True, limit=MAX_LABEL)
        if isinstance(label, str) and label.strip():
            if label in labels:
                errors.append(f"{at}.label: {label!r} is already an option")
            labels.add(label)
        if "description" in option:
            _check_text(errors, f"{at}.description", option["description"], required=False)
        if "recommended" in option:
            if not isinstance(option["recommended"], bool):
                errors.append(f"{at}.recommended: must be true or false")
            elif option["recommended"]:
                recommended += 1
    if recommended > 1 and multi is not True:
        errors.append(f"{where}.options: {recommended} options are recommended; "
                      "at most one unless multi is true")


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


def _set_model(row: Dict[str, Any], model: Optional[str]) -> None:
    """`None` leaves the row's model alone, `""` clears it, anything else must be in MODELS."""
    if model is None:
        return
    if not model:
        row.pop("model", None)
    elif model in MODELS:
        row["model"] = model
    else:
        raise ValueError(f"--model must be one of {', '.join(MODELS)} (\"\" clears it), got {model!r}")


def set_lane(doc: Dict[str, Any], repo: str, status: str,
             item: Optional[str] = None, session: Optional[str] = None,
             model: Optional[str] = None) -> None:
    """Upsert the lane for `repo` (one lane per repo).

    `item` and `session` are replaced with the row; the lane's model outlives a
    status change, so an omitted `model` keeps it and `""` clears it (#1101).
    """
    existing = next((i for i, lane in enumerate(doc["lanes"]) if lane["repo"] == repo), None)
    lane: Dict[str, Any] = {"repo": repo, "status": status}
    if item:
        lane["item"] = item
    if session:
        lane["session"] = session
    if existing is not None and "model" in doc["lanes"][existing]:
        lane["model"] = doc["lanes"][existing]["model"]
    _set_model(lane, model)
    if existing is None:
        doc["lanes"].append(lane)
    else:
        doc["lanes"][existing] = lane


def drop_lane(doc: Dict[str, Any], repo: str) -> None:
    kept = [lane for lane in doc["lanes"] if lane["repo"] != repo]
    if len(kept) == len(doc["lanes"]):
        raise ValueError(f"no lane for {repo}")
    doc["lanes"] = kept


def add_item(doc: Dict[str, Any], ref: str, title: str, status: str = "queued",
             note: Optional[str] = None, at: Optional[int] = None,
             model: Optional[str] = None) -> None:
    """Queue `ref`; `at` is a 1-based position (default: the end)."""
    repo, local = split_ref(ref)
    row: Dict[str, Any] = {"repo": repo, "ref": local, "title": title, "status": status}
    if note:
        row["note"] = note
    _set_model(row, model)
    position = len(doc["queue"]) if at is None else _position(at, len(doc["queue"]) + 1)
    doc["queue"].insert(position, row)


def set_item(doc: Dict[str, Any], ref: str, status: Optional[str] = None,
             note: Optional[str] = None, title: Optional[str] = None,
             model: Optional[str] = None) -> None:
    """Change an item in place; an empty `note` or `model` clears it."""
    if status is None and note is None and title is None and model is None:
        raise ValueError("nothing to set: pass --status, --note, --title or --model")
    row = doc["queue"][_index(doc, ref)]
    _set_model(row, model)
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


def _next_id(doc: Dict[str, Any]) -> str:
    """`q<N>`, one past the highest auto id in use, so a cleared id is not reissued
    while a later one is still waiting (an answer for it can't land on a new item)."""
    taken = [int(m.group(1)) for row in doc["waiting_on_roberto"]
             if (m := _AUTO_ID.fullmatch(str(row.get("id", ""))))]
    return f"q{max(taken, default=0) + 1}"


def add_waiting(doc: Dict[str, Any], text: str, ref: Optional[str] = None) -> str:
    """Append a plain waiting item; returns its id."""
    row: Dict[str, Any] = {"id": _next_id(doc), "text": text}
    if ref:
        split_ref(ref)
        row["ref"] = ref.strip()
    doc["waiting_on_roberto"].append(row)
    return row["id"]


def parse_option(spec: str) -> Dict[str, Any]:
    """`"Label::description"` -> an option; no `::` means a bare label."""
    label, _, description = spec.partition("::")
    option: Dict[str, Any] = {"label": label.strip()}
    if not option["label"]:
        raise ValueError(f"--option needs a label before '::', got {spec!r}")
    if description.strip():
        option["description"] = description.strip()
    return option


def short_text(question: str) -> str:
    """The card line for a question: whitespace collapsed, cut to MAX_TEXT."""
    text = " ".join(question.split())
    return text if len(text) <= MAX_TEXT else text[:MAX_TEXT - 1].rstrip() + "\u2026"


def add_question(
    doc: Dict[str, Any],
    question: str,
    *,
    repo: Optional[str] = None,
    ref: Optional[str] = None,
    detail: Optional[str] = None,
    recommendation: Optional[str] = None,
    options: Optional[List[str]] = None,
    recommended: Optional[List[str]] = None,
    multi: bool = False,
    item_id: Optional[str] = None,
    text: Optional[str] = None,
) -> str:
    """Append a structured question for the Board's answer sheet; returns its id.

    `options` are `"Label::description"` specs; `recommended` names option
    labels to mark. `repo` is derived from `ref` when omitted. The shape rules
    (option count, one recommendation unless `multi`, repo/ref agreement,
    unique ids) are `validate()`'s, so they hold for every writer.
    """
    row: Dict[str, Any] = {"id": item_id or _next_id(doc),
                           "text": short_text(question) if text is None else text}
    if ref:
        row["repo"] = repo or split_ref(ref)[0]
        row["ref"] = ref.strip()
    elif repo:
        row["repo"] = repo
    row["question"] = question.strip()
    if detail:
        row["detail"] = detail.strip()
    if recommendation:
        row["recommendation"] = recommendation.strip()
    parsed = [parse_option(spec) for spec in options or []]
    for label in recommended or []:
        match = [opt for opt in parsed if opt["label"] == label.strip()]
        if not match:
            raise ValueError(f"--recommended {label!r} is not one of the --option labels")
        match[0]["recommended"] = True
    if parsed:
        row["options"] = parsed
    if multi:
        row["multi"] = True
    doc["waiting_on_roberto"].append(row)
    return row["id"]


def clear_waiting(doc: Dict[str, Any], key: str) -> None:
    """Drop a waiting item by 1-based index, by its id, by its `repo#N` ref, or by exact text."""
    rows = doc["waiting_on_roberto"]
    if key.isdigit():
        rows.pop(_position(int(key), len(rows)))
        return
    kept = [row for row in rows if key not in (row.get("id"), row.get("ref"), row["text"])]
    if len(kept) == len(rows):
        raise ValueError(f"nothing waiting matches {key!r}")
    doc["waiting_on_roberto"] = kept


# ---- CLI (mounted under `chief_ops.py plan`) ---------------------------------

def _summary(doc: Dict[str, Any]) -> str:
    return (f"PLAN=written lanes={len(doc['lanes'])} queue={len(doc['queue'])} "
            f"waiting={len(doc['waiting_on_roberto'])} updated_at={doc['updated_at']}")


def render(doc: Dict[str, Any]) -> str:
    """The plan as JSON with one row per line -- still parseable, but a question's
    options stay on its own line instead of spreading over a screen."""
    def dump(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False)

    def tail(i: int, n: int) -> str:
        return "," if i < n - 1 else ""

    lines = ["{"]
    for i, (key, value) in enumerate(doc.items()):
        if isinstance(value, list) and value:
            lines.append(f"  {dump(key)}: [")
            lines += [f"    {dump(row)}{tail(j, len(value))}" for j, row in enumerate(value)]
            lines.append(f"  ]{tail(i, len(doc))}")
        else:
            lines.append(f"  {dump(key)}: {dump(value)}{tail(i, len(doc))}")
    lines.append("}")
    return "\n".join(lines)


def _ask(doc: Dict[str, Any], args: argparse.Namespace) -> str:
    return add_question(doc, args.question, repo=args.repo, ref=args.ref,
                        detail=args.detail, recommendation=args.recommend,
                        options=args.option, recommended=args.recommended,
                        multi=args.multi, item_id=args.id, text=args.text)


def _run(args: argparse.Namespace) -> int:
    path = Path(args.plan_file) if args.plan_file else plan_file()
    action = args.plan_action
    if action == "show":
        print(render(load(path)))
        return 0
    added: List[str] = []
    mutations: Dict[str, Callable[[Dict[str, Any]], None]] = {
        "lane": lambda d: set_lane(d, args.repo, args.status, args.item, args.session, args.model),
        "drop-lane": lambda d: drop_lane(d, args.repo),
        "add": lambda d: add_item(d, args.ref, args.title, args.status, args.note, args.at, args.model),
        "set": lambda d: set_item(d, args.ref, args.status, args.note, args.title, args.model),
        "move": lambda d: move_item(d, args.ref, args.to),
        "remove": lambda d: remove_item(d, args.ref),
        "wait": lambda d: added.append(add_waiting(d, args.text, args.ref)),
        "ask": lambda d: added.append(_ask(d, args)),
        "unwait": lambda d: clear_waiting(d, args.key),
        "clear": lambda d: None,
    }
    doc = update(path, mutations[action], reset=action == "clear")
    print(_summary(doc) + "".join(f" id={item}" for item in added))
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
    lane.add_argument("--model", default=None, help=f"{'|'.join(MODELS)}; omitted keeps it, \"\" clears it")

    drop = acts.add_parser("drop-lane", help="remove a repo's lane")
    drop.add_argument("repo")

    add = acts.add_parser("add", help="queue <repo>#<N>")
    add.add_argument("ref")
    add.add_argument("title")
    add.add_argument("--status", choices=QUEUE_STATUSES, default="queued")
    add.add_argument("--note", default=None)
    add.add_argument("--at", type=int, default=None, help="1-based position (default: end)")
    add.add_argument("--model", default=None, help="|".join(MODELS))

    set_p = acts.add_parser("set", help="change a queued item's status, note, title or model")
    set_p.add_argument("ref")
    set_p.add_argument("--status", choices=QUEUE_STATUSES, default=None)
    set_p.add_argument("--note", default=None, help='"" clears it')
    set_p.add_argument("--title", default=None)
    set_p.add_argument("--model", default=None, help=f"{'|'.join(MODELS)}; \"\" clears it")

    move = acts.add_parser("move", help="move a queued item to a 1-based position")
    move.add_argument("ref")
    move.add_argument("--to", type=int, required=True)

    rm = acts.add_parser("remove", help="drop an item from the queue")
    rm.add_argument("ref")

    wait = acts.add_parser("wait", help="add a waiting-on-Roberto item")
    wait.add_argument("text")
    wait.add_argument("--ref", default=None, help="<repo>#<N>")

    ask = acts.add_parser("ask", help="add a structured question for the Board's answer sheet")
    ask.add_argument("question")
    ask.add_argument("--repo", default=None, help="fleet repo (default: from --ref)")
    ask.add_argument("--ref", default=None, help="<repo>#<N>")
    ask.add_argument("--detail", default=None, help="descriptive context")
    ask.add_argument("--recommend", default=None, help="your recommendation, free text")
    ask.add_argument("--option", action="append", default=None,
                     help='"Label::description"; repeat, up to 4')
    ask.add_argument("--recommended", action="append", default=None,
                     help="an --option label to mark recommended (more than one needs --multi)")
    ask.add_argument("--multi", action="store_true", help="more than one option may be chosen")
    ask.add_argument("--id", default=None, help="stable id (default: the next q<N>)")
    ask.add_argument("--text", default=None,
                     help="the short card line (default: the question, trimmed)")

    unwait = acts.add_parser("unwait", help="clear a waiting item (index, id, repo#N or text)")
    unwait.add_argument("key")

    acts.add_parser("clear", help="reset to an empty plan")
    parser.set_defaults(func=_run)
