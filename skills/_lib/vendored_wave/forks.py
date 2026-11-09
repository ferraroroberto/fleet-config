"""The `[vendored_forks]` table: copies an adopter deliberately keeps different from the scaffold's.

An adopter lists such a component beside its `[vendored]` entries, keyed by component name, with
`src` (the forked path), `reason` (what differs and why) and `upstream` (the issue offering the change
to the scaffold). project-scaffolding's `docs/components.md` owns that contract; this module reads it for
`vendored_drift.py scan`, which reports each well-formed entry as a `fork` and never as drift or as an
undeclared carrier. stdlib only.
"""

from __future__ import annotations

import tomllib
from typing import Callable, Dict, List


def parse_vendored_forks(toml_text: str) -> Dict[str, Dict[str, str]]:
    """A `.fleet.toml`'s optional `[vendored_forks]` table -> ``{component: {src, reason, upstream}}``.

    `{}` when the table is absent or empty; a non-table entry (a malformed hand-edit) is dropped, as
    `vendored_drift.parse_vendored_manifest` drops one, so it cannot hide a copy: with no declaration the
    copy surfaces as an undeclared carrier.
    """
    table = tomllib.loads(toml_text).get("vendored_forks")
    if not isinstance(table, dict):
        return {}
    return {name: dict(entry) for name, entry in table.items() if isinstance(entry, dict)}


def fork_rows(repo_name: str, declared: Dict[str, Dict[str, str]], manifest: Dict[str, Dict[str, str]],
              wanted: Callable[[str], bool], errors: List[Dict[str, str]]) -> List[Dict[str, object]]:
    """One `fork` row per well-formed entry of one repo; every other entry is an error, never a row.

    A component in both tables contradicts itself (a fork leaves `[vendored]`), so it is named as an error
    and its `[vendored]` entry keeps being drift-checked. An entry with no `src` or `reason` cannot say what
    differs or why, which is the whole content of the declaration. A missing `upstream` stays a row with
    `upstream: None`, so the smell is visible without hiding the fork. `wanted` is the scan's component filter.
    """
    rows: List[Dict[str, object]] = []
    for component, entry in sorted(declared.items()):
        if not wanted(component):
            continue
        if component in manifest:
            errors.append({"repo": repo_name, "component": component, "kind": "fork_conflict", "error":
                           f"component {component!r} is declared in both [vendored] and [vendored_forks]"})
        elif not (entry.get("src") and entry.get("reason")):
            errors.append({"repo": repo_name, "component": component, "kind": "fork_malformed",
                           "error": "[vendored_forks] entry missing src/reason"})
        else:
            rows.append({"repo": repo_name, "component": component, "state": "fork", "src": entry["src"],
                         "reason": entry["reason"], "upstream": entry.get("upstream") or None})
    return rows
