"""Refuse `gh pr merge` on a UX diff that has no `.proof/<PR>/index.html`.

`/issue-finish` asks every lane to run `ui_proof check . --pr <PR>` before it
merges, and that command refuses a UI diff with no proof page. A lane that
skips the step merged anyway, so the rule lives here, on the merge itself.

Triggers on `PreToolUse` for `Bash` and `PowerShell`. A command with no
`gh pr merge` in it returns at once, on a regex, with no spawn. For each
merge in the command it runs the skills-tier `ui_proof gate` (by subprocess:
`hooks/` never imports `skills/_lib`) with the merge's working directory
(following any `cd`/`Set-Location` before it), its PR argument and its
`-R/--repo`, and answers from the gate's exit code:

- 0, `GATE=pass`: no UX surface touched (`VISUAL_PROOF=n/a`, decided before
  any `gh` call) or the proof page is present and clean. Allowed silently.
- 1, `GATE=refuse`: refused, quoting the gate's `NEED=` line.
- anything else (exit 3 `GATE=unknown`, a crash, a timeout, a missing gate):
  refused with its own "proof state unknown" message, never a pass.

The escape is `FLEET_UI_PROOF_BYPASS=<reason>` in the environment the agent
session was started from (the owner's own terminal). A prefix typed into the
command itself does not reach this process, so an agent cannot set it. Every
bypassed merge is appended to `<hooks state>/ui-proof-bypass.jsonl` and the
session is told the check was skipped.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib  # noqa: E402
import venv_discipline  # noqa: E402

SHELL_TOOLS = ("Bash", "PowerShell")
BYPASS_ENV = "FLEET_UI_PROOF_BYPASS"
BYPASS_LOG = "ui-proof-bypass.jsonl"
_BYPASS_LOG_MAX_BYTES = 1 << 20
# The gate is reached by path, the way notify_on_idle reaches chief_ops: hooks/
# sits inside the fleet-config checkout, so its sibling skills tree is found
# without a machine path.
GATE = Path(__file__).resolve().parent.parent / "skills" / "_lib" / "ui_proof"
# Two `gh` calls at most, each bounded at 20 s in the gate; stays under Claude
# Code's 60 s hook timeout, past which the harness would let the merge through.
GATE_TIMEOUT_S = 50

_HINT_RE = re.compile(r"\bgh(?:\.exe)?\b.*\bpr\b.*\bmerge\b", re.IGNORECASE | re.DOTALL)
_CONTINUATION_RE = re.compile(r"[\\`]\r?\n")
_SEGMENT_SPLIT_RE = re.compile(r"[\n;|&]+")
_ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_REPO_FLAGS = ("-R", "--repo")
# `gh pr merge` options that take a value, so the value is never read as the PR.
_VALUE_FLAGS = {"-R", "--repo", "-b", "--body", "-F", "--body-file", "-t", "--subject",
                "--match-head-commit", "-A", "--author-email"}


@dataclass
class MergeCall:
    """One `gh pr merge` in the command: where it runs, which PR, which repo."""

    cwd: Optional[Path]
    selector: Optional[str]
    slug: Optional[str]
    text: str


def _flag_value(words: List[str], i: int) -> Tuple[Optional[str], int]:
    """`(value, next index)` for the option at `words[i]`, `--flag=value` or `--flag value`."""
    word = words[i]
    if "=" in word and word.startswith("--"):
        return word.split("=", 1)[1], i + 1
    return (words[i + 1] if i + 1 < len(words) else None), i + 2


def parse_merge(words: List[str]) -> Optional[Tuple[Optional[str], Optional[str]]]:
    """`(selector, slug)` when `words` is a `gh pr merge` invocation, else None."""
    i = 0
    while i < len(words) and _ENV_ASSIGN_RE.match(words[i]):
        i += 1
    if i >= len(words) or Path(words[i].strip("'\"")).name.lower() not in ("gh", "gh.exe"):
        return None
    i += 1
    slug: Optional[str] = None
    while i < len(words) and words[i].split("=", 1)[0] in _REPO_FLAGS:
        slug, i = _flag_value(words, i)
    if words[i:i + 2] != ["pr", "merge"]:
        return None
    i += 2
    selector: Optional[str] = None
    while i < len(words):
        word = words[i]
        if word.split("=", 1)[0] in _VALUE_FLAGS:
            value, i = _flag_value(words, i)
            if word.split("=", 1)[0] in _REPO_FLAGS:
                slug = value
            continue
        if not word.startswith("-") and selector is None:
            selector = word
        i += 1
    return selector, slug


def merge_calls(cmd: str, base: Path) -> List[MergeCall]:
    """Every `gh pr merge` in `cmd`, with the directory it runs in.

    The directory starts at the payload cwd and follows each `cd`-like
    segment before the merge; one the command string cannot pin down
    (`cd $X`, a missing directory) makes it unknown (None).
    """
    calls: List[MergeCall] = []
    current: Optional[Path] = base
    stack: List[Path] = []
    for segment in _SEGMENT_SPLIT_RE.split(_CONTINUATION_RE.sub(" ", cmd)):
        words = _lib.shell_tokens(segment, unquote=True)
        if not words:
            continue
        verb = words[0].lower()
        if verb in _lib.CD_VERBS | _lib.PUSH_VERBS | _lib.POP_VERBS:
            change = venv_discipline.directory_change(segment, current, stack) if current else None
            if change is None:
                current = None
            else:
                kind, target = change
                if kind == "push" and current is not None:
                    stack.append(current)
                elif kind == "pop" and stack:
                    stack.pop()
                current = target
            continue
        parsed = parse_merge(words)
        if parsed is not None:
            calls.append(MergeCall(current, parsed[0], parsed[1], segment.strip()))
    return calls


def run_gate(call: MergeCall) -> Tuple[Optional[int], Dict[str, str], str]:
    """`(exit code or None, KEY=value lines, stderr or failure)` from `ui_proof gate`."""
    argv = [sys.executable, str(GATE), "gate", str(call.cwd)]
    if call.selector:
        argv += ["--pr", call.selector]
    if call.slug:
        argv += ["--repo-slug", call.slug]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=GATE_TIMEOUT_S, creationflags=_lib.NO_WINDOW,
                              env=dict(os.environ, PYTHONUTF8="1"))
    except subprocess.TimeoutExpired:
        return None, {}, f"the gate did not answer within {GATE_TIMEOUT_S} s"
    except (OSError, subprocess.SubprocessError) as exc:
        return None, {}, f"the gate could not start: {exc}"
    fields = dict(line.split("=", 1) for line in proc.stdout.splitlines() if "=" in line)
    return proc.returncode, fields, (proc.stderr or "").strip()[-300:]


def refusal(call: MergeCall, code: Optional[int], fields: Dict[str, str], err: str) -> Optional[str]:
    """The refusal text for one merge, or None when the gate passed it."""
    if code == 0 and fields.get("GATE") == "pass":
        return None
    if code == 1 and fields.get("GATE") == "refuse":
        return (f"Refused: `{call.text}` merges a PR ({fields.get('PR', '?')}) that touches the UX "
                f"surface without its visual proof. Missing: {fields.get('NEED') or '(not named)'}. "
                f"Steps: docs/skills/ux-conformance-gate.md; then merge again. ({fields.get('DETAIL', '')})")
    why = fields.get("REASON") or err or f"exit {code}, GATE={fields.get('GATE', '(none)')}"
    return (f"Refused: proof state unknown for `{call.text}`. The visual-proof check could not run "
            f"({why}), so whether this merge needs a proof page is not established; this is not a "
            f"missing-proof verdict. Fix the cause and merge again. If the check itself is broken, the "
            f"owner can start the session with {BYPASS_ENV}=<reason> set in his own terminal; every "
            f"bypassed merge is logged.")


def _log_bypass(payload: Dict, calls: List[MergeCall], reason: str) -> Path:
    path = _lib.state_dir() / BYPASS_LOG
    record = {"utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "reason": reason,
              "session_id": str(payload.get("session_id") or ""), "cwd": str(_lib.cwd(payload)),
              "merges": [c.text[:300] for c in calls]}
    try:
        _lib.append_jsonl_rotating(path, record, _BYPASS_LOG_MAX_BYTES)
    except OSError as exc:
        print(f"ui_proof_merge_guard: could not log the bypass to {path}: {exc}", file=sys.stderr)
    return path


def main() -> None:
    payload = _lib.read_stdin_json()
    if _lib.tool_name(payload) not in SHELL_TOOLS:
        _lib.allow()
    cmd = _lib.command_string(payload)
    if not _HINT_RE.search(cmd):
        _lib.allow()
    calls = merge_calls(cmd, _lib.cwd(payload))
    if not calls:
        _lib.allow()
    reason = os.environ.get(BYPASS_ENV, "").strip()
    if reason:
        log = _log_bypass(payload, calls, reason)
        _lib.warn(f"ui_proof_merge_guard: {BYPASS_ENV} is set ({reason}), so this merge skipped the "
                  f"visual-proof check. Logged to {log}.")
    refusals = []
    for call in calls:
        if call.cwd is None:
            text = refusal(call, None, {"REASON": "the command changes directory to a path the hook "
                                                  "cannot read, so the merge's repo is not known"}, "")
        else:
            text = refusal(call, *run_gate(call))
        if text:
            refusals.append(text)
    if refusals:
        _lib.block("\n\n".join(refusals))
    _lib.allow()


if __name__ == "__main__":
    main()
