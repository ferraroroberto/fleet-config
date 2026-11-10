"""One-time migration of the live chief plan: give every `parked` row a kind.

Reads the plan, guesses each parked row's kind from its title and note, and
prints one line per row: the guess, why, and whether an `owner` row already
carries `needs-owner` (the label that puts it in the Board's "Waiting for
start"). A dry run (the default) writes nothing.

`--apply` writes the kind of every row it can place and leaves the rest alone:
  * umbrella / blocked-by rows are written directly;
  * an owner row is written only when the issue already carries `needs-owner`;
    one without it prints the `owner_check mark` command to run, because the
    block's reason and "try" text are a person's words and the repos are public;
  * a row with no confident guess stays kind-less. Fix a wrong or missing guess
    with `--set <repo>#<N>=<kind>` (repeatable), in the dry run and the apply.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe skills/_lib/chief/migrate_parked.py [--apply] [--set repo#N=kind ...]`
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_LIB = Path(__file__).resolve().parent.parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
import chief_plan as cp  # noqa: E402
from fleet_core.utf8_stdio import ensure_utf8_stdio  # noqa: E402
from owner_check import block, github  # noqa: E402

# The short repo names the chief's notes use.
SHORT = {"al": "app-launcher", "ps": "project-scaffolding", "fc": "fleet-config",
         "cm": "content-management", "llh": "local-llm-hub"}

_UMBRELLA = re.compile(r"umbrella|decision log|kept open on purpose", re.I)
# A phrase like "waits on <repo>#<N>", "blocked by #<N>" or "after <short>#<N>"; the repo is optional.
_BLOCKER = re.compile(r"(?:waits?(?: on)?|blocked by|after)\s+(?:([A-Za-z][A-Za-z0-9._-]*))?#(\d+)", re.I)
# A named step of Roberto's. A bare "owner" is not one: "owner revised" and "owner-held"
# describe a decision, so they stay unclassified for the chief to place.
_OWNER = re.compile(r"roberto|owner (?:step|runs|creates|reviews|iphone|check|call)|owner:|phone|iphone|"
                    r"device|probe|live check|sign-?up|install\.ps1|hardware|owes|dry run", re.I)
_CLOSED = re.compile(r"closed not planned", re.I)


def blocker_ref(row: Dict[str, Any], note: str) -> Optional[str]:
    """`repo#N` named by a waits-on / blocked-by / after phrase in the note, else None."""
    match = _BLOCKER.search(note)
    if not match:
        return None
    repo = match.group(1) or ""
    repo = SHORT.get(repo, repo) or row["repo"]
    return f"{repo}#{match.group(2)}"


def guess(row: Dict[str, Any]) -> Tuple[Optional[str], str]:
    """(kind spec or None, the phrase that decided it). Umbrella beats a blocker beats owner."""
    text = f"{row['title']} {row.get('note', '')}"
    note = row.get("note", "")
    if _CLOSED.search(text):
        return None, "closed not planned: likely `plan remove`, not a kind"
    if (m := _UMBRELLA.search(text)):
        return "umbrella", m.group(0)
    if (ref := blocker_ref(row, note)):
        return f"blocked-by {ref}", "waits on " + ref
    if (m := _OWNER.search(text)):
        return "owner", m.group(0)
    return None, "no marker in the title or note"


def owner_labels(repos: List[str]) -> Tuple[set, set]:
    """(refs carrying needs-owner, repos GitHub could not be read for), for the plan's repos only."""
    result = github.list_items(repos)
    have = {it["ref"] for it in result["items"] if it["kind"] == block.BEFORE_START and it["state"] == "OPEN"}
    return have, {miss["repo"] for miss in result["unknown"]}


def classify_rows(rows: List[Dict[str, Any]], overrides: Dict[str, str]) -> List[Dict[str, Any]]:
    """One record per parked row: ref, kind spec (or None), why, and the owner label state."""
    have, unreadable = owner_labels(sorted({row["repo"] for row in rows}))
    plan: List[Dict[str, Any]] = []
    for row in rows:
        ref = f"{row['repo']}{row['ref']}"
        spec, why = (overrides[ref], "--set") if ref in overrides else guess(row)
        label = ""
        if spec == "owner":
            label = ("unknown" if "*" in unreadable or row["repo"] in unreadable
                     else "present" if ref in have else "missing")
        plan.append({"row": row, "ref": ref, "spec": spec, "why": why, "label": label})
    return plan


def describe(item: Dict[str, Any]) -> str:
    spec = item["spec"] or "UNCLASSIFIED"
    action = {"missing": "would need needs-owner (run owner_check mark)",
              "present": "has needs-owner", "unknown": "needs-owner state unknown (gh unreadable)"}.get(item["label"], "")
    note = item["row"].get("note", "")[:70]
    return f"{item['ref']:<34} {spec:<32} [{item['why']}] {action}  | {note}"


def apply_plan(path: Path, plan: List[Dict[str, Any]]) -> Tuple[int, List[str]]:
    """Write the placeable kinds under the plan lock; returns (rows written, left-for-the-chief lines)."""
    written: List[str] = []
    left: List[str] = []
    for item in plan:
        if item["spec"] is None:
            left.append(f"UNCLASSIFIED {item['ref']}: pass --set {item['ref']}=<kind>")
        elif item["label"] == "missing":
            left.append(f"NEEDS_MARK {item['ref']}: owner_check mark {item['ref']} --kind before-start "
                        '--reason "..." --try "..."')
        elif item["label"] == "unknown":
            left.append(f"UNKNOWN {item['ref']}: could not read the needs-owner label; rerun")
        else:
            written.append(item["ref"])

    by_ref = {item["ref"]: item for item in plan}

    def mutate(doc: Dict[str, Any]) -> None:
        for row in doc["queue"]:
            ref = f"{row['repo']}{row['ref']}"
            if ref in written and row["status"] == cp.QueueStatus.PARKED:
                kind, blocker = cp.parse_kind(by_ref[ref]["spec"])
                row["kind"] = kind
                if blocker:
                    row["blocked_by"] = blocker

    if written:
        cp.update(path, mutate)
    return len(written), left


def main(argv: Optional[List[str]] = None) -> int:
    ensure_utf8_stdio()
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--apply", action="store_true", help="write the kinds (default: dry run)")
    parser.add_argument("--set", action="append", default=[], metavar="REPO#N=KIND",
                        help="override a guess; KIND is owner, umbrella or 'blocked-by repo#N'")
    parser.add_argument("--plan-file", default=None)
    args = parser.parse_args(argv)
    path = Path(args.plan_file) if args.plan_file else cp.plan_file()
    overrides: Dict[str, str] = {}
    for spec in args.set:
        ref, sep, kind = spec.partition("=")
        if not sep:
            parser.error(f"--set needs REPO#N=KIND, got {spec!r}")
        cp.parse_kind(kind)
        overrides[ref.strip()] = kind.strip()
    try:
        rows = [r for r in cp.load(path)["queue"] if r["status"] == cp.QueueStatus.PARKED and not r.get("kind")]
        plan = classify_rows(rows, overrides)
    except (ValueError, RuntimeError) as exc:
        print(f"MIGRATE=unknown reason={exc}")
        return 3
    for item in plan:
        print(describe(item))
    tally = {k: sum(1 for i in plan if (i["spec"] or "").split(" ")[0] == k)
             for k in cp.PARKED_KINDS}
    print(f"PARKED={len(plan)} owner={tally['owner']} umbrella={tally['umbrella']} "
          f"blocked-by={tally['blocked-by']} unclassified={sum(1 for i in plan if i['spec'] is None)} "
          f"owner-missing-label={sum(1 for i in plan if i['label'] == 'missing')}")
    if not args.apply:
        print("DRY_RUN=yes (nothing written; add --apply to write)")
        return 0
    count, left = apply_plan(path, plan)
    for line in left:
        print(line)
    print(f"APPLIED={count} LEFT={len(left)}")
    return 0 if not left else 1


if __name__ == "__main__":
    sys.exit(main())
