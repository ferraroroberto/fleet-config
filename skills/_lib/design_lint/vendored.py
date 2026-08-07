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


def vendored(root: Path, scaffold: Path) -> Dict[str, object]:
    """Byte-compare the app's _vendored component copies against the scaffold."""
    app_dir = find_vendored_root(root)
    ref_dir = scaffold / "app/webapp/static/_vendored"
    if not ref_dir.is_dir():
        return {"error": f"scaffold _vendored not found at {ref_dir}"}
    result: Dict[str, object] = {"components": {}, "app_has_vendored_dir": app_dir is not None}
    comps: Dict[str, object] = result["components"]  # type: ignore[assignment]

    def digest(p: Path) -> str:
        return hashlib.sha256(p.read_bytes()).hexdigest()

    for comp in sorted(d for d in ref_dir.iterdir() if d.is_dir()):
        app_comp = (app_dir / comp.name) if app_dir else None
        if not app_comp or not app_comp.is_dir():
            comps[comp.name] = {"status": "NOT_ADOPTED", "files": {}}
            continue
        files: Dict[str, str] = {}
        forked = False
        for ref_file in sorted(comp.rglob("*")):
            if not ref_file.is_file():
                continue
            relf = ref_file.relative_to(comp).as_posix()
            app_file = app_comp / relf
            if not app_file.is_file():
                files[relf] = "MISSING"
                forked = True
                continue
            sprite_status = (compare_icon_sprite(read_text(app_file), read_text(ref_file))
                              if relf == "icons-sprite.html" else None)
            if sprite_status is not None:
                files[relf] = sprite_status
                forked = forked or sprite_status == "FORKED"
            elif digest(app_file) == digest(ref_file):
                files[relf] = "IDENTICAL"
            else:
                files[relf] = "FORKED"
                forked = True
        # app-side extras are allowed (e.g. an app-specific sprite) — note them
        for app_file in sorted(app_comp.rglob("*")):
            if app_file.is_file():
                relf = app_file.relative_to(app_comp).as_posix()
                files.setdefault(relf, "APP_ONLY")
        comps[comp.name] = {"status": "FORKED" if forked else "IDENTICAL", "files": files}
    return result
