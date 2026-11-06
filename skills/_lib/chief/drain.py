"""`chief_ops.py drain`: queue unattended audit issues, then fill free worker slots.

The chief runs this at the end of every turn, beside `wake`. It does the
mechanical half of "drain the backlog that can run unattended" and leaves the
judgment (verifying each merge, closing each lane) with the chief:

1. **Switch.** `auto_drain` from `/api/board/chief/settings`. Only a literal
   `true` is on. Off, missing or unreadable prints `DRAIN=off reason=...`
   and nothing else happens.
2. **Intake.** An open issue joins the plan queue only when its body starts
   with an `<!-- audit-managed: kind=<kind> -->` marker (not
   `cleanup-deferred`), it has no owner or ledger label, it still lists an
   unchecked `- [ ]` finding, it is not already in
   the plan, drain never added it before, and its repo is not
   `auto_ship = false` in `hooks/projects.toml`.
3. **Dispatch.** Queued rows that drain added are dispatched top-down through
   `cmd_dispatch`, with every one of its refusals, skipping a busy repo. The
   first refusal or unconfirmed dispatch ends the drain for this turn.

Drain's ledger (`chief-drain.json` in the hooks state dir) lists every ref it
ever queued. That is how it tells its own rows from hand-queued ones, and how
a row the chief removed stays removed: the ref is in the ledger but no longer
in the plan.

Every action prints one line (`DRAIN_ADDED=`, `DRAIN_DISPATCHED=`,
`DRAIN_SKIPPED=<ref> reason=`, `DRAIN_STOPPED=<ref> reason=`), then the
summary `DRAIN=done added=N dispatched=M`. `--dry-run` prints `DRAIN_WOULD_*`
lines and writes nothing: no plan, no ledger, no brief, no dispatch.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import re
import urllib.error
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import chief_plan
import gh_issue_fetch
from active_issue import _iso_z, _now, state_lock, write_rows
from audit_ledger.records import marker_kind
from fleet_core import fleet_repo_scan
from fleet_core.hooks_state import state_dir

from . import commands, launcher, rails

LEDGER_FILENAME = "chief-drain.json"
BRIEF_DIRNAME = "chief-drain-briefs"
BRIEF_TEMPLATE = Path(__file__).with_name("drain_brief.md")

# Lane model per audit kind, by docs/model-tiers.md difficulty; unlisted kinds are easy tier.
KIND_MODELS = {
    "design-drift": "sonnet",
    "design-review": "sonnet",
    "stale": "sonnet",
    "documentation": "sonnet",
    "perf-review": "opus",
    "prompt-drift": "opus",
}
DEFAULT_MODEL = "sonnet"

# A deferral record, not a findings list: nothing in it is for a lane to fix.
EXCLUDED_KINDS = frozenset({"cleanup-deferred"})
# Owner-gated work, and the /codebase-audit ledger, which is not actionable.
EXCLUDED_LABELS = frozenset({"needs-owner", "needs-validation", "audit-meta"})

_DISPATCHED = re.compile(r"^DISPATCHED session=(\S+)", re.MULTILINE)
# An open finding is an unchecked task-list item. A standing ledger with none (a perf-review
# issue whose Fixes section reads "_Nothing over budget._") has no work for a lane.
_OPEN_FINDING = re.compile(r"^[ \t]*[-*][ \t]+\[ \][ \t]", re.MULTILINE)


# ---- pure decisions -----------------------------------------------------------

def drain_switch(reply: Any) -> Optional[str]:
    """None when `auto_drain` is on, else the `DRAIN=off` line. Unknown is never on."""
    settings = reply.get("settings") if isinstance(reply, dict) else None
    if not isinstance(settings, dict):
        return "DRAIN=off reason=settings-unreadable"
    if "auto_drain" not in settings:
        return "DRAIN=off reason=setting-missing"
    value = settings["auto_drain"]
    if value is True:
        return None
    if value is False:
        return "DRAIN=off reason=switch-off"
    return f"DRAIN=off reason=setting-not-boolean value={json.dumps(value)}"


def no_ship_repos(tables: Dict[str, dict]) -> Set[str]:
    """Repos whose merge publishes (`auto_ship = false`), lowercased."""
    return {name.lower() for name, table in tables.items() if table.get("auto_ship") is False}


def plan_refs(doc: Dict[str, Any]) -> Set[str]:
    """Every `<repo>#<N>` the plan names, in its queue or as a lane's item."""
    refs = {f"{row['repo']}{row['ref']}" for row in doc["queue"]}
    refs.update(f"{lane['repo']}{lane['item']}" for lane in doc["lanes"] if lane.get("item"))
    return {ref.lower() for ref in refs}


def _clean_title(title: str, fallback: str) -> str:
    text = re.sub(r"\s+", " ", title or "").strip()
    return (text or fallback)[: chief_plan.MAX_TEXT]


def select_intake(
    issues: List[Dict[str, Any]],
    doc: Dict[str, Any],
    ledger_refs: Set[str],
    no_ship: Set[str],
) -> Tuple[List[Dict[str, str]], List[Tuple[str, str]]]:
    """`(adds, skips)`: the audit issues to queue, and each audit issue left out with why.

    An issue with no audit marker is not drain's business and is never reported.
    """
    in_plan = plan_refs(doc)
    known = {ref.lower() for ref in ledger_refs}
    adds: List[Dict[str, str]] = []
    skips: List[Tuple[str, str]] = []
    for issue in issues:
        kind = marker_kind(issue.get("body") or "")
        if kind is None:
            continue
        repo = (issue.get("repository") or {}).get("name") or ""
        ref = f"{repo}#{issue.get('number')}"
        labels = {(label.get("name") or "").lower() for label in issue.get("labels") or []}
        if kind in EXCLUDED_KINDS:
            reason = f"kind-{kind}"
        elif labels & EXCLUDED_LABELS:
            reason = "label-" + sorted(labels & EXCLUDED_LABELS)[0]
        elif not _OPEN_FINDING.search(issue.get("body") or ""):
            reason = "no-open-findings"
        elif ref.lower() in in_plan:
            reason = "in-plan"
        elif ref.lower() in known:
            reason = "removed-from-plan"
        elif repo.lower() in no_ship:
            reason = "auto-ship-off"
        else:
            adds.append({"ref": ref, "kind": kind, "model": KIND_MODELS.get(kind, DEFAULT_MODEL),
                         "title": _clean_title(issue.get("title") or "", kind)})
            continue
        skips.append((ref, reason))
    return adds, skips


def apply_adds(doc: Dict[str, Any], adds: List[Dict[str, str]]) -> None:
    for add in adds:
        chief_plan.add_item(doc, add["ref"], add["title"], model=add["model"], note=f"auto-drain {add['kind']}")


def auto_rows(doc: Dict[str, Any], ledger_refs: Set[str]) -> List[Dict[str, Any]]:
    """Queued rows drain added, in plan order; hand-queued rows stay the chief's."""
    known = {ref.lower() for ref in ledger_refs}
    return [row for row in doc["queue"]
            if row["status"] == "queued" and f"{row['repo']}{row['ref']}".lower() in known]


def record_lane(doc: Dict[str, Any], repo: str, local_ref: str, sid: str, model: Optional[str]) -> None:
    """The lane exactly as `plan lane <repo> building --item #N --session <sid>` writes it."""
    chief_plan.set_lane(doc, repo, "building", item=local_ref, session=sid, model=model)
    chief_plan.set_item(doc, f"{repo}{local_ref}", status="building")


# ---- state ----------------------------------------------------------------------

def ledger_file() -> Path:
    return state_dir() / LEDGER_FILENAME


def load_ledger(path: Path) -> Dict[str, Any]:
    """`{ref: {kind, added_at}}`; a missing file is empty, an unparseable one raises ValueError."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise ValueError(f"{path.name} unreadable: {exc}") from exc
    refs = doc.get("refs") if isinstance(doc, dict) else None
    if not isinstance(refs, dict):
        raise ValueError(f"{path.name} has no refs mapping")
    return refs


def remember(path: Path, adds: List[Dict[str, str]]) -> None:
    with state_lock(path):
        refs = load_ledger(path)
        stamp = _iso_z(_now())
        for add in adds:
            refs[add["ref"]] = {"kind": add["kind"], "added_at": stamp}
        write_rows(path, {"version": 1, "refs": refs})


def write_brief(ref: str, kind: str) -> Path:
    text = BRIEF_TEMPLATE.read_text(encoding="utf-8").replace("__REF__", ref).replace("__KIND__", kind)
    path = state_dir() / BRIEF_DIRNAME / (ref.replace("#", "-") + ".md")
    # runtime-write-exempt: the lane brief lives in the hooks state directory, never a code folder
    path.parent.mkdir(parents=True, exist_ok=True)
    # runtime-write-exempt: same hooks-state brief file, read back by the launcher at dispatch
    path.write_text(text, encoding="utf-8")
    return path


def fetch_open_issues() -> Tuple[List[Dict[str, Any]], List[Tuple[str, str]]]:
    """Open issues fleet-wide minus dormant repos, as `gh_issue_fetch fetch` reads them. Tests replace it."""
    repos, _ = gh_issue_fetch.split_dormant(gh_issue_fetch.list_owner_repos(), fleet_repo_scan.ignored_repos())
    return gh_issue_fetch.aggregate(repos, lambda repo: gh_issue_fetch.fetch_repo_issues(repo, None))


# ---- the command -------------------------------------------------------------

def _dispatch_one(args: argparse.Namespace, row: Dict[str, Any], kind: str) -> Tuple[Optional[str], str]:
    """`(sid, output)` from one guard-railed `cmd_dispatch`; `sid` None means it did not dispatch."""
    ref = f"{row['repo']}{row['ref']}"
    ns = argparse.Namespace(
        repo=row["repo"], number=int(row["ref"][1:]), mode="start", model=row.get("model"),
        effort=None, brief_file=str(write_brief(ref, kind)), yolo_confirmed=False,
        allow_during_fleet_job=False, base_url=args.base_url,
    )
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            rc = commands.cmd_dispatch(ns)
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        return None, buf.getvalue() + f"DISPATCH=failed reason={exc}\n"
    match = _DISPATCHED.search(buf.getvalue())
    # rc 0 with no session id means the launcher spawned something this call cannot name.
    sid = match.group(1) if rc == 0 and match and match.group(1) != "None" else None
    if rc == 0 and sid is None:
        return None, buf.getvalue() + "DISPATCH=unconfirmed reason=no-session-id\n"
    return sid, buf.getvalue()


def _free_slots(args: argparse.Namespace, columns: Dict[str, Any], reply: Dict[str, Any]) -> Optional[int]:
    cap, managed, scope_error = commands._worker_cap_inputs(reply.get("settings") or {})
    if scope_error:
        return None
    alive = rails.alive_worker_count(columns, exclude_sid=rails.caller_session_id(), managed_sids=managed)
    return max(cap - alive, 0)


def _intake(args: argparse.Namespace, plan_path: Path, ledger_path: Path,
            fetch: Callable[[], Tuple[List[Dict[str, Any]], List[Tuple[str, str]]]],
            ) -> Tuple[Dict[str, Any], Dict[str, Any], List[Dict[str, str]]]:
    """Queue the eligible audit issues; returns `(plan, ledger, adds)` as they now stand."""
    no_ship = no_ship_repos(fleet_repo_scan.fleet_repo_tables())
    issues, errors = fetch()
    for repo, reason in errors:
        print(f"DRAIN_SKIPPED={repo} reason=fetch-error ({reason.splitlines()[0] if reason else '?'})")
    doc = chief_plan.load(plan_path)
    ledger = load_ledger(ledger_path)
    adds, skips = select_intake(issues, doc, set(ledger), no_ship)
    for ref, reason in skips:
        print(f"DRAIN_SKIPPED={ref} reason={reason}")
    verb = "DRAIN_WOULD_ADD" if args.dry_run else "DRAIN_ADDED"
    for add in adds:
        print(f"{verb}={add['ref']} kind={add['kind']} model={add['model']}")
    if args.dry_run:
        apply_adds(doc, adds)  # in memory only, so the dispatch preview sees the new rows
    elif adds:
        # Plan first: a ledger entry without its plan row would read as "removed" forever.
        doc = chief_plan.update(plan_path, lambda d: apply_adds(d, adds))
        remember(ledger_path, adds)
    ledger = {**ledger, **{add["ref"]: {"kind": add["kind"]} for add in adds}}
    return doc, ledger, adds


def cmd_drain(args: argparse.Namespace, fetch: Optional[Callable] = None) -> int:
    try:
        reply = launcher._request(args.base_url, "/api/board/chief/settings")
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        print(f"DRAIN=off reason=settings-unreadable ({exc})")
        return 0
    off = drain_switch(reply)
    if off is not None:
        print(off)
        return 0

    plan_path = Path(args.plan_file) if args.plan_file else chief_plan.plan_file()
    ledger_path = ledger_file()
    try:
        doc, ledger, adds = _intake(args, plan_path, ledger_path, fetch or fetch_open_issues)
    except (ValueError, OSError, fleet_repo_scan.ProjectsTomlMissing, RuntimeError) as exc:
        print(f"DRAIN=unknown reason=intake-failed ({exc})")
        return 1

    columns = (launcher._request(args.base_url, "/api/board").get("columns") or {})
    busy = set(rails.repo_occupancy(columns, exclude_sid=rails.caller_session_id()))
    rows = auto_rows(doc, set(ledger))
    if args.dry_run:
        free = _free_slots(args, columns, reply)
        shown = 0
        for row in rows:
            ref = f"{row['repo']}{row['ref']}"
            if row["repo"].lower() in busy:
                print(f"DRAIN_SKIPPED={ref} reason=repo-busy")
                continue
            if free is not None and shown >= free:
                break
            busy.add(row["repo"].lower())
            shown += 1
            print(f"DRAIN_WOULD_DISPATCH={ref} model={row.get('model', DEFAULT_MODEL)}")
        print(f"DRAIN=dry-run added={len(adds)} dispatched={shown} "
              f"free_slots={'unknown' if free is None else free}")
        return 0

    dispatched = 0
    for row in rows:
        ref = f"{row['repo']}{row['ref']}"
        if row["repo"].lower() in busy:
            print(f"DRAIN_SKIPPED={ref} reason=repo-busy")
            continue
        kind = (ledger.get(ref) or {}).get("kind") or "audit"
        sid, output = _dispatch_one(args, row, kind)
        print(output, end="" if output.endswith("\n") or not output else "\n")
        if sid is None:
            reason = "unconfirmed" if "DISPATCH=unconfirmed" in output else "refused"
            print(f"DRAIN_STOPPED={ref} reason={reason}")
            break
        chief_plan.update(plan_path, lambda d: record_lane(d, row["repo"], row["ref"], sid, row.get("model")))
        busy.add(row["repo"].lower())
        dispatched += 1
        print(f"DRAIN_DISPATCHED={ref} session={sid} model={row.get('model', DEFAULT_MODEL)}")
    print(f"DRAIN=done added={len(adds)} dispatched={dispatched}")
    return 0
