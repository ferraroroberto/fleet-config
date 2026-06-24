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

Pure logic, no I/O in the diff functions, so ``tests/run_acceptance.py`` can
exercise them. The CLI at the bottom does the git read::

    py .claude/skills/config-map/whatchanged.py            # vs HEAD, default file
    py .claude/skills/config-map/whatchanged.py --ref main # vs another ref
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Dict, Optional

# U+2212 MINUS SIGN for removals (reads cleaner than a hyphen, which looks like a
# CLI flag) — matches the '+config-map, −old' example.
_MINUS = "−"


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
    prev = parse_config(prev_js)
    cur = parse_config(cur_js)
    added = sorted(set(cur) - set(prev))
    removed = sorted(set(prev) - set(cur))
    updated = sorted(
        k for k in set(cur) & set(prev)
        if json.dumps(cur[k], sort_keys=True) != json.dumps(prev[k], sort_keys=True)
    )
    return {"added": added, "removed": removed, "updated": updated}


def format_line(diff: dict) -> str:
    """One-line human summary, e.g. ``+config-map, −old-hook, 3 updated``.

    Added/removed entries are named (short label); updates are counted. An empty
    diff (a no-op week) reads ``no config changes``.
    """
    parts = [f"+{_short(k)}" for k in diff["added"]]
    parts += [f"{_MINUS}{_short(k)}" for k in diff["removed"]]
    n = len(diff["updated"])
    if n:
        parts.append(f"{n} updated")
    return ", ".join(parts) if parts else "no config changes"


def summarize(prev_js: Optional[str], cur_js: str) -> str:
    """The Slack-post line; ``baseline`` when there is no prior snapshot."""
    if not prev_js:
        return "baseline"
    return format_line(diff_config(prev_js, cur_js))


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
        description="Print the /config-map week-over-week change line."
    )
    ap.add_argument("--file", default="architecture/config.data.js",
                    help="working config.data.js (the current snapshot)")
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
