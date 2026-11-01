"""Narrow the surface-neutral lenses to what a web page can act on.

`vendored` lists every web-app component the scaffold ships; a site adopts
none of the PWA set, so only the components it does vendor are kept. A built
static site commits its build output next to its source, so `siblings` finds
every script twice; a duplicate whose sites all sit in byte-identical files is
that copy, not a second implementation, and is dropped.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Dict, List


def adopted_only(report: Dict[str, object]) -> Dict[str, object]:
    """The `vendored` report with `NOT_ADOPTED` components left out."""
    comps = report.get("components")
    if not isinstance(comps, dict):
        return report
    kept = {name: row for name, row in comps.items()
            if not (isinstance(row, dict) and row.get("status") == "NOT_ADOPTED")}
    return {**report, "components": kept}


def _digest(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return f"unreadable:{path}"


def without_build_copies(root: Path, rows: List[dict]) -> List[dict]:
    """`siblings` rows minus those whose every site is in a byte-identical file."""
    kept: List[dict] = []
    for row in rows:
        files = {site.rsplit(":", 1)[0] for site in row.get("sites", [])}
        if len(files) > 1 and len({_digest(root / f) for f in files}) == 1:
            continue
        kept.append(row)
    return kept
