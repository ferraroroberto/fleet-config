"""Per-family tokenized/total declaration ratios (the `adoption` lens).

How much of the stylesheet actually goes through tokens, split by family
(color, font-size, radius, spacing), with the literal escapees that drag the
ratio down.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional

from .css import _ANY_DECL_RE, _COLOR_LITERAL_RE, strip_comments
from .files import read_text, rel


_COLOR_PROPS = ("color", "background", "background-color", "border",
                "border-color", "border-top", "border-bottom", "border-left",
                "border-right", "outline", "fill", "stroke")
_SPACING_PROPS = ("padding", "margin", "gap", "row-gap", "column-gap",
                  "padding-top", "padding-bottom", "padding-left", "padding-right",
                  "padding-inline", "padding-block",
                  "margin-top", "margin-bottom", "margin-left", "margin-right",
                  "margin-inline", "margin-block")


def _family_of(prop: str, value: str) -> Optional[str]:
    p = prop.lower()
    if p == "font-size":
        return "font-size"
    if p == "border-radius":
        return "radius"
    if p in _SPACING_PROPS:
        return "spacing"
    if p in _COLOR_PROPS:
        # only count declarations that actually carry a color
        if "var(" in value or _COLOR_LITERAL_RE.search(value):
            return "color"
        return None
    return None


_SPACING_EXEMPT = re.compile(r"^(0|auto|inherit|initial|unset|none)( (0|auto))*$")
_RADIUS_EXEMPT = re.compile(r"^(0|50%|inherit)$")


def adoption(root: Path, css_files: List[Path]) -> Dict[str, object]:
    fam: Dict[str, Dict[str, object]] = {
        f: {"tokenized": 0, "total": 0, "escapees": [], "literals": {}}
        for f in ("color", "font-size", "radius", "spacing")
    }
    for path in css_files:
        css = strip_comments(read_text(path), "css")
        for m in _ANY_DECL_RE.finditer(css):
            prop, value = m.group(1), m.group(2).strip()
            if prop.startswith("--"):
                continue
            family = _family_of(prop, value)
            if family is None:
                continue
            v = value.strip()
            if family == "spacing" and _SPACING_EXEMPT.fullmatch(v):
                continue
            if family == "radius" and _RADIUS_EXEMPT.fullmatch(v):
                continue
            if family == "color" and v.lower() in ("transparent", "currentcolor", "inherit", "none"):
                continue
            entry = fam[family]
            entry["total"] += 1
            if "var(--" in v:
                entry["tokenized"] += 1
            else:
                line = css[: m.start()].count("\n") + 1
                lits: Dict[str, int] = entry["literals"]  # type: ignore[assignment]
                lits[v] = lits.get(v, 0) + 1
                esc: List[dict] = entry["escapees"]  # type: ignore[assignment]
                if len(esc) < 40:
                    esc.append({"file": rel(root, path), "line": line,
                                "prop": prop, "value": v})
    for family, entry in fam.items():
        total = int(entry["total"])  # type: ignore[arg-type]
        tok = int(entry["tokenized"])  # type: ignore[arg-type]
        entry["ratio"] = round(tok / total, 3) if total else None
        entry["literals"] = dict(sorted(
            entry["literals"].items(), key=lambda kv: -kv[1])[:15])  # type: ignore[union-attr]
    return fam
