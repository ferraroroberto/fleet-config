"""Spec role -> app custom-property mapping (the `tokens` lens).

Owns the alias table that says which `--var` names an app may legitimately use
for each spec role, which roles are optional, and the per-theme drift/missing/
unmapped comparison the LLM half of /design-sync then reasons about.
"""
from __future__ import annotations

from typing import Dict, List, Tuple

from .css import normalize_value


# spec role → candidate app custom-property names (without the -- prefix).
ALIASES: Dict[str, List[str]] = {
    "colors.canvas": ["bg", "canvas", "background"],
    "colors.canvas-subtle": ["card-off", "canvas-subtle", "bg-subtle", "surface-subtle"],
    "colors.card": ["card", "surface"],
    "colors.border": ["line", "border"],
    "colors.border-muted": ["line-muted", "border-muted"],
    "colors.fg": ["ink", "fg", "text"],
    "colors.fg-muted": ["muted", "fg-muted", "text-muted"],
    "colors.accent": ["accent", "link", "primary"],
    "colors.accent-fg": ["accent-fg", "on-accent"],
    "colors.accent-soft": ["accent-soft"],
    "colors.accent-border-soft": ["accent-border-soft"],
    "colors.accent-border-strong": ["accent-border-strong"],
    "colors.success": ["on", "success", "ok"],
    "colors.danger": ["deficit", "danger", "error"],
    "colors.attention": ["attention", "warning"],
    "colors.tile-green": ["tile-green"],
    "colors.tile-blue": ["tile-blue"],
    "colors.tile-purple": ["tile-purple"],
    "colors.tile-orange": ["tile-orange"],
    "colors.tile-yellow": ["tile-yellow"],
    "rounded.sm": ["radius-sm"],
    "rounded.md": ["radius-md"],
    "rounded.lg": ["radius", "radius-lg"],
    "rounded.pill": ["radius-pill"],
    "rounded.nav": ["radius-nav"],
    "spacing.xs": ["space-xs"],
    "spacing.sm": ["space-sm"],
    "spacing.md": ["space-md"],
    "spacing.lg": ["space-lg"],
    "spacing.xl": ["space-xl"],
    "spacing.gutter": ["gap", "gutter"],
    "typography.heading-xl.fontSize": ["font-heading-xl"],
    "typography.heading-lg.fontSize": ["font-heading-lg"],
    "typography.body.fontSize": ["font-body"],
    "typography.label.fontSize": ["font-label"],
    "typography.caption.fontSize": ["font-caption"],
    "components.control.height": ["control-h"],
    "icons.size.inline": ["icon-inline"],
    "icons.size.title": ["icon-title"],
    "icons.size.feature": ["icon-feature"],
}

# tokens whose absence is *not* a finding (optional adoptions)
OPTIONAL_ROLES = {
    "rounded.sm",  # home-automation deliberately dropped it (#361)
    "icons.size.inline", "icons.size.title", "icons.size.feature",
    "colors.tile-green", "colors.tile-blue", "colors.tile-purple",
    "colors.tile-orange", "colors.tile-yellow",
    # accent derivatives — only apps that ship a tint tier define them (#296)
    "colors.accent-soft", "colors.accent-border-soft",
    "colors.accent-border-strong",
}


def map_tokens(
    spec_light: Dict[str, str],
    spec_dark: Dict[str, str],
    app: Dict[str, Dict[str, Tuple[str, int]]],
    fname: str,
) -> Dict[str, object]:
    matches: List[dict] = []
    drift: List[dict] = []
    missing: List[dict] = []
    claimed: set = set()
    for role, candidates in ALIASES.items():
        spec_vals = {"light": spec_light.get(role), "dark": spec_dark.get(role)}
        if spec_vals["light"] is None and spec_vals["dark"] is None:
            continue
        var = None
        for cand in candidates:
            if f"--{cand}" in app["light"] or f"--{cand}" in app["dark"]:
                var = f"--{cand}"
                break
        if var is None:
            if role not in OPTIONAL_ROLES:
                missing.append({"role": role, "candidates": candidates})
            continue
        claimed.add(var)
        for theme in ("light", "dark"):
            spec_v = spec_vals[theme]
            if spec_v is None:
                continue
            got = app[theme].get(var)
            if got is None:
                # dark themes inherit theme-invariant structural tokens from
                # :root (radii, spacing) — but a token whose spec value
                # DIFFERS per theme (colors) genuinely needs the dark
                # override, so its absence is a finding.
                if (theme == "dark" and var in app["light"]
                        and normalize_value(spec_vals["light"] or "")
                        == normalize_value(spec_vals["dark"] or "")):
                    continue
                drift.append({"role": role, "var": var, "theme": theme,
                              "app": None, "spec": spec_v,
                              "file": fname, "line": 0, "kind": "missing-theme-value"})
                continue
            app_v, line = got
            if normalize_value(app_v) != normalize_value(spec_v):
                drift.append({"role": role, "var": var, "theme": theme,
                              "app": app_v, "spec": spec_v,
                              "file": fname, "line": line, "kind": "value-drift"})
            else:
                matches.append({"role": role, "var": var, "theme": theme})
    unmapped = sorted(
        v for v in set(app["light"]) | set(app["dark"]) if v not in claimed
    )
    return {"matched": matches, "drift": drift, "missing": missing,
            "unmapped": unmapped}
