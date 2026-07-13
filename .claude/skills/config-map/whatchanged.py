"""Week-over-week 'what changed' line for the /config-map Slack post.

Produces a one-liner like ``+config-map, −old-hook, 3 updated`` by diffing two
``config.data.js`` snapshots — the same shape ``/system-map`` uses. The skill
calls this after regenerating the map, diffing the freshly-built working file
against the previously-committed one (``git show HEAD:…``), so the line
summarises exactly what moved in the config surface since the last run.

Every inventory dimension is flattened to one keyed entry — skills (universal /
fleet / repo-specific), hooks, helpers, matrix rows, conventions — so an added
skill, a removed hook, a re-wired matrix cell, or an edited description all
surface. Coverage counts are intentionally excluded (numeric churn, not a
structural change).

The generic diff/CLI scaffolding (shared with ``/system-map``'s twin) lives in
``skills/_lib/snapshot_diff.py`` — this module supplies only the
config-specific ``parse_config``/``format_line``.

Pure logic, no I/O in the diff functions, so ``tests/run_acceptance.py`` can
exercise them. The CLI at the bottom does the git read::

    E:/automation/fleet-config/.venv/Scripts/python.exe .claude/skills/config-map/whatchanged.py            # vs HEAD, default file
    E:/automation/fleet-config/.venv/Scripts/python.exe .claude/skills/config-map/whatchanged.py --ref main # vs another ref
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Dict, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "skills" / "_lib"))
import snapshot_diff  # noqa: E402


def parse_config(js_text: str) -> Dict[str, dict]:
    """Flatten a ``config.data.js`` body to ``{key: entry}`` across all dimensions.

    Slices the strict-JSON object out of ``window.CONFIG = { … };`` exactly the
    way ``run_acceptance.py`` does, then keys each inventory entry by a
    dimension-prefixed name so adds/removes/edits in any dimension are visible.
    """
    data = json.loads(js_text[js_text.index("{"): js_text.rindex("}") + 1])
    out: Dict[str, dict] = {}
    for s in data.get("skills_universal", []):
        out[f"skill:{s['nm']}"] = s
    for s in data.get("skills_fleet", []):
        out[f"fleet:{s['nm']}"] = s
    for r in data.get("skills_repo", []):
        for item in r.get("items", []):
            out[f"repo:{r['repo']}/{item}"] = {"repo": r["repo"], "item": item}
    for h in data.get("hooks", []):
        out[f"hook:{h['nm']}"] = h
    for h in data.get("hooks_helpers", []):
        out[f"helper:{h['nm']}"] = h
    for m in data.get("matrix", []):
        out[f"matrix:{m['cls']}"] = m
    for c in data.get("conventions", []):
        out[f"conv:{c['nm']}"] = c
    return out


def _short(key: str) -> str:
    """Human label for a flattened key — drop the dimension prefix and any repo path."""
    name = key.split(":", 1)[1] if ":" in key else key
    return name.rsplit("/", 1)[-1]


def diff_config(prev_js: str, cur_js: str) -> dict:
    """Return ``{'added': [...], 'removed': [...], 'updated': [...]}`` (sorted keys).

    * ``added`` / ``removed`` — entries present in only the new / only the old snapshot.
    * ``updated`` — entries in both whose content changed (a tweaked description,
      a re-wired matrix cell, a flipped scheduled/blocking flag, …).
    """
    return snapshot_diff.diff_entries(parse_config(prev_js), parse_config(cur_js))


def format_line(diff: dict) -> str:
    """One-line human summary, e.g. ``+config-map, −old-hook, 3 updated``.

    Added/removed entries are named (short label); updates are counted. An empty
    diff (a no-op week) reads ``no config changes``.
    """
    parts = [f"+{_short(k)}" for k in diff["added"]]
    parts += [f"{snapshot_diff.MINUS}{_short(k)}" for k in diff["removed"]]
    n = len(diff["updated"])
    if n:
        parts.append(f"{n} updated")
    return ", ".join(parts) if parts else "no config changes"


def summarize(prev_js: Optional[str], cur_js: str) -> str:
    """The Slack-post line; ``baseline`` when there is no prior snapshot."""
    return snapshot_diff.summarize(prev_js, cur_js, parse_config, format_line)


def main(argv: Optional[list[str]] = None) -> int:
    return snapshot_diff.run_cli(
        "Print the /config-map week-over-week change line.",
        "architecture/config.data.js",
        parse_config,
        format_line,
        argv,
    )


if __name__ == "__main__":
    sys.exit(main())
