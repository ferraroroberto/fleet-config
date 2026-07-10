"""Shared "diff two JSON snapshots into a one-line summary" logic behind the
/config-map and /system-map week-over-week Slack posts (fleet-config#318).

Both skills follow the same shape: slice the strict-JSON object out of a
``window.X = {...};`` snapshot file, flatten it to ``{key: entry}``, diff two
generations of the file (the just-regenerated working copy vs. the previously
committed one via ``git show <ref>:<file>``), and print one summary line.
Before this module existed, ``config-map/whatchanged.py`` and
``system-map/whatchanged.py`` reimplemented that generic scaffolding
near-verbatim — the entry-diff, the CLI's UTF-8-stdout reconfigure + git-show
read + baseline handling — with only the domain-specific ``parse_*`` (which
inventory dimension keys what) and the human wording genuinely differing.
Each skill now supplies only its own ``parse_*``/``format_line`` and this
module owns the rest.

``diff_entries`` is pure (no I/O) so ``tests/run_acceptance.py`` can exercise
it directly; ``run_cli`` does the git read and is what each skill's CLI
delegates to.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Callable, Dict, Optional

# U+2212 MINUS SIGN for removals (reads cleaner than a hyphen, which looks like
# a CLI flag) — matches both skills' '+added, −removed' examples.
MINUS = "−"


def diff_entries(prev: Dict[str, dict], cur: Dict[str, dict]) -> dict:
    """Return ``{'added': [...], 'removed': [...], 'updated': [...]}`` (sorted keys).

    * ``added`` / ``removed`` — keys present in only the new / only the old snapshot.
    * ``updated`` — keys in both whose entry content changed.
    """
    added = sorted(set(cur) - set(prev))
    removed = sorted(set(prev) - set(cur))
    updated = sorted(
        k for k in set(cur) & set(prev)
        if json.dumps(cur[k], sort_keys=True) != json.dumps(prev[k], sort_keys=True)
    )
    return {"added": added, "removed": removed, "updated": updated}


def summarize(
    prev_js: Optional[str],
    cur_js: str,
    parse_fn: Callable[[str], Dict[str, dict]],
    format_fn: Callable[[dict], str],
) -> str:
    """The Slack-post line; ``baseline`` when there is no prior snapshot (a
    first-run repo has no prior commit to diff against)."""
    if not prev_js:
        return "baseline"
    return format_fn(diff_entries(parse_fn(prev_js), parse_fn(cur_js)))


def read_git_snapshot(file_path: str, ref: str) -> Optional[str]:
    """The previous snapshot's text at ``ref``, or ``None`` if the file didn't
    exist there yet (first run / baseline)."""
    try:
        return subprocess.run(
            ["git", "show", f"{ref}:{file_path}"],
            capture_output=True, text=True, encoding="utf-8", check=True,
        ).stdout
    except subprocess.CalledProcessError:
        return None


def run_cli(
    description: str,
    default_file: str,
    parse_fn: Callable[[str], Dict[str, dict]],
    format_fn: Callable[[dict], str],
    argv: Optional[list] = None,
) -> int:
    """Shared CLI body: UTF-8 stdout, ``--file``/``--ref`` args, git-show read, print."""
    # Captured stdout falls back to cp1252 on Windows, which can't encode the
    # U+2212 minus sign — force UTF-8 so the line survives the skill's capture.
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - best-effort; real terminals are already utf-8
        pass

    ap = argparse.ArgumentParser(description=description)
    ap.add_argument("--file", default=default_file,
                    help="working snapshot file (the current generation)")
    ap.add_argument("--ref", default="HEAD",
                    help="git ref to diff against (the previous snapshot)")
    args = ap.parse_args(argv)

    cur = Path(args.file).read_text(encoding="utf-8")
    prev = read_git_snapshot(args.file, args.ref)

    print(summarize(prev, cur, parse_fn, format_fn))
    return 0
