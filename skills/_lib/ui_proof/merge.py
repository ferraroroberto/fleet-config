"""`ui_proof merge`: the one scripted `gh pr merge`, behind the same visual-proof gate as the hook.

The `ui_proof_merge_guard` hook sees only commands an agent types, so a merge a
script spawns never reaches it. Every merge a skill or helper runs goes through
`merge()` instead: it asks `merge_gate.run` first and runs `gh pr merge` only on
`GATE=pass`. `GATE=refuse` (exit 1) and anything else (exit 3, unknown) refuse
without merging, exactly as the hook does. The acceptance run's `merge_wrapper`
check fails on any `pr merge` argv outside this file.

The merge always names the repo the gate judged (`-R <owner/name>`), so the PR
that merges is the PR that was checked, whatever directory the caller is in.

The owner's escape is the hook's: `FLEET_UI_PROOF_BYPASS=<reason>` in the
environment skips the gate, and the merge is appended to the same
`<hooks state>/ui-proof-bypass.jsonl`.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

import git_run
from fleet_core import runtime_files
from fleet_core.hooks_state import state_dir

from ui_proof import merge_gate

BYPASS_ENV = "FLEET_UI_PROOF_BYPASS"
BYPASS_LOG = "ui-proof-bypass.jsonl"
# `-R` is set from the repo the gate judged; a second one could point gh elsewhere.
_REPO_FLAGS = ("-R", "--repo")
# Generous for `--auto` registration or a squash merge; a hang must not stall a wave forever.
_MERGE_TIMEOUT_S = 120

Gate = Callable[[Path, Optional[str], Optional[str]], Tuple[int, List[str]]]
RunGh = Callable[..., subprocess.CompletedProcess]


def _fields(lines: Sequence[str]) -> dict:
    return dict(line.split("=", 1) for line in lines if "=" in line)


def _judged_slug(fields: dict) -> Optional[str]:
    """The owner/name the gate judged: `PR=<slug>#<n>` or, for n/a, `REPO=<slug>`."""
    if fields.get("PR") and "#" in fields["PR"]:
        return fields["PR"].rsplit("#", 1)[0]
    return fields.get("REPO") or None


def _log_bypass(reason: str, cwd: Path, selector: str, slug: Optional[str]) -> Path:
    path = state_dir() / BYPASS_LOG
    record = {"utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "reason": reason,
              "session_id": "", "cwd": str(cwd),
              "merges": [f"ui_proof merge {selector}" + (f" -R {slug}" if slug else "")]}
    runtime_files.make_dirs(path.parent, parents=True, exist_ok=True)
    with runtime_files.open_for_write(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")
    return path


def merge(cwd: Path, selector: str, slug: Optional[str], gh_args: Sequence[str],
          gate: Gate = merge_gate.run, run_gh: RunGh = git_run.run_gh) -> Tuple[int, List[str]]:
    """`(exit code, KEY=value lines)`: gh's own exit code once merged, 1 refused, 3 unknown, 2 bad call."""
    if any(a.split("=", 1)[0] in _REPO_FLAGS for a in gh_args):
        return 2, ["MERGE=not_run", "REASON=pass the repo as --repo-slug, not -R/--repo among the gh args"]
    reason = os.environ.get(BYPASS_ENV, "").strip()
    if reason:
        try:
            log = str(_log_bypass(reason, cwd, selector, slug))
        except OSError as exc:
            log = f"(not logged: {exc})"
        lines = ["GATE=bypassed", f"BYPASS_REASON={reason}", f"BYPASS_LOG={log}"]
        target = slug
    else:
        code, lines = gate(cwd, selector, slug)
        fields = _fields(lines)
        if code == 1 and fields.get("GATE") == "refuse":
            return 1, lines + ["MERGE=refused",
                               "REASON=the PR touches the UX surface without its visual proof; "
                               "see docs/skills/ux-conformance-gate.md"]
        if code != 0 or fields.get("GATE") != "pass":
            return 3, lines + ["MERGE=refused", "REASON=proof state unknown, so the merge did not run; "
                               "this is not a missing-proof verdict"]
        target = _judged_slug(fields)
        if not target:
            return 3, lines + ["MERGE=refused", "REASON=the gate passed but named no repo to merge in"]
    argv = ["pr", "merge", selector, *(["-R", target] if target else []), *gh_args]
    try:
        res = run_gh(argv, timeout=_MERGE_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return 3, lines + ["MERGE=unknown", f"REASON=gh pr merge did not answer within {_MERGE_TIMEOUT_S} s"]
    except OSError as exc:
        return 3, lines + ["MERGE=not_run", f"REASON=gh could not start: {exc}"]
    out = " ".join(((res.stdout or "") + " " + (res.stderr or "")).split())[-300:]
    state = "ok" if res.returncode == 0 else "failed"
    return res.returncode, lines + [f"MERGE={state}", f"GH_EXIT={res.returncode}", f"GH_OUTPUT={out}"]
