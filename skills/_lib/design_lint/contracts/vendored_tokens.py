"""Vendored-component token needs vs what the app defines (fleet-config#1290).

A vendored component is byte-identical across the fleet, but the tokens it
reads come from the adopting app. When the app never defines one, nothing
catches it until a gate goes red or the control renders wrong:
`icon-button.css` reads `var(--icon-inline)` with no fallback, so an app
without the token draws a zero-size glyph (home-automation#828), and its
`--hit-min` falls back to 44px silently (app-launcher#1417). The `vendored`
lens only byte-compares and the `tokens` lens only compares values the app
does define, so neither sees a token that is simply absent.

The token list is derived from the component's CSS (`var(--x)` reads), never
from its README, so the check cannot drift from the code. The README's
"Required design tokens" table is a cross-check: disagreement is noted in the
detail without changing the status, since the fix belongs upstream in
project-scaffolding, not in the adopting app. Only one direction is checked —
a token the CSS reads that the table omits. A table row the CSS never reads is
normal: a container's table also lists what the controls inside it need
(modal's lists the button and input tokens). A token the README calls a
per-context knob (`--icon-btn-box`) is exempt: leaving it unset is the
documented default.
"""
from __future__ import annotations

import re
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from ..css import strip_comments
from ..files import read_text, rel
from ._ctx import _ContractsCtx, _result

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))  # skills/_lib, as design_lint.files does
from vendored_drift import parse_vendored_manifest  # noqa: E402


_VAR_OPEN_RE = re.compile(r"\bvar\(\s*(--[A-Za-z0-9_-]+)\s*")
_TICKED_TOKEN_RE = re.compile(r"`(--[A-Za-z][A-Za-z0-9_-]*)`")
# A definition the app ships: a declaration in CSS or an inline style attribute,
# or a JS `style.setProperty('--x', ...)`.
_DECL_RE = re.compile(r"(?<![\w-])(--[A-Za-z0-9_-]+)\s*:")
_SET_PROPERTY_RE = re.compile(r"""setProperty\(\s*['"`](--[A-Za-z0-9_-]+)""")
_KNOB_RE = re.compile(r"\bknob\b", re.I)


@dataclass
class _Read:
    """One `var(--name[, fallback])` read; `inner` are the reads inside the fallback."""
    name: str
    pos: int
    fallback: Optional[str]
    inner: List["_Read"] = field(default_factory=list)


def _var_reads(text: str, lo: int = 0, hi: Optional[int] = None) -> List[_Read]:
    """Every top-level `var()` in `text[lo:hi]`, with nested fallbacks parsed recursively."""
    hi = len(text) if hi is None else hi
    reads: List[_Read] = []
    pos = lo
    while True:
        m = _VAR_OPEN_RE.search(text, pos, hi)
        if not m:
            return reads
        i = m.end()
        if i < hi and text[i] == ",":
            depth, j = 1, i + 1
            while j < hi and depth:
                if text[j] == "(":
                    depth += 1
                elif text[j] == ")":
                    depth -= 1
                j += 1
            close = j - 1 if depth == 0 else hi
            reads.append(_Read(m.group(1), m.start(), text[i + 1:close].strip(),
                               _var_reads(text, i + 1, close)))
            pos = close + 1
        else:
            reads.append(_Read(m.group(1), m.start(), None))
            pos = i + 1


def _readme_tokens(readme: str) -> Tuple[Set[str], Set[str]]:
    """`(table tokens, knobs)` from a component README.

    The table is the "Required design tokens" section's rows (every backticked
    `--x` in the first cell, so `--a` / `--b` rows count both; the `| --- |`
    separator holds none). A knob is a token named on a prose line that calls
    it a knob — table rows are excluded so a token whose *name* contains the
    word (`--toggle-knob`) is not mistaken for one.
    """
    table: Set[str] = set()
    knobs: Set[str] = set()
    in_section = False
    for line in readme.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            in_section = "required design tokens" in stripped.lower()
            continue
        if stripped.startswith("|"):
            if in_section:
                first = stripped.strip("|").split("|", 1)[0]
                table.update(_TICKED_TOKEN_RE.findall(first))
            continue
        if _KNOB_RE.search(stripped):
            knobs.update(_TICKED_TOKEN_RE.findall(stripped))
    return table, knobs


def _declared_components(root: Path) -> Tuple[Dict[str, Path], Optional[str]]:
    """`({name: app component dir}, error)` for each `[vendored]` entry that is a CSS component."""
    path = root / ".fleet.toml"
    if not path.is_file():
        return {}, None
    try:
        manifest = parse_vendored_manifest(read_text(path))
    except tomllib.TOMLDecodeError as exc:
        return {}, f"unreadable .fleet.toml: {exc}"
    comps: Dict[str, Path] = {}
    for name, entry in sorted(manifest.items()):
        dest = entry.get("dest") or entry.get("src")
        if not isinstance(dest, str):
            continue
        comp_dir = root / dest
        if comp_dir.is_dir() and any(comp_dir.rglob("*.css")):
            comps[name] = comp_dir
    return comps, None


def _all_names(reads: List[_Read]) -> Set[str]:
    """Every token a list of reads can touch, nested fallbacks included."""
    names: Set[str] = set()
    for r in reads:
        names.add(r.name)
        names |= _all_names(r.inner)
    return names


def _gaps(reads: List[_Read], text: str, where: str, defined: Set[str], knobs: Set[str],
          missing: Dict[str, str], fallback: Dict[str, str]) -> None:
    """Fill `missing` (token -> first no-fallback read site) and `fallback` (token -> the
    component's fallback) for the reads the app leaves undefined. A nested fallback is
    only read when its outer token is undefined, so it is walked only then."""
    for r in reads:
        if r.name in defined:
            continue
        if r.fallback is None:
            if r.name not in knobs:
                line = text.count("\n", 0, r.pos) + 1
                missing.setdefault(r.name, f"{where}:{line}")
            continue
        if r.name not in knobs:
            fallback.setdefault(r.name, r.fallback)
        _gaps(r.inner, text, where, defined, knobs, missing, fallback)


def _check_vendored_tokens(ctx: _ContractsCtx) -> List[dict]:
    comps, err = _declared_components(ctx.root)
    if err:
        return [_result("vendored-tokens", "FAIL", err)]
    if not comps:
        return [_result("vendored-tokens", "NA", "no CSS component declared in .fleet.toml [vendored]")]

    defined: Set[str] = set(_DECL_RE.findall(ctx.css_all)) | set(_DECL_RE.findall(ctx.markup_all))
    defined |= set(_SET_PROPERTY_RE.findall(ctx.markup_all))

    fails: List[str] = []
    warns: List[str] = []
    notes: List[str] = []
    evidence: Optional[str] = None
    for name, comp_dir in comps.items():
        readme_path = comp_dir / "README.md"
        table, knobs = _readme_tokens(read_text(readme_path)) if readme_path.is_file() else (set(), set())
        missing: Dict[str, str] = {}
        fallback: Dict[str, str] = {}
        read: Set[str] = set()
        own: Set[str] = set()
        for css_path in sorted(comp_dir.rglob("*.css")):
            text = strip_comments(read_text(css_path), "css")
            reads = _var_reads(text)
            read |= _all_names(reads)
            own.update(_DECL_RE.findall(text))
            _gaps(reads, text, rel(ctx.root, css_path), defined, knobs, missing, fallback)

        fallback = {t: v for t, v in fallback.items() if t not in missing}
        if missing:
            fails.append(f"{name} {', '.join(sorted(missing))}")
            evidence = evidence or missing[min(missing)]
        if fallback:
            warns.append(f"{name} " + ", ".join(f"{t} ({v})" for t, v in sorted(fallback.items())))
        if table:  # no table, nothing to cross-check against
            unlisted = sorted(read - table - knobs - own)
            if unlisted:
                notes.append(f"{name} reads {', '.join(unlisted)}")

    parts: List[str] = []
    if fails:
        parts.append("undefined, no fallback: " + "; ".join(fails))
    if warns:
        parts.append("undefined, component fallback used: " + "; ".join(warns))
    if not parts:
        parts.append(f"{len(comps)} declared component(s) — every token they read is defined")
    if notes:
        parts.append("not in the README token table (fix upstream in project-scaffolding): " + "; ".join(notes))
    status = "FAIL" if fails else "WARN" if warns else "PASS"
    return [_result("vendored-tokens", status, " | ".join(parts), evidence)]
