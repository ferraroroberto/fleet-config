"""The web-page `tokens` lens: does the site declare the contract's token names?

`design.web.md` fixes token names and roles, never values: the values are the
site's brand. So this lens reads the names from the spec's own token table
(section 2) and reports which the site's stylesheets declare, without grading
a single value. The one floor a value can be held to statically is
`--control`, the button and field height, against the spec's
`targets.control`.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

_TOKENS_HEADING_RE = re.compile(r"^##\s+\d+\.\s+Tokens\b")
_NAME_RE = re.compile(r"`(--[a-z][a-z0-9-]*)`")
# `--s1` … `--s9`: a numbered scale written as its two ends.
_RANGE_RE = re.compile(r"`--([a-z]+)(\d+)`\s*(?:…|\.\.\.)\s*`--\1(\d+)`")
_PX_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)px\s*$")


def contract_token_names(spec_text: str) -> List[str]:
    """Every token name in the first column of the spec's token table, ranges expanded."""
    lines = spec_text.splitlines()
    start = next((i for i, line in enumerate(lines) if _TOKENS_HEADING_RE.match(line)), None)
    if start is None:
        return []
    names: List[str] = []
    for line in lines[start + 1:]:
        if line.startswith("## "):
            break
        if not line.startswith("| `--"):
            continue
        first_cell = line.split("|")[1]
        for m in _RANGE_RE.finditer(first_cell):
            prefix, lo, hi = m.group(1), int(m.group(2)), int(m.group(3))
            names.extend(f"--{prefix}{n}" for n in range(lo, hi + 1))
        for name in _NAME_RE.findall(_RANGE_RE.sub("", first_cell)):
            names.append(name)
    return list(dict.fromkeys(names))


def _px(value: Optional[str]) -> Optional[float]:
    m = _PX_RE.match(value or "")
    return float(m.group(1)) if m else None


def web_tokens(spec_text: str, spec: Dict[str, str],
               app: Dict[str, Dict[str, Tuple[str, int]]], main_file: str) -> dict:
    """Required token names present / missing in the site, plus the `--control` floor."""
    required = contract_token_names(spec_text)
    declared = {**app.get("dark", {}), **app.get("light", {})}
    present = {name: {"value": declared[name][0], "line": declared[name][1]}
               for name in required if name in declared}
    missing = [name for name in required if name not in declared]
    floors: List[dict] = []
    floor = _px(spec.get("targets.control"))
    if floor is not None and "--control" in present:
        got = _px(present["--control"]["value"])
        if got is None:
            floors.append({"token": "--control", "status": "unmeasured",
                           "detail": f"value {present['--control']['value']!r} is not a px length"})
        else:
            floors.append({"token": "--control", "status": "PASS" if got >= floor else "FAIL",
                           "detail": f"{got:g}px against the {floor:g}px floor"})
    return {
        "surface": "web-page",
        "main_file": main_file,
        "required": required,
        "present": present,
        "missing": missing,
        "floors": floors,
    }
