"""Per-criterion acceptance audit for `/issue-finish` (fleet-config#958).

Why this exists
----------------
Acceptance used to be one yes/no sentence, and fleet criteria very often live
*outside the diff* -- "the tray serves the new SHA", "works on the real
iPhone", "the scheduled job fired". A single boolean lets those ride through
as met when the truthful answer is "could not be established from here". The
global rule already says an unestablished fact is its own state, never folded
into a pass; this applies it to acceptance.

The model still makes every judgment. This helper only keeps the count
honest: every checkbox criterion gets exactly one verdict, a criterion left
out is ``incomplete`` (never read as met), and the unattended rule is one
fixed lookup rather than a per-run interpretation.

Subcommands
-----------
  extract <N> [--repo <owner/name>]
        Prints a JSON template: one entry per checkbox criterion of issue
        <N> (numbered from 1), with the verdict fields empty for the model to
        fill. Criteria are the checkboxes under an "Acceptance criteria"
        heading, or every checkbox in the body when there is no such heading.

  tally <N> <verdicts.json> [--repo <owner/name>] [--unattended]
        Re-reads the issue's criteria (so a dropped entry cannot hide) and
        matches the filled template by ``id``. Prints ``ACCEPTANCE=<state>``,
        a count line, one line per problem item, and -- when any item is
        unverifiable -- a ``PR_TEST_PLAN:`` block of unticked checkboxes, one
        per item, naming who or what can verify it.

Verdict entry fields: ``proof_location`` (``DIFF`` | ``CROSS-REPO`` |
``EXTERNAL``), ``verdict`` (``DONE`` | ``NOT DONE`` | ``UNVERIFIABLE``),
``evidence`` (one line, required), and for ``UNVERIFIABLE`` also ``verifier``
(required) and ``post_merge`` (true when it can only be checked after merge).

States, most severe first:
  unknown       the issue could not be read (exit 2)
  incomplete    a criterion has no valid verdict (exit 1)
  not_done      a criterion is NOT DONE (exit 1)
  blocked       --unattended and an UNVERIFIABLE item is not EXTERNAL and
                post-merge by nature (exit 1)
  unverifiable  everything else is DONE; the listed items need a verifier
                (exit 0)
  done          every criterion DONE (exit 0)
  no_criteria   the issue has no checkbox criteria (exit 0)

Pure logic (``parse_criteria``, ``tally``) is unit-tested in
``tests/test_acceptance_audit.py``. stdlib only.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from git_run import run_gh  # noqa: E402
from utf8_stdio import ensure_utf8_stdio  # noqa: E402

PROOF_LOCATIONS = ("DIFF", "CROSS-REPO", "EXTERNAL")
VERDICTS = ("DONE", "NOT DONE", "UNVERIFIABLE")

_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$")
_CHECKBOX_RE = re.compile(r"^\s*[-*+]\s+\[([ xX])\]\s+(.*\S)\s*$")
_ACCEPTANCE_RE = re.compile(r"acceptance", re.IGNORECASE)


def parse_criteria(body: str) -> List[Dict[str, Any]]:
    """Checkbox criteria of an issue body, numbered from 1.

    Checkboxes under a heading mentioning "acceptance" win; with no such
    section, every checkbox in the body counts. Nested checkboxes are
    criteria of their own. Fenced code blocks are skipped.
    """
    scoped: List[str] = []
    everywhere: List[str] = []
    in_acceptance = False
    in_fence = False
    for line in (body or "").splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        heading = _HEADING_RE.match(line)
        if heading:
            in_acceptance = bool(_ACCEPTANCE_RE.search(heading.group(1)))
            continue
        box = _CHECKBOX_RE.match(line)
        if not box:
            continue
        everywhere.append(box.group(2))
        if in_acceptance:
            scoped.append(box.group(2))
    texts = scoped or everywhere
    return [{"id": i, "criterion": text} for i, text in enumerate(texts, start=1)]


def template(criteria: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        {**c, "proof_location": "", "verdict": "", "evidence": "", "verifier": "", "post_merge": False}
        for c in criteria
    ]


def _normalize_verdict(raw: Any) -> str:
    return re.sub(r"[\s_]+", " ", str(raw or "")).strip().upper()


def _entry_problem(entry: Dict[str, Any]) -> Optional[str]:
    """Why ``entry`` is not a valid verdict, or ``None`` when it is."""
    location = str(entry.get("proof_location") or "").strip().upper()
    verdict = _normalize_verdict(entry.get("verdict"))
    if location not in PROOF_LOCATIONS:
        return "proof_location must be one of " + "/".join(PROOF_LOCATIONS)
    if verdict not in VERDICTS:
        return "verdict must be one of " + "/".join(VERDICTS)
    if not str(entry.get("evidence") or "").strip():
        return "evidence is empty"
    if verdict == "UNVERIFIABLE" and not str(entry.get("verifier") or "").strip():
        return "UNVERIFIABLE needs a verifier"
    return None


def tally(
    criteria: List[Dict[str, Any]], entries: List[Dict[str, Any]], unattended: bool = False,
) -> Tuple[str, List[str]]:
    """``(state, lines)`` for the filled template ``entries`` against ``criteria``."""
    if not criteria:
        return "no_criteria", ["CRITERIA=0 -- judge the issue's prose and say so"]
    by_id: Dict[int, Dict[str, Any]] = {}
    for entry in entries:
        try:
            by_id[int(entry.get("id"))] = entry
        except (TypeError, ValueError):
            continue

    missing: List[str] = []
    not_done: List[str] = []
    blocked: List[str] = []
    unverifiable: List[Dict[str, Any]] = []
    done = 0
    for criterion in criteria:
        cid, text = criterion["id"], criterion["criterion"]
        entry = by_id.get(cid)
        problem = "no verdict" if entry is None else _entry_problem(entry)
        if problem:
            missing.append(f"MISSING: #{cid} {text} ({problem})")
            continue
        verdict = _normalize_verdict(entry.get("verdict"))
        location = str(entry.get("proof_location")).strip().upper()
        evidence = str(entry.get("evidence")).strip()
        if verdict == "DONE":
            done += 1
        elif verdict == "NOT DONE":
            not_done.append(f"NOT_DONE: #{cid} {text} -- {evidence}")
        else:
            item = {"id": cid, "criterion": text, "location": location, "evidence": evidence,
                    "verifier": str(entry.get("verifier")).strip()}
            unverifiable.append(item)
            if unattended and not (location == "EXTERNAL" and entry.get("post_merge") is True):
                blocked.append(f"BLOCKED: #{cid} {text} -- {location}, not checkable post-merge")

    if missing:
        state = "incomplete"
    elif not_done:
        state = "not_done"
    elif blocked:
        state = "blocked"
    elif unverifiable:
        state = "unverifiable"
    else:
        state = "done"

    lines = [
        f"CRITERIA={len(criteria)} DONE={done} NOT_DONE={len(not_done)} "
        f"UNVERIFIABLE={len(unverifiable)} MISSING={len(missing)}",
        *missing, *not_done, *blocked,
    ]
    for item in unverifiable:
        lines.append(f"UNVERIFIABLE: #{item['id']} {item['criterion']} -- {item['evidence']}")
    if unverifiable:
        lines.append("PR_TEST_PLAN:")
        lines.extend(
            f"- [ ] {item['criterion']} -- not verifiable from this session "
            f"({item['location']}); verify: {item['verifier']}"
            for item in unverifiable
        )
    return state, lines


_EXIT = {"done": 0, "unverifiable": 0, "no_criteria": 0,
         "not_done": 1, "incomplete": 1, "blocked": 1}


def fetch_body(number: int, repo: Optional[str]) -> Tuple[Optional[str], str]:
    """``(body, "")`` or ``(None, reason)`` when the issue could not be read."""
    args = ["issue", "view", str(number), "--json", "body"]
    if repo:
        args += ["--repo", repo]
    try:
        proc = run_gh(args, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"gh failed: {type(exc).__name__}"
    if proc.returncode != 0:
        return None, (proc.stderr or proc.stdout or "gh exited non-zero").strip().splitlines()[0][:200]
    try:
        return str(json.loads(proc.stdout or "{}").get("body") or ""), ""
    except ValueError:
        return None, "gh returned non-JSON"


def main(argv: Optional[List[str]] = None) -> int:
    ensure_utf8_stdio()
    parser = argparse.ArgumentParser(description="Per-criterion acceptance audit (fleet-config#958).")
    sub = parser.add_subparsers(dest="cmd", required=True)
    ext = sub.add_parser("extract")
    ext.add_argument("number", type=int)
    ext.add_argument("--repo")
    tal = sub.add_parser("tally")
    tal.add_argument("number", type=int)
    tal.add_argument("verdicts", type=Path)
    tal.add_argument("--repo")
    tal.add_argument("--unattended", action="store_true")
    args = parser.parse_args(argv)

    body, reason = fetch_body(args.number, args.repo)
    if body is None:
        print(f"ACCEPTANCE=unknown reason={reason}")
        return 2
    criteria = parse_criteria(body)
    if args.cmd == "extract":
        print(json.dumps(template(criteria), indent=2, ensure_ascii=False))
        return 0

    try:
        entries = json.loads(args.verdicts.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"ACCEPTANCE=incomplete reason=verdicts file unreadable: {exc}")
        return 1
    if not isinstance(entries, list):
        entries = []
    state, lines = tally(criteria, [e for e in entries if isinstance(e, dict)], args.unattended)
    print(f"ACCEPTANCE={state}")
    for line in lines:
        print(line)
    return _EXIT[state]


if __name__ == "__main__":
    sys.exit(main())
