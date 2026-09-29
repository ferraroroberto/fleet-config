"""Design-rule parameters shared by the static and rendered legs (fleet-config#1062).

`design_lint` (`/design-sync`, reads source) and `design_review`
(`/design-review`, reads the rendered page) each check the same design rules,
so each rule parameter is defined once here and both legs import it. The rubric
TOML cannot import, so its copy of a parameter is pinned to these constants by
`tests/test_design_review.py` instead.
"""
from __future__ import annotations

# Selector fragments where `word-break: break-all` is right: opaque strings
# with no word boundaries. Rubric TYPE-04 `params.allow` must equal this.
BREAK_ALL_OK = "path|url|sha|hash|mono|code|token|uuid"

# Arrows and geometric shapes drawn as text in place of an icon. The static
# leg scans these as "glyph icons" (`markup._GLYPH_ICON_RE`); the kebab
# (U+22EE) is here because no emoji range covers it.
GLYPH_ICON_CLASS = "←-⇿■-◿⋮"
# Further icon-like characters (close, hamburger, misc arrows). The
# static leg's emoji scan already owns these, so counting them as glyph icons
# too would flag one site twice; the rendered leg has no separate emoji scan,
# so its glyph regex takes both classes. "×" is rendered-only: statically it
# would also flag prose such as "1280×900".
GLYPH_ICON_EMOJI_SCAN_CLASS = "⬀-⯿✖✕☰"
GLYPH_ICON_RENDERED_ONLY_CLASS = "×"

# The rendered leg's single character class (JS-compatible, `u` flag).
GLYPH_ICON_RENDERED_RE = (
    f"[{GLYPH_ICON_CLASS}{GLYPH_ICON_EMOJI_SCAN_CLASS}{GLYPH_ICON_RENDERED_ONLY_CLASS}]"
)
