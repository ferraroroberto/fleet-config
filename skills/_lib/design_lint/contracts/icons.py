"""Icon contracts — sizes, the single icon set, and the PWA identity family.

One vendored Lucide sprite rather than emoji or hand-drawn glyphs, fixed sizes
on the spec's `icons.size` steps, and the installable app-icon family (Apple
180 + regular 192/512 + a *distinct* maskable 512 + favicon) wired into both
index.html and the manifest.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List

from ..css import _BLOCK_RE, strip_comments
from ..files import read_text, rel, repo_files
from ..markup import find_emoji_sites
from ._ctx import _ContractsCtx, _result


def _check_icon_sizes(ctx: _ContractsCtx) -> List[dict]:
    # 9. icon-size strays vs the spec's icons.size steps
    css_all, spec_light = ctx.css_all, ctx.spec_light
    allowed = set()
    for key, val in spec_light.items():
        if key.startswith("icons.size.") and val.endswith("px"):
            allowed.add(val)
    if not allowed:
        return [_result("icon-sizes", "NA", "spec defines no icons.size steps")]
    strays: Dict[str, int] = {}
    for bm in _BLOCK_RE.finditer(css_all):
        selector = bm.group(1)
        if "icon" not in selector:
            continue
        # `.header-icon-btn { width: 40px }` is a BUTTON box, not a glyph —
        # icon-named button/control selectors are not icon-size findings.
        if re.search(r"btn|button", selector):
            continue
        for dm in re.finditer(r"\b(?:width|height):\s*(\d+px)", bm.group(2)):
            if dm.group(1) not in allowed:
                strays[dm.group(1)] = strays.get(dm.group(1), 0) + 1
    if strays:
        top = ", ".join(f"{k}x{v}" for k, v in sorted(strays.items(), key=lambda kv: -kv[1])[:8])
        return [_result("icon-sizes", "WARN",
                         f"icon px sizes outside the icons.size steps ({', '.join(sorted(allowed))}): {top}")]
    return [_result("icon-sizes", "PASS", "all fixed icon sizes on the icons.size steps")]


def _check_icon_set(ctx: _ContractsCtx) -> List[dict]:
    # 13. icon-set — one set, vendored Lucide sprite, never hand-drawn/mixed
    #     (design.md Icons). Emoji glyphs anywhere in rendered UI-chrome text
    #     (markup text nodes or JS string literals) are the anti-pattern; the
    #     lucide-sprite adoption signal is the vendored `icons/` component
    #     (fleet-config#284, app-launcher#355/#368).
    vendored_root = ctx.vendored_root
    lucide_adopted = (vendored_root is not None
                       and (vendored_root / "icons").is_dir())
    emoji_sites = find_emoji_sites(ctx.root, ctx.html_files, ctx.js_files)
    if emoji_sites and not lucide_adopted:
        return [_result("icon-set", "FAIL",
                         f"icon-set: emoji-glyphs ({len(emoji_sites)} site(s)) / "
                         "lucide-sprite: NOT_ADOPTED (design.md Icons — one set, never "
                         "hand-drawn/mixed): " + ", ".join(emoji_sites[:8]))]
    if emoji_sites and lucide_adopted:
        return [_result("icon-set", "WARN",
                         f"icon-set: emoji-glyphs ({len(emoji_sites)} site(s)) alongside "
                         "an adopted lucide-sprite — mixed icon set: " + ", ".join(emoji_sites[:8]))]
    if lucide_adopted:
        return [_result("icon-set", "PASS",
                         "icon-set: lucide-sprite adopted, no emoji glyphs in rendered text")]
    return [_result("icon-set", "NA", "no emoji glyphs and no vendored icons/ component found")]


def _check_app_icon_family(ctx: _ContractsCtx) -> List[dict]:
    """Check the installable PWA identity family from design.md's app-icon map."""
    required = {
        key: ctx.spec_light.get(f"app-icon.{key}", "")
        for key in ("apple", "regular-small", "regular-large", "maskable", "favicon")
    }
    generator = ctx.spec_light.get("app-icon.generator", "")
    if not generator or not all(required.values()):
        return [_result("app-icon-family", "NA", "spec defines no complete app-icon contract")]
    if not ctx.index_files:
        return [_result("app-icon-family", "NA", "no index.html found")]

    manifest_hrefs: List[str] = []
    missing_manifest_link: List[str] = []
    missing_apple_link: List[str] = []
    missing_favicon_link: List[str] = []
    for path in ctx.index_files:
        text = strip_comments(read_text(path), "html")
        path_manifest_hrefs: List[str] = []
        path_apple_linked = False
        path_favicon_linked = False
        for tag in re.findall(r"<link\b[^>]*>", text, re.I):
            rel_m = re.search(r"\brel=[\"']([^\"']+)[\"']", tag, re.I)
            href_m = re.search(r"\bhref=[\"']([^\"']+)[\"']", tag, re.I)
            rel_value = rel_m.group(1).lower().split() if rel_m else []
            href = href_m.group(1) if href_m else ""
            if "manifest" in rel_value and href:
                path_manifest_hrefs.append(href)
            if "apple-touch-icon" in rel_value and required["apple"] in href:
                path_apple_linked = True
            if "icon" in rel_value and required["favicon"] in href:
                path_favicon_linked = True
        manifest_hrefs.extend(path_manifest_hrefs)
        if not path_manifest_hrefs:
            missing_manifest_link.append(rel(ctx.root, path))
        if not path_apple_linked:
            missing_apple_link.append(rel(ctx.root, path))
        if not path_favicon_linked:
            missing_favicon_link.append(rel(ctx.root, path))

    problems: List[str] = []
    if missing_manifest_link:
        problems.append("no rel=manifest link: " + ", ".join(missing_manifest_link))
    if missing_apple_link:
        problems.append(f"no {required['apple']} apple-touch-icon link: "
                        + ", ".join(missing_apple_link))
    if missing_favicon_link:
        problems.append(f"no {required['favicon']} favicon link: "
                        + ", ".join(missing_favicon_link))

    asset_files = repo_files(ctx.root, (".png", ".ico", ".svg"))
    asset_names = {path.name for path in asset_files}
    missing_assets = [name for name in required.values() if name not in asset_names]
    if missing_assets:
        problems.append("missing canonical asset(s): " + ", ".join(missing_assets))

    manifest_names = {Path(href.split("?", 1)[0]).name for href in manifest_hrefs}
    source_files = repo_files(ctx.root, (".webmanifest", ".json", ".py"))
    manifest_parts: List[str] = []
    for path in source_files:
        text = read_text(path)
        if (path.name in manifest_names
                or any(name and name in text for name in manifest_names)):
            manifest_parts.append(text)
    manifest_blob = "\n".join(manifest_parts)
    purpose_values = [
        match.group(1).strip().lower().split()
        for match in re.finditer(
            r"[\"']purpose[\"']\s*:\s*[\"']([^\"']+)[\"']", manifest_blob, re.I
        )
    ]
    if any("any" in value and "maskable" in value for value in purpose_values):
        problems.append("one manifest icon combines purpose 'any maskable'")
    if not any(value == ["any"] for value in purpose_values):
        problems.append("manifest has no distinct purpose 'any' entry")
    if not any(value == ["maskable"] for value in purpose_values):
        problems.append("manifest has no distinct purpose 'maskable' entry")
    for key in ("regular-small", "regular-large", "maskable"):
        if required[key] not in manifest_blob:
            problems.append(f"manifest does not reference {required[key]}")

    generator_adopted = False
    for path in repo_files(ctx.root, (".py",)):
        if "scripts" not in path.parts:
            continue
        text = read_text(path)
        if generator in text and re.search(r"\brender_set\s*\(", text):
            generator_adopted = True
            break
    if not generator_adopted:
        problems.append(f"shared {generator}.render_set generator not adopted")

    if problems:
        return [_result("app-icon-family", "FAIL", " | ".join(problems),
                        rel(ctx.root, ctx.index_files[0]))]
    return [_result(
        "app-icon-family", "PASS",
        "canonical brand_gen family: Apple 180 + regular 192/512 + distinct "
        "maskable 512 + favicon; index and manifest wiring present",
        rel(ctx.root, ctx.index_files[0]),
    )]
