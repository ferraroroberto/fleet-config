"""A repo's declared wide views — `[design] wide_views` in its `.fleet.toml`.

design.md's Layout rule (fleet-config#1113): width follows the shape of a
view's content. A one-dimensional view keeps the 772px measure; a
two-dimensional one (board lanes, a many-field table, a tree with attribute
columns, calendar days) may span the full width. An app names those views:

    [design]
    wide_views = ["board", "table"]     # tab ids, as the design-review walk derives them

Both width checks read this one table: `design_review`'s LAYOUT-06 (rendered)
and `design_lint`'s `desktop-measure` (authored CSS), so a declaration means
the same thing to each. Absent file, table or key is the normal case — an
undeclared view keeps the measure. A malformed declaration is returned as an
error string, never guessed into a list: an unreadable file must not silently
turn every view into a measured one, nor a typo into an exemption.
"""
from __future__ import annotations

import tomllib
from pathlib import Path
from typing import List, Optional, Tuple


def load_wide_views(root: Optional[Path]) -> Tuple[List[str], Optional[str]]:
    """`(view ids, error)` from `<root>/.fleet.toml`; the error is a one-line reason or None."""
    if root is None:
        return [], None
    path = Path(root) / ".fleet.toml"
    if not path.is_file():
        return [], None
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8", errors="replace"))
    except tomllib.TOMLDecodeError as exc:
        return [], f"unparseable .fleet.toml: {exc}"
    design = data.get("design")
    if not isinstance(design, dict) or "wide_views" not in design:
        return [], None
    raw = design["wide_views"]
    if not isinstance(raw, list) or not all(isinstance(v, str) and v.strip() for v in raw):
        return [], "[design] wide_views must be a list of non-empty view ids"
    return [v.strip() for v in raw], None
