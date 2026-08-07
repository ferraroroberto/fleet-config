"""design.md frontmatter -> a flat {'colors.canvas': '#ffffff'} dict.

A tolerant, indent-tracked, line-based parser for the fleet's known spec
format — deliberately no YAML dependency (skills/_lib is stdlib-only).
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple


_INLINE_MAP_RE = re.compile(r"^\{(.*)\}$", re.S)


def _strip_comment(value: str) -> str:
    """Drop a trailing `# comment` that is outside any quotes."""
    out: List[str] = []
    quote: Optional[str] = None
    for ch in value:
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
            out.append(ch)
            continue
        if ch == "#":
            break
        out.append(ch)
    return "".join(out).strip()


def _clean_value(value: str) -> str:
    v = _strip_comment(value).strip().rstrip(",")
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        v = v[1:-1]
    return v.strip()


def _split_inline_map(body: str) -> List[str]:
    """Split `k: v, k2: v2` on commas outside quotes/parens."""
    parts: List[str] = []
    buf: List[str] = []
    depth = 0
    quote: Optional[str] = None
    for ch in body:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
            buf.append(ch)
            continue
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    if buf:
        parts.append("".join(buf))
    return [p.strip() for p in parts if p.strip()]


def parse_spec(text: str) -> Dict[str, str]:
    """Flatten a design.md frontmatter into {'colors.canvas': '#ffffff', ...}.

    Tolerant, indent-tracked, line-based parser for the fleet's known format
    (no YAML dependency): flat leaves, nested groups (icons.size), and inline
    `{ k: v, ... }` maps. `{path.to.token}` references resolve afterwards.
    """
    lines = text.splitlines()
    # isolate the frontmatter block
    try:
        start = lines.index("---") + 1
        end = lines.index("---", start)
    except ValueError:
        start, end = 0, len(lines)

    flat: Dict[str, str] = {}
    stack: List[Tuple[int, str]] = []  # (indent, key)
    i = start
    while i < end:
        raw = lines[i]
        i += 1
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        stripped = raw.strip()
        m = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", stripped)
        if not m:
            continue  # continuation lines (folded description text)
        key, rest = m.group(1), m.group(2)
        while stack and stack[-1][0] >= indent:
            stack.pop()
        path = ".".join([k for _, k in stack] + [key])
        rest_clean = _strip_comment(rest).strip()
        if not rest_clean:
            stack.append((indent, key))  # group opener
            continue
        if rest_clean == ">":
            continue  # folded scalar (description) — skip its block
        im = _INLINE_MAP_RE.match(rest_clean)
        if im:
            for item in _split_inline_map(im.group(1)):
                km = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", item)
                if km:
                    flat[f"{path}.{km.group(1)}"] = _clean_value(km.group(2))
            continue
        flat[path] = _clean_value(rest)

    # resolve {path.to.token} references (bounded)
    for _ in range(4):
        changed = False
        for k, v in list(flat.items()):
            refs = re.findall(r"\{([A-Za-z0-9_.-]+)\}", v)
            for r in refs:
                if r in flat and "{" not in flat[r]:
                    v = v.replace("{" + r + "}", flat[r])
                    changed = True
            flat[k] = v
        if not changed:
            break
    return flat
