"""Week-over-week 'what changed' line for the /system-map Slack post.

Produces a one-liner like ``+whatsapp-radar, −suna, 3 repos updated`` by diffing
two ``fleet.data.js`` snapshots — the way ``/audit-fleet`` reports week-over-week.
The skill calls this after reconciling the map, diffing the freshly-edited
working file against the previously-committed one (``git show HEAD:…``), so the
line summarises exactly what moved since the last run.

The generic diff/CLI scaffolding (shared with ``/config-map``'s twin) lives in
``skills/_lib/snapshot_diff.py`` — this module supplies only the
fleet-specific ``parse_fleet``/``format_line``.

Pure logic, no I/O in the diff functions, so ``tests/run_acceptance.py`` can
exercise them. The CLI at the bottom does the git read and is what the skill
invokes::

    C:/Users/rober/AppData/Local/Python/bin/python.exe .claude/skills/system-map/whatchanged.py            # vs HEAD, default file
    C:/Users/rober/AppData/Local/Python/bin/python.exe .claude/skills/system-map/whatchanged.py --ref main # vs another ref

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

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "skills" / "_lib"))
import snapshot_diff  # noqa: E402

# The sections of fleet.data.js whose cards are real fleet repos (same set the
# drift guard in tests/run_acceptance.py reconciles against projects.toml).
_SECTIONS = ("governance", "enabling", "web", "pipe")


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
    return snapshot_diff.diff_entries(parse_fleet(prev_js), parse_fleet(cur_js))


def format_line(diff: dict) -> str:
    """One-line human summary, e.g. ``+a, −b, 3 repos updated``.

    Added/removed repos are named; updates are counted. An empty diff (a no-op
    week) reads ``no fleet changes``.
    """
    parts = [f"+{r}" for r in diff["added"]]
    parts += [f"{snapshot_diff.MINUS}{r}" for r in diff["removed"]]
    n = len(diff["updated"])
    if n:
        parts.append(f"{n} repo{'s' if n != 1 else ''} updated")
    return ", ".join(parts) if parts else "no fleet changes"


def summarize(prev_js: Optional[str], cur_js: str) -> str:
    """The Slack-post line; ``baseline`` when there is no prior snapshot.

    The first run (no committed ``fleet.data.js`` at the ref) has nothing to
    diff against, so it reports ``baseline`` rather than a misleading all-added.
    """
    return snapshot_diff.summarize(prev_js, cur_js, parse_fleet, format_line)


def main(argv: Optional[list[str]] = None) -> int:
    return snapshot_diff.run_cli(
        "Print the /system-map week-over-week change line.",
        "architecture/fleet.data.js",
        parse_fleet,
        format_line,
        argv,
    )


if __name__ == "__main__":
    sys.exit(main())
