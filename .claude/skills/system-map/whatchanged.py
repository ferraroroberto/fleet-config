"""Week-over-week 'what changed' line for the /system-map Slack post.

Produces a one-liner like ``+whatsapp-radar, −suna, 3 repos updated`` by diffing
two ``fleet.data.js`` snapshots — the way ``/audit-fleet`` reports week-over-week.
The skill calls this after reconciling the map, diffing the freshly-edited
working file against the previously-committed one (``git show HEAD:…``), so the
line summarises exactly what moved since the last run.

Pure logic, no I/O in the diff functions, so ``tests/run_acceptance.py`` can
exercise them. The CLI at the bottom does the git read and is what the skill
invokes::

    py .claude/skills/system-map/whatchanged.py            # vs HEAD, default file
    py .claude/skills/system-map/whatchanged.py --ref main # vs another ref

Repo identity matches the drift guard in ``run_acceptance.py``: a card's key is
its ``repo`` field when present, else its ``nm``. Only the repo-bearing sections
count — ``access`` / ``edge`` / ``external`` / ``compute`` / ``principles`` are
not fleet repos.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Dict, Optional

# The sections of fleet.data.js whose cards are real fleet repos (same set the
# drift guard in tests/run_acceptance.py reconciles against projects.toml).
_SECTIONS = ("governance", "enabling", "web", "pipe")

# U+2212 MINUS SIGN for removals (reads cleaner than a hyphen, which looks like a
# CLI flag) — matches the issue's '+whatsapp-radar, −suna' example.
_MINUS = "−"


def parse_fleet(js_text: str) -> Dict[str, dict]:
    """Map each repo key → its card dict, from a ``fleet.data.js`` body.

    Slices the strict-JSON object out of ``window.FLEET = { … };`` exactly the
    way ``run_acceptance.py`` does, then keys each card by ``repo``-or-``nm``.
    """
    data = json.loads(js_text[js_text.index("{"): js_text.rindex("}") + 1])
    return {
        e.get("repo", e["nm"]): e
        for section in _SECTIONS
        for e in data.get(section, [])
    }


def diff_fleet(prev_js: str, cur_js: str) -> dict:
    """Return ``{'added': [...], 'removed': [...], 'updated': [...]}`` (sorted).

    * ``added`` / ``removed`` — repos present in only the new / only the old snapshot.
    * ``updated`` — repos in both snapshots whose card content changed (a tweaked
      description, port, layer move within a section, …).
    """
    prev = parse_fleet(prev_js)
    cur = parse_fleet(cur_js)
    added = sorted(set(cur) - set(prev))
    removed = sorted(set(prev) - set(cur))
    updated = sorted(
        k for k in set(cur) & set(prev)
        if json.dumps(cur[k], sort_keys=True) != json.dumps(prev[k], sort_keys=True)
    )
    return {"added": added, "removed": removed, "updated": updated}


def format_line(diff: dict) -> str:
    """One-line human summary, e.g. ``+a, −b, 3 repos updated``.

    Added/removed repos are named; updates are counted. An empty diff (a no-op
    week) reads ``no fleet changes``.
    """
    parts = [f"+{r}" for r in diff["added"]]
    parts += [f"{_MINUS}{r}" for r in diff["removed"]]
    n = len(diff["updated"])
    if n:
        parts.append(f"{n} repo{'s' if n != 1 else ''} updated")
    return ", ".join(parts) if parts else "no fleet changes"


def summarize(prev_js: Optional[str], cur_js: str) -> str:
    """The Slack-post line; ``baseline`` when there is no prior snapshot.

    The first run (no committed ``fleet.data.js`` at the ref) has nothing to
    diff against, so it reports ``baseline`` rather than a misleading all-added.
    """
    if not prev_js:
        return "baseline"
    return format_line(diff_fleet(prev_js, cur_js))


def main(argv: Optional[list[str]] = None) -> int:
    import argparse
    import subprocess

    # Captured stdout falls back to cp1252 on Windows, which can't encode the
    # U+2212 minus sign — force UTF-8 so the line survives the skill's capture.
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - best-effort; real terminals are already utf-8
        pass

    ap = argparse.ArgumentParser(
        description="Print the /system-map week-over-week change line."
    )
    ap.add_argument("--file", default="architecture/fleet.data.js",
                    help="working fleet.data.js (the current snapshot)")
    ap.add_argument("--ref", default="HEAD",
                    help="git ref to diff against (the previous snapshot)")
    args = ap.parse_args(argv)

    cur = Path(args.file).read_text(encoding="utf-8")
    try:
        prev: Optional[str] = subprocess.run(
            ["git", "show", f"{args.ref}:{args.file}"],
            capture_output=True, text=True, encoding="utf-8", check=True,
        ).stdout
    except subprocess.CalledProcessError:
        prev = None  # file absent at that ref → first run / baseline

    print(summarize(prev, cur))
    return 0


if __name__ == "__main__":
    sys.exit(main())
