"""The `system_map` acceptance check: the fleet list, the committed map and
ARCHITECTURE.md must agree, and the check says so plainly when it cannot judge.

Lives beside `architecture_guards.py` rather than in it: that file is over the
structure limit and may not grow.
"""

from __future__ import annotations

import re
from typing import Tuple

from acceptance.shared import REPO, _Checker, gate_projects_toml


# A card row is a markdown table row whose FIRST cell names the project, in
# bold, optionally after an icon: `| 🚀 **app-launcher** | ... |`. Anchoring to
# the first cell is the whole point — a bare `r in doc` substring test passed
# for `mcp-personal-onedrive` purely because the External-integrations table
# mentions it in prose in a *second* cell, so the guard could not detect the
# one thing it exists to detect: a mapped repo with no card of its own
# (fleet-config#681). Non-repo rows (`**Telegram**`, `**GPU**`) also match and are
# harmless: the set is only ever tested for membership of known repo names.
_CARD_ROW_RE = re.compile(r"^\|[^|]*?\*\*([A-Za-z0-9][A-Za-z0-9._-]*)\*\*[^|]*\|", re.MULTILINE)


def _repo_tables(toml: dict) -> set:
    return {
        name for name, tbl in toml.items()
        if name != "global" and isinstance(tbl, dict) and "cwd_prefix" in tbl
    }


def _architecture_card_repos(doc: str) -> set:
    """Project names carrying their own card row in ARCHITECTURE.md."""
    return set(_CARD_ROW_RE.findall(doc))


def _unmapped_state(missing: set, switch_is_local: bool) -> str:
    """How `system_map` reads repos that are declared, un-ignored and not on the map.

    `ok` when there are none. Otherwise `fail` where the machine-local list --
    the real scope switch -- was read; `unconfirmed` where only the tracked
    example stood in for it (a worktree or fresh clone), because the example
    cannot say which repos are switched off here.
    """
    if not missing:
        return "ok"
    return "fail" if switch_is_local else "unconfirmed"


def _system_map_coverage_check() -> Tuple[int, int, int]:
    """The system map must cover exactly the fleet, and the doc must agree.

    Guards the `/system-map` single source of truth (architecture/fleet.data.js)
    against drift, mechanically:
      1. every declared repo is on the map or switched off in an
         `architecture_ignore` list (the local file's or the example's);
      2. no map entry is a stale/typo'd repo that is not declared;
      3. every mapped repo also appears in ARCHITECTURE.md (data ↔ doc agree).
    The committed map is a snapshot of one machine's switch, so a repo
    flipped either way since the last regeneration is tolerated; a new repo
    left off the map is not. With no machine-local list (a worktree, a fresh
    clone) the switch is unknown, so an unmapped repo is reported as skipped
    (not confirmed) rather than failed. Returns (failures, total, skipped).
    """
    import json
    import tomllib

    check = _Checker()

    arch = REPO / "architecture"
    switch_is_local = (REPO / "hooks" / "projects.toml").is_file()
    declared: set = set()
    ignore: set = set()
    for path in {gate_projects_toml(), REPO / "hooks" / "projects.toml.example"}:
        toml = tomllib.loads(path.read_text(encoding="utf-8"))
        declared |= _repo_tables(toml)
        ignore |= set(toml.get("global", {}).get("architecture_ignore", []))

    # fleet.data.js holds `window.FLEET = { ...strict JSON... };` — slice the object out.
    raw = (arch / "fleet.data.js").read_text(encoding="utf-8")
    data = json.loads(raw[raw.index("{"): raw.rindex("}") + 1])
    mapped = {
        e.get("repo", e["nm"])
        for section in ("governance", "enabling", "web", "pipe")
        for e in data.get(section, [])
    }

    missing = declared - ignore - mapped
    stale = mapped - declared
    state = _unmapped_state(missing, switch_is_local)
    case = f"system_map: every fleet repo is on the map (missing: {sorted(missing) or 'none'})"
    if state == "unconfirmed":
        check.advisory(
            case + " -- NOT CONFIRMED", False,
            "no hooks/projects.toml here, so the example's architecture_ignore stands in for "
            "this machine's scope switch; re-run on the primary checkout to confirm",
        )
    else:
        check(case, state == "ok")
    check(f"system_map: no stale map entries (stale: {sorted(stale) or 'none'})", not stale)

    doc = (arch / "ARCHITECTURE.md").read_text(encoding="utf-8")
    carded = _architecture_card_repos(doc)
    doc_missing = sorted(r for r in mapped if r not in carded)
    check(f"system_map: every mapped repo has a card row in ARCHITECTURE.md (missing: {doc_missing or 'none'})", not doc_missing)

    # The matcher itself, against the shape that defeated the old substring
    # test: `mcp-personal-onedrive` named in prose in a *second* cell of the
    # External-integrations table, with no card row of its own anywhere.
    _prose_only = (
        "| Service | Reached from | For |\n"
        "|---|---|---|\n"
        "| 📁 **OneDrive** | apps: email-archiver, ghost-repo | file browsing |\n"
    )
    check("system_map: the card matcher ignores a repo named only in another cell's prose",
          "ghost-repo" not in _architecture_card_repos(_prose_only))
    check("system_map: the card matcher accepts a real icon+bold first-cell card row",
          "ghost-repo" in _architecture_card_repos("| 📁 **ghost-repo** | what it is | pipeline | — |\n"))
    check("system_map: the card matcher accepts a bold first cell with no icon",
          "ghost-repo" in _architecture_card_repos("| **ghost-repo** | what it is |\n"))

    # The worktree misfire: an unmapped repo must never hard-fail on the example alone.
    check("system_map: an unmapped repo fails where the local list was read",
          _unmapped_state({"ghost-repo"}, True) == "fail")
    check("system_map: an unmapped repo is not confirmed where only the example was read",
          _unmapped_state({"ghost-repo"}, False) == "unconfirmed")
    check("system_map: nothing unmapped is ok either way",
          _unmapped_state(set(), True) == _unmapped_state(set(), False) == "ok")

    return check.failures, check.total, check.skipped
