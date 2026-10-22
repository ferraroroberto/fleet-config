"""Unit tests for design_lint's token layer (fleet-config#277): spec frontmatter parsing, custom-prop extraction, value normalization, alias mapping and adoption ratios.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/design_lint/test_tokens.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import shutil
from pathlib import Path

from _suite import _h, check  # noqa: E402
import design_lint as dl  # noqa: E402

from throwaway_dir import throwaway_dir  # noqa: E402


# ---- spec frontmatter parsing ----

SPEC = """---
name: Fleet
colors:
  canvas:        "#ffffff"   # page background
  accent:        "#0969da"
  success:       "#1a7f37"
typography:
  body:       { fontFamily: "system-ui, sans-serif", fontSize: 1rem,    fontWeight: 400, lineHeight: 1.5 }
rounded:
  lg:   16px
components:
  switch:         { trackOn: "{colors.success}", width: 44px }
icons:
  size:
    inline:  16px
    title:   18px
---

## Prose

Ignored.
"""

spec = dl.parse_spec(SPEC)
check(spec.get("colors.canvas") == "#ffffff", "flat leaf parsed + comment stripped")
check(spec.get("typography.body.fontSize") == "1rem",
      "inline map flattened despite quoted comma in fontFamily")
check(spec.get("typography.body.fontFamily") == "system-ui, sans-serif",
      "quoted comma value survives the inline-map split")
check(spec.get("rounded.lg") == "16px", "scalar group leaf parsed")
check(spec.get("components.switch.trackOn") == "#1a7f37",
      "{colors.success} token ref resolved to the literal")
check(spec.get("icons.size.inline") == "16px", "two-level nesting (icons.size)")
check("name" in spec and spec["name"] == "Fleet", "top-level scalar kept")


# ---- CSS custom-prop extraction ----

CSS = """/* tokens — see @media (color-gamut: p3) below for the P3 twins. */
:root {
  --bg: #ffffff;   /* canvas */
  --accent: #0969da;
}
[data-theme="dark"] {
  --bg: #0d1117;
  --accent: #2f81f7;
}
@media (color-gamut: p3) {
  :root { --accent: oklch(0.52 0.18 256); }
}
@media (prefers-color-scheme: dark) {
  :root { --scheme-var: #111111; }
}
"""

props = dl.parse_custom_props(CSS, "styles.css")
check(props["light"].get("--bg", ("", 0))[0] == "#ffffff",
      "light :root parsed despite @media mention inside a comment (the real bug)")
check(props["dark"].get("--bg", ("", 0))[0] == "#0d1117", "dark theme block parsed")
check(props["light"].get("--accent", ("", 0))[0] == "#0969da",
      "P3 media twin did NOT clobber the sRGB light value")
check(props["dark"].get("--scheme-var", ("", 0))[0] == "#111111",
      "prefers-color-scheme dark media lands in dark")


# ---- value normalization ----

check(dl.normalize_value("#FFF") == "#ffffff", "#fff shorthand expands")
check(dl.normalize_value("  1REM ") == "1rem", "case + whitespace normalize")


# ---- alias mapping: match / drift / missing / unmapped ----

app = {
    "light": {"--bg": ("#ffffff", 2), "--accent": ("#123456", 3), "--house": ("#000", 4)},
    "dark": {"--bg": ("#0d1117", 7)},
}
spec_l = {"colors.canvas": "#ffffff", "colors.accent": "#0969da", "colors.fg": "#1f2328"}
spec_d = {"colors.canvas": "#0d1117", "colors.accent": "#2f81f7", "colors.fg": "#e6edf3"}
res = dl.map_tokens(spec_l, spec_d, app, "styles.css")
check(any(m["role"] == "colors.canvas" and m["theme"] == "light" for m in res["matched"]),
      "canvas light matches via the --bg alias")
check(any(m["role"] == "colors.canvas" and m["theme"] == "dark" for m in res["matched"]),
      "canvas dark matches")
drifts = {(d["role"], d["theme"], d["kind"]) for d in res["drift"]}
check(("colors.accent", "light", "value-drift") in drifts,
      "accent light value drift detected")
check(("colors.accent", "dark", "missing-theme-value") in drifts,
      "accent has no dark value -> missing-theme-value")
check(any(m["role"] == "colors.fg" for m in res["missing"]),
      "fg has no candidate var -> missing")
check("--house" in res["unmapped"], "unclaimed app var surfaces as unmapped")
# structural tokens defined only in :root are NOT dark-drift (inheritance)
app2 = {"light": {"--radius": ("16px", 1)}, "dark": {}}
res2 = dl.map_tokens({"rounded.lg": "16px"}, {"rounded.lg": "16px"}, app2, "s.css")
check(not res2["drift"], "root-only structural token inherits into dark, no drift")
# contrast-safe roles (#963): optional while unadopted, mapped and drift-checked once adopted
_cs_l = {"colors.accent-text": "#0550ae", "colors.control-border": "#818b98"}
_cs_d = {"colors.accent-text": "#58a6ff", "colors.control-border": "#6e7681"}
res3 = dl.map_tokens(_cs_l, _cs_d, {"light": {}, "dark": {}}, "s.css")
check(not res3["missing"], "unadopted contrast-safe roles are not missing findings")
res4 = dl.map_tokens(_cs_l, _cs_d, {"light": {"--accent-text": ("#0550ae", 1)},
                                    "dark": {"--accent-text": ("#0969da", 9)}}, "s.css")
check(any(m["role"] == "colors.accent-text" and m["theme"] == "light" for m in res4["matched"])
      and any(d["role"] == "colors.accent-text" and d["theme"] == "dark" for d in res4["drift"]),
      "an adopted --accent-text maps to its role and drifts when the dark value is wrong")
# new type roles (#964): --font-body-sm maps to typography.body-sm; unadopted roles are not missing
_ty = {"typography.body-sm.fontSize": "0.875rem", "typography.overline.fontSize": "0.75rem"}
res5 = dl.map_tokens(_ty, _ty, {"light": {"--font-body-sm": ("0.875rem", 1)}, "dark": {}}, "s.css")
check(any(m["role"] == "typography.body-sm.fontSize" for m in res5["matched"]) and not res5["missing"],
      "--font-body-sm maps to the body-sm role; an unadopted overline is not a missing finding")
# a derived value references the app's own alias of a role (#1087): `var(--muted)` is the
# spec's `var(--fg-muted)` when --muted is the app's fg-muted, so it matches, not drifts
_ns = {"colors.fg-muted": "#59636e", "colors.neutral-soft": "color-mix(in srgb, var(--fg-muted) 16%, transparent)"}
_ns_d = {"colors.fg-muted": "#9198a1", "colors.neutral-soft": "color-mix(in srgb, var(--fg-muted) 16%, transparent)"}
_ns_app = {"light": {"--muted": ("#59636e", 1), "--neutral-soft": ("color-mix(in srgb, var(--muted) 16%, transparent)", 2)},
           "dark": {"--muted": ("#9198a1", 5)}}
res6 = dl.map_tokens(_ns, _ns_d, _ns_app, "s.css")
check(("colors.neutral-soft", "light") in {(m["role"], m["theme"]) for m in res6["matched"]}
      and not [d for d in res6["drift"] if d["role"] == "colors.neutral-soft"],
      f"var(--muted) in a derived value is the app's alias of --fg-muted, not drift -- {res6['drift']}")
_ns_bad = {"light": {"--muted": ("#59636e", 1), "--neutral-soft": ("color-mix(in srgb, var(--ink) 16%, transparent)", 2)},
           "dark": {"--muted": ("#9198a1", 5)}}
res7 = dl.map_tokens(_ns, _ns_d, _ns_bad, "s.css")
check(any(d["role"] == "colors.neutral-soft" and d["kind"] == "value-drift" for d in res7["drift"]),
      "a derived value built on the wrong role still drifts")


# ---- adoption ratios ----

ADOPT_CSS = """
.a { color: var(--ink); background: #fff; border-radius: var(--radius); }
.b { font-size: 1rem; padding: 8px; margin: 0; }
.c { font-size: var(--font-body); border-radius: 50%; gap: var(--gap); }
.d { background: transparent; color: currentColor; }
"""
tree = Path(throwaway_dir("dl-adopt-"))
try:
    (tree / "s.css").write_text(ADOPT_CSS, encoding="utf-8")
    fam = dl.adoption(tree, [tree / "s.css"])
    check(fam["color"]["total"] == 2 and fam["color"]["tokenized"] == 1,
          "color: var counts tokenized, #fff escapee, transparent/currentColor exempt")
    check(fam["font-size"]["total"] == 2 and fam["font-size"]["tokenized"] == 1,
          "font-size: one tokenized one literal")
    check(fam["radius"]["total"] == 1 and fam["radius"]["tokenized"] == 1,
          "radius: 50% exempt, var counted")
    check(fam["spacing"]["total"] == 2 and fam["spacing"]["tokenized"] == 1,
          "spacing: margin 0 exempt; 8px escapee + var(--gap) tokenized")
    esc = fam["font-size"]["escapees"]
    check(esc and esc[0]["value"] == "1rem" and esc[0]["line"] == 3,
          "escapee carries value + line")
finally:
    shutil.rmtree(tree, ignore_errors=True)

_h.report_and_exit("test_tokens")
