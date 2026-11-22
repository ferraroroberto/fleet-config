"""INV-03's exception list: app-owned button shapes the scaffold decided to leave alone.

project-scaffolding's vendored button README sorts every bespoke `<button>` into three buckets; the third is
"no component covers it", and a shape that lives in one app stays app-owned. Nothing in that app can clear such
a control, so without a list INV-03 keeps naming it and the app's finding never closes.

An entry names a shape, the one app that owns it and the control keys the capture gives it (`button.<first class>`
or `button#<id>`). A control is exempt only when both its app and its key match, so the same class in a second
app stays divergent and reopens the decision (`reopened_by`). The table in docs/skills/design-review-core.md is the
readable record of this list; tests/design_checks/test_design_inventory.py fails when the two disagree.

stdlib only.
"""
from __future__ import annotations

from typing import Dict, List, NamedTuple, Optional, Tuple


class ExemptShape(NamedTuple):
    shape: str
    app: str
    keys: Tuple[str, ...]


EXEMPT_SHAPES: Tuple[ExemptShape, ...] = (
    ExemptShape("data row", "folder-explorer", ("button.tree-main", "button.list-row", "button.result-row")),
    ExemptShape("stat tile", "folder-explorer", ("button.stat",)),
    ExemptShape("scope pill", "folder-explorer", ("button#scopePill", "button.reference-pill")),
    ExemptShape("readout that opens a sheet", "app-launcher", ("button.cpu-reading", "button.usage-meter")),
    ExemptShape("combobox trigger with per-option notes", "app-launcher", ("button.model-combo-trigger",)),
    ExemptShape("jump-to-latest pill", "app-launcher", ("button#chatLatest",)),
    ExemptShape("hero record toggle", "voice-transcriber", ("button#recordBtn",)),
    ExemptShape("date quick-pick tile", "task-os", ("button.snooze-opt",)),
)


def exempt_shape(app: str, key: str) -> Optional[str]:
    """The listed shape this control is, or None when it is not exempt (a listed key in another app is not)."""
    for s in EXEMPT_SHAPES:
        if s.app == app and key in s.keys:
            return s.shape
    return None


def reopened_by(app: str, key: str) -> Optional[ExemptShape]:
    """The listing a bespoke control reopens: a listed key found in an app the list does not name."""
    for s in EXEMPT_SHAPES:
        if s.app != app and key in s.keys:
            return s
    return None


def exempt_rows(tally: Dict[Tuple[str, str], int]) -> List[dict]:
    """`{shape, app, count}` rows, sorted, from a `(shape, app) -> controls` tally."""
    return [{"shape": shape, "app": app, "count": n} for (shape, app), n in sorted(tally.items())]
