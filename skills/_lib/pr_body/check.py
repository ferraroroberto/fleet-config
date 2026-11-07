"""One checker for a finish PR's body, run after `gh pr create`/`edit` (fleet-config#1913).

Rules, each refused with its own message naming the fix:
  closing-refs  GitHub's own `closingIssuesReferences` must be exactly the branch's issue, or empty
                for a PR whose body says `Part of #N` / `Progresses #N` (the keyword matches on
                substrings, so the prose cannot be trusted to decide)
  images        no image markdown, `<img>` or GitHub attachment URL (a private repo passes
                `--allow-images`)
  attribution   no AI attribution line, trailer or session link
  merge-danger  a well-formed `**Merge danger:**` line (docs/skills/merge-danger.md)
  red-first     a PR whose issue is labelled `bug` needs a `gate_evidence.py check-red` record (the new test
                failed on a tree other than the fix tree) or a `**Red-first:** exempt · <reason>` line
  ticked-box    a ticked test-plan box needs a `DONE` acceptance verdict (`--verdicts`, the file
                `acceptance_audit.py` tallied) or, for a gate box, a `FRESH` gate_evidence record

Exit 0 pass, 1 refused, 3 NOT CONFIRMED -- the PR, the issue's labels, the verdicts or a gate or red record
could not be read, which is never a pass. Forward-only: it reads one PR, there is no baseline. stdlib only.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import gate_evidence  # noqa: E402
from fleet_core.utf8_stdio import ensure_utf8_stdio  # noqa: E402
from git_run import run_gh  # noqa: E402

# A `gh` read of one PR is a short lookup; past this it is reported unreadable, not waited on.
GH_TIMEOUT_S = 60

_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
_TICKED_RE = re.compile(r"^\s*[-*+]\s+\[[xX]\]\s+(.*\S)\s*$")
_PART_OF_RE = re.compile(r"^\s*(?:part of|progresses)\s+#(\d+)\b", re.IGNORECASE | re.MULTILINE)
_GATE_WORD_RE = re.compile(r"\bgate\b", re.IGNORECASE)

_EXEMPT_RE = re.compile(r"^\*\*Red-first:\*\*\s+exempt\s+·\s+(?P<reason>\S.*)$", re.MULTILINE)


def _red_how() -> str:
    """The exact remedy, with this interpreter and the helper's real path so a lane can paste the command."""
    run = f"{Path(sys.executable).as_posix()} {Path(gate_evidence.__file__).resolve().as_posix()} run --label red -- <the new test's command>"
    return (f"to produce it, `git stash` the fix (keep the new test in the tree), run `{run}` and see it exit non-zero, "
            f"then `git stash pop`; or, for a bug with no automatable test, add `**Red-first:** exempt · <reason>` to the PR body")


_IMAGE_RES = (
    re.compile(r"!\[[^\]]*\]\s*[(\[]"),
    re.compile(r"<img\b", re.IGNORECASE),
    re.compile(r"user-attachments/", re.IGNORECASE),
    re.compile(r"user-images\.\w+usercontent\.\w+", re.IGNORECASE),
)
_ATTRIBUTION_RES = (
    (re.compile(r"\bclaude\.ai/(?:code|chat|share)/\S*", re.IGNORECASE), "a claude.ai session link"),
    (re.compile(r"\bsession_[0-9A-Za-z]{10,}"), "a session id"),
    (re.compile(r"generated with\s+\[?claude", re.IGNORECASE), "a `Generated with Claude Code` line"),
    (re.compile(r"^\s*co-authored-by:.*(?:claude|anthropic|codex|openai|copilot|gpt|gemini|grok)",
                re.IGNORECASE | re.MULTILINE), "a `Co-Authored-By` trailer naming an AI"),
    (re.compile(r"^\s*claude-session:", re.IGNORECASE | re.MULTILINE), "a `Claude-Session:` trailer"),
)
_DANGER_RE = re.compile(
    r"^\*\*Merge danger:\*\*\s+(?P<door>one-way|two-way) door\s+·\s+blast radius:\s+(?P<radius>[A-Za-z][\w-]*)"
    r"(?:\s+·\s+(?P<reason>\S.*))?\s*$",
    re.MULTILINE,
)


def find_danger_line(body: str) -> Optional[str]:
    """The first well-formed `Merge danger:` line, or None. A one-way door must state its reason."""
    for match in _DANGER_RE.finditer(body):
        if match.group("door") == "one-way" and not match.group("reason"):
            continue
        return match.group(0).strip()
    return None


def find_exempt_line(body: str) -> Optional[str]:
    """The first `**Red-first:** exempt · <reason>` line outside code, or None. The reason is required."""
    match = _EXEMPT_RE.search(prose(body))
    return match.group(0).strip() if match else None


def _red_findings(red_state: str) -> Tuple[List[str], List[str]]:
    if red_state == "RED":
        return [], []
    if red_state == "UNKNOWN":
        return [], ["red-first: the red record could not be read (the tree could not be hashed)"]
    why = {"NOT_RED": "its recorded run of `red` did not fail (exit 0, or it never started)",
           "SAME_TREE": "its failing run of `red` was on this very tree; it must be taken on a different tree (the pre-fix one)"}.get(
        red_state, "no `red` run is recorded")
    return [f"red-first: this bug PR has no red-first evidence: {why}; {_red_how()}"], []


def prose(body: str, *, inline_code: bool = True) -> str:
    """The body without fenced blocks (and inline code unless kept), so a rule quoted as code is not a violation."""
    kept: List[str] = []
    in_fence = False
    for line in body.splitlines():
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if not in_fence:
            kept.append(_INLINE_CODE_RE.sub("", line) if inline_code else line)
    return "\n".join(kept)


def ticked_boxes(body: str) -> List[str]:
    """Ticked checkbox texts outside code fences; backticks stay so a quoted term still matches its criterion."""
    return [m.group(1) for m in map(_TICKED_RE.match, prose(body, inline_code=False).splitlines()) if m]


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[`*_]", "", text)).strip().lower().rstrip(".;:")


def _done_criteria(verdicts: List[Dict[str, Any]]) -> List[str]:
    return [_norm(str(v.get("criterion") or "")) for v in verdicts
            if re.sub(r"[\s_]+", " ", str(v.get("verdict") or "")).strip().upper() == "DONE"]


def _closing_findings(closing: List[int], issue: int, body: str) -> List[str]:
    refs = set(closing)
    if refs == {issue}:
        return []
    part_of = {int(n) for n in _PART_OF_RE.findall(prose(body))}
    if not refs:
        if issue in part_of:
            return []
        return [f"closing-refs: GitHub sees this PR closing nothing; add `Closes #{issue}` if it finishes the issue, "
                f"or `Part of #{issue}` if it only advances it"]
    others = ", ".join(f"#{n}" for n in sorted(refs - {issue}))
    if issue not in refs:
        return [f"closing-refs: GitHub sees this PR closing {others}, not the branch's issue #{issue}; "
                f"reword so only `Closes #{issue}` is a closing keyword (use `Part of` for the rest)"]
    return [f"closing-refs: GitHub sees this PR also closing {others}; keep `Closes #{issue}` and use "
            f"`Part of` for the rest"]


def _box_findings(boxes: List[str], verdicts: List[Dict[str, Any]], gate_state: str) -> Tuple[List[str], List[str]]:
    refused: List[str] = []
    unconfirmed: List[str] = []
    done = _done_criteria(verdicts)
    for box in boxes:
        text = _norm(box)
        if any(c and (c in text or text in c) for c in done):
            continue
        if _GATE_WORD_RE.search(box):
            if gate_state == "FRESH":
                continue
            if gate_state == "UNKNOWN":
                unconfirmed.append(f"ticked box {box!r} leans on the gate record, which could not be read")
                continue
            refused.append(f"ticked-box: {box!r} claims the gate but its record is {gate_state}; re-run the gate "
                           f"through gate_evidence.py run, or untick the box")
            continue
        refused.append(f"ticked-box: {box!r} matches no DONE acceptance verdict; untick it (a box is ticked only "
                       f"for something observed), or reword it to the criterion it proves and pass --verdicts")
    return refused, unconfirmed


def evaluate(
    body: str, *, closing: List[int], issue: int, verdicts: List[Dict[str, Any]], gate_state: str, allow_images: bool,
    is_bug: bool = False, red_state: str = "MISSING",
) -> Tuple[List[str], List[str]]:
    """``(refused, unconfirmed)``: one ``<rule>: <fix>`` line per violation, one line per unestablished fact."""
    refused = _closing_findings(closing, issue, body)
    text = prose(body)
    if not allow_images and any(rx.search(text) for rx in _IMAGE_RES):
        refused.append("images: a public PR carries no screenshot or image (markdown, `<img>` or attachment URL); "
                       "delete it and describe the result in text")
    found = [what for rx, what in _ATTRIBUTION_RES if rx.search(text)]
    if found:
        refused.append(f"attribution: the body carries {' and '.join(found)}; delete it (no AI attribution or session link in a PR)")
    if find_danger_line(body) is None:
        refused.append("merge-danger: add `**Merge danger:** two-way door · blast radius: <word>` "
                       "(one-way appends ` · <reason>`)")
    box_refused, unconfirmed = _box_findings(ticked_boxes(body), verdicts, gate_state)
    if is_bug and find_exempt_line(body) is None:
        red_refused, red_unconfirmed = _red_findings(red_state)
        refused, unconfirmed = refused + red_refused, unconfirmed + red_unconfirmed
    return refused + box_refused, unconfirmed


def parse_pr(raw: str) -> Tuple[str, List[int]]:
    """``(body, closing issue numbers)`` from `gh pr view --json body,closingIssuesReferences`.

    A payload missing either field raises ``ValueError``: an absent list is not an empty one.
    """
    data = json.loads(raw)
    if not isinstance(data, dict) or not isinstance(data.get("body"), str):
        raise ValueError("no body in the PR payload")
    refs = data.get("closingIssuesReferences")
    if not isinstance(refs, list):
        raise ValueError("no closingIssuesReferences in the PR payload")
    return data["body"], [int(r["number"]) for r in refs]


def _read_pr(pr: str, repo: Optional[str]) -> Tuple[str, List[int]]:
    args = ["pr", "view", pr, "--json", "body,closingIssuesReferences"]
    if repo:
        args += ["--repo", repo]
    try:
        proc = run_gh(args, timeout=GH_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError(f"gh failed: {type(exc).__name__}") from exc
    if proc.returncode != 0:
        raise ValueError(((proc.stderr or proc.stdout or "gh exited non-zero").strip().splitlines() or ["?"])[0][:200])
    try:
        return parse_pr(proc.stdout or "")
    except (ValueError, KeyError, TypeError) as exc:
        raise ValueError(f"unreadable PR payload: {exc}") from exc


def _read_issue_labels(issue: int, repo: Optional[str]) -> List[str]:
    args = ["issue", "view", str(issue), "--json", "labels"]
    if repo:
        args += ["--repo", repo]
    try:
        proc = run_gh(args, timeout=GH_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError(f"gh failed: {type(exc).__name__}") from exc
    if proc.returncode != 0:
        raise ValueError(((proc.stderr or proc.stdout or "gh exited non-zero").strip().splitlines() or ["?"])[0][:200])
    try:
        labels = json.loads(proc.stdout or "")["labels"]
        return [str(label["name"]) for label in labels]
    except (ValueError, KeyError, TypeError) as exc:
        raise ValueError(f"unreadable issue payload: {exc}") from exc


def _read_verdicts(path: Optional[Path]) -> List[Dict[str, Any]]:
    if path is None:
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("verdicts file is not a JSON list")
    return [d for d in data if isinstance(d, dict)]


def _not_confirmed(reason: str) -> int:
    print(f"PR_BODY=not_confirmed reason={reason}")
    return 3


def main(argv: Optional[List[str]] = None) -> int:
    ensure_utf8_stdio()
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--pr", required=True, help="PR number, URL or branch")
    parser.add_argument("--issue", type=int, required=True, help="the branch's issue number")
    parser.add_argument("--repo", help="owner/name when not the current checkout")
    parser.add_argument("--verdicts", type=Path, help="the filled acceptance_audit.py verdicts JSON")
    parser.add_argument("--gate-repo", type=Path, default=Path.cwd(), help="checkout whose gate record backs a gate box")
    parser.add_argument("--gate-label", default="gate")
    parser.add_argument("--red-label", default="red", help="label of the failing pre-fix run a bug PR needs")
    parser.add_argument("--allow-images", action="store_true", help="a private repo opting out of the image rule")
    args = parser.parse_args(argv)

    try:
        body, closing = _read_pr(args.pr, args.repo)
    except ValueError as exc:
        return _not_confirmed(f"the PR could not be read: {exc}")
    boxes = ticked_boxes(body)
    verdicts: List[Dict[str, Any]] = []
    if boxes and args.verdicts is not None:
        try:
            verdicts = _read_verdicts(args.verdicts)
        except (OSError, ValueError) as exc:
            return _not_confirmed(f"the verdicts file could not be read: {exc}")
    gate_state = "MISSING"
    if any(_GATE_WORD_RE.search(b) for b in boxes):
        gate_state, _ = gate_evidence.check(args.gate_repo.resolve(), args.gate_label)

    is_bug, red_state = False, "MISSING"
    try:
        is_bug = "bug" in _read_issue_labels(args.issue, args.repo)
    except ValueError as exc:
        return _not_confirmed(f"the issue's labels could not be read: {exc}")
    if is_bug and find_exempt_line(body) is None:
        red_state, _ = gate_evidence.check_red(args.gate_repo.resolve(), args.red_label)

    refused, unconfirmed = evaluate(body, closing=closing, issue=args.issue, verdicts=verdicts, gate_state=gate_state,
                                    allow_images=args.allow_images, is_bug=is_bug, red_state=red_state)
    if refused:
        print("PR_BODY=refused")
        for line in refused:
            print(f"REFUSED {line}")
        for line in unconfirmed:
            print(f"NOT_CONFIRMED {line}")
        return 1
    if unconfirmed:
        print("PR_BODY=not_confirmed")
        for line in unconfirmed:
            print(f"NOT_CONFIRMED {line}")
        return 3
    print("PR_BODY=pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
