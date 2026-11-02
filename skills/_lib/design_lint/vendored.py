"""Byte-compare the app's `_vendored/` copies against project-scaffolding.

Whole-file sha256 for every component file, except `icons-sprite.html`, whose
own README sanctions per-app symbol trimming and so gets a per-symbol compare.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Dict, Optional

from .files import find_vendored_root, read_text


_SPRITE_SYMBOL_RE = re.compile(
    r'<symbol\b[^>]*\bid=["\']([^"\']+)["\'][^>]*>.*?</symbol>', re.S)


def _sprite_symbols(text: str) -> Dict[str, str]:
    return {m.group(1): m.group(0) for m in _SPRITE_SYMBOL_RE.finditer(text)}


def compare_icon_sprite(app_text: str, ref_text: str) -> Optional[str]:
    """Per-symbol compare for `icons-sprite.html`, not whole-file digest.

    The icons component's own README sanctions per-app symbol-set trimming
    (an app only inlines the Lucide glyphs it actually uses) — a whole-file
    byte-compare flags any trimmed sprite as FORKED even when every symbol it
    kept is byte-identical to the scaffold's (fleet-config#284 finding 4).
    Returns None (caller falls back to a whole-file digest) when either side
    has no `<symbol id="...">` elements to compare.

    Comparison is restricted to symbol ids present in BOTH files. Different
    apps vendor different Lucide subsets, so an app-only symbol (present in
    the app's sprite, absent from the reference) is expected, sanctioned
    trimming — not drift — and must not by itself force FORKED (fleet-
    config#389: local-llm-hub's sprite barely overlaps the reference set, so
    every symbol they *do* share was byte-identical yet the file still
    reported FORKED). A reference symbol missing from the app's file is the
    pre-existing "not (yet) vendored" case and is still reflected via the
    symbol-count comparison below.
    """
    app_syms = _sprite_symbols(app_text)
    ref_syms = _sprite_symbols(ref_text)
    if not app_syms or not ref_syms:
        return None
    common_ids = app_syms.keys() & ref_syms.keys()
    mismatched = [sid for sid in common_ids if app_syms[sid] != ref_syms[sid]]
    if mismatched:
        return "FORKED"
    if app_syms.keys() != ref_syms.keys():
        return "IDENTICAL (trimmed)"
    return "IDENTICAL"


# Where the scaffold keeps the canonical copies, per design surface: a static site
# vendors its own component set, never the PWA one.
REF_DIRS = {
    "web-app": "app/webapp/static/_vendored",
    "web-page": "app/site/static/_vendored",
}


def _digest(p: Path, lf: bool = False) -> str:
    """sha256 of the file; `lf` folds CRLF to LF first (text files only).

    A static site pins `eol=lf` in `.gitattributes` while the scaffold checkout
    keeps whatever `core.autocrlf` gives it, so identical git blobs differ on
    disk. Binary files (a NUL byte) are always hashed as they are.
    """
    data = p.read_bytes()
    if lf and b"\0" not in data:
        data = data.replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


def _compare_file(app_file: Path, ref_file: Path, relf: str, lf: bool) -> str:
    """One file's verdict; `UNREADABLE` when either side cannot be read."""
    try:
        if relf == "icons-sprite.html":
            sprite = compare_icon_sprite(read_text(app_file), read_text(ref_file))
            if sprite is not None:
                return sprite
        return "IDENTICAL" if _digest(app_file, lf) == _digest(ref_file, lf) else "FORKED"
    except OSError:
        return "UNREADABLE"


def _compare_component(comp: Path, app_comp: Path, lf: bool) -> Dict[str, object]:
    files: Dict[str, str] = {}
    for ref_file in sorted(comp.rglob("*")):
        if not ref_file.is_file():
            continue
        relf = ref_file.relative_to(comp).as_posix()
        app_file = app_comp / relf
        files[relf] = _compare_file(app_file, ref_file, relf, lf) if app_file.is_file() else "MISSING"
    # app-side extras are allowed (e.g. an app-specific sprite) — note them
    for app_file in sorted(app_comp.rglob("*")):
        if app_file.is_file():
            files.setdefault(app_file.relative_to(app_comp).as_posix(), "APP_ONLY")
    verdicts = set(files.values())
    if verdicts & {"FORKED", "MISSING"}:
        status = "FORKED"
    elif "UNREADABLE" in verdicts:
        status = "NOT_CONFIRMED"  # could not be compared: neither a pass nor a fork
    else:
        status = "IDENTICAL"
    return {"status": status, "files": files}


def vendored(root: Path, scaffold: Path, surface: str = "web-app") -> Dict[str, object]:
    """Byte-compare the app's _vendored component copies against the scaffold.

    `surface` picks the scaffold's reference set: the PWA components for a web
    app, the site components for a web page. A web page whose `_vendored` folder
    shares no component with the site set cannot be compared and reports an
    `error` (not confirmed) rather than an empty, clean-looking result.
    """
    app_dir = find_vendored_root(root)
    ref_dir = scaffold / REF_DIRS.get(surface, REF_DIRS["web-app"])
    if not ref_dir.is_dir():
        return {"error": f"scaffold _vendored not found at {ref_dir}"}
    result: Dict[str, object] = {"components": {}, "app_has_vendored_dir": app_dir is not None}
    comps: Dict[str, object] = result["components"]  # type: ignore[assignment]

    for comp in sorted(d for d in ref_dir.iterdir() if d.is_dir()):
        app_comp = (app_dir / comp.name) if app_dir else None
        if not app_comp or not app_comp.is_dir():
            comps[comp.name] = {"status": "NOT_ADOPTED", "files": {}}
            continue
        comps[comp.name] = _compare_component(comp, app_comp, lf=surface == "web-page")
    if (surface == "web-page" and app_dir is not None
            and all(c["status"] == "NOT_ADOPTED" for c in comps.values())):  # type: ignore[index]
        return {"error": f"{app_dir} shares no component with the scaffold's site set at {ref_dir}",
                "app_has_vendored_dir": True}
    return result
