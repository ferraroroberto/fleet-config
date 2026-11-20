"""Unit tests for design_lint's dark control-fill contract: a control, chip or badge fill must not be darker than the dark card.

The fixtures are the two shapes the fleet shipped: a dark `--input-bg` that
pointed at the canvas, and chips and badges filled with a dark `--canvas-subtle`.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/design_lint/test_dark_fills_contract.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Dict

from _suite import _h, check  # noqa: E402

# isort: split
import design_lint as dl  # noqa: E402
from throwaway_dir import throwaway_dir  # noqa: E402

_STATIC = "app/webapp/static"
_IDS = ("dark-fill-token", "dark-fill-selector", "dark-track-fill")

# The fleet dark palette: canvas #0d1117, card #161b22, control fill #21262d, inset #010409.
_PALETTE = (':root { --bg: #ffffff; --card: #ffffff; --line-muted: #d0d7de; --canvas-subtle: #f6f8fa;\n'
            '        --input-bg: #f6f8fa; }\n'
            ':root[data-theme="dark"] { --bg: #0d1117; --card: #161b22; --line-muted: #21262d;\n'
            '        --canvas-subtle: #010409; }\n')


def lint(css: str) -> Dict[str, dict]:
    t = Path(throwaway_dir("dl-df-"))
    try:
        path = t / _STATIC / "styles.css"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(css, encoding="utf-8")
        out = dl.contracts(t, [path], [], [], {"icons.size.inline": "16px"})
        rows = {c["id"]: c for c in out if c["id"] in _IDS}
        check(set(rows) == set(_IDS), f"dark-fill: one row per id -- {sorted(rows)}")
        return rows
    finally:
        shutil.rmtree(t, ignore_errors=True)


def status(css: str, check_id: str) -> str:
    return lint(css)[check_id]["status"]


# ---- Rule 1: the token ----
_canvas = lint(_PALETTE.replace("--input-bg: #f6f8fa;", "--input-bg: var(--bg);")
               .replace("--canvas-subtle: #010409;", "--canvas-subtle: #010409; --input-bg: var(--bg);"))
check(_canvas["dark-fill-token"]["status"] == "FAIL",
      f"dark --input-bg = the canvas (#0d1117) < card (#161b22) -> FAIL -- {_canvas['dark-fill-token']}")
check("--input-bg" in _canvas["dark-fill-token"]["detail"] and "#0d1117" in _canvas["dark-fill-token"]["detail"],
      f"detail names the token and its resolved colour -- {_canvas['dark-fill-token']}")
check(_canvas["dark-fill-token"]["evidence"] == f"{_STATIC}/styles.css:4",
      f"evidence is the dark declaration's file:line -- {_canvas['dark-fill-token']}")

_fleet = _PALETTE.replace("--canvas-subtle: #010409;", "--canvas-subtle: #010409; --input-bg: var(--line-muted);")
check(status(_fleet, "dark-fill-token") == "PASS", "dark --input-bg = #21262d (via var chain) passes")

_card2 = _PALETTE.replace("--canvas-subtle: #010409;", "--canvas-subtle: #010409; --card-2: #0d1117;")
check(status(_card2, "dark-fill-token") == "FAIL", "dark --card-2 darker than the card fails too")

_light_only = (':root { --bg: #ffffff; --card: #ffffff; --input-bg: #000000; }\n'
               ':root[data-theme="dark"] { --bg: #0d1117; --card: #161b22; --input-bg: #21262d; }\n')
check(status(_light_only, "dark-fill-token") == "PASS", "only the dark value is judged, the light one is not")

_inherit = ':root { --card: #161b22; --input-bg: #010409; }\n:root[data-theme="dark"] { --bg: #0d1117; }\n'
check(status(_inherit, "dark-fill-token") == "FAIL", "a dark theme inherits a light :root value it does not override")

_media = ('@media (prefers-color-scheme: dark) { :root { --card: #161b22; --input-bg: #0d1117; } }\n')
check(status(_media, "dark-fill-token") == "FAIL", "a prefers-color-scheme dark block is the dark palette")

_equal = ':root { --card: #161b22; }\n:root[data-theme="dark"] { --input-bg: #161b22; }\n'
check(status(_equal, "dark-fill-token") == "PASS", "a fill equal to the card is the same surface, not a recess")

_alpha = (':root { --card: #161b22; }\n:root[data-theme="dark"] { --input-bg: rgba(255, 255, 255, 0.08); }\n')
check(status(_alpha, "dark-fill-token") == "PASS", "a translucent white fill composites lighter than the card")

_unknown = ':root { --card: #161b22; }\n:root[data-theme="dark"] { --input-bg: oklch(20% 0 0); }\n'
check(status(_unknown, "dark-fill-token") == "WARN",
      "an unresolvable fill is WARN, never folded into PASS")

_none = ':root { --card: #161b22; }\n:root[data-theme="dark"] { --bg: #0d1117; }\n'
check(status(_none, "dark-fill-token") == "NA", "no control fill token defined -> NA")

# ---- NA: no dark palette ----
_light = ':root { --card: #ffffff; --input-bg: #f6f8fa; }\n.chip { background: var(--input-bg); }\n'
check(all(r["status"] == "NA" for r in lint(_light).values()), "no dark theme -> every row NA")
_nocard = ':root { --bg: #fff; }\n:root[data-theme="dark"] { --bg: #0d1117; }\n'
check(all(r["status"] == "NA" for r in lint(_nocard).values()), "no resolvable dark --card -> every row NA")

# ---- Rule 2: the selector ----
_CHIP_BAD = _PALETTE + ".chip { padding: 0 12px; background: var(--canvas-subtle); border: 1px solid var(--line-muted); }\n"
_chip = lint(_CHIP_BAD)
check(_chip["dark-fill-selector"]["status"] == "FAIL",
      f".chip filled with a #010409 token over a #161b22 card -> FAIL -- {_chip['dark-fill-selector']}")
check(".chip" in _chip["dark-fill-selector"]["detail"] and "#010409" in _chip["dark-fill-selector"]["detail"],
      f"detail names the selector and the colour -- {_chip['dark-fill-selector']}")
check(_chip["dark-fill-selector"]["evidence"] == f"{_STATIC}/styles.css:5",
      f"evidence is the rule's file:line -- {_chip['dark-fill-selector']}")

for sel in (".badge", ".pill", ".button-surface", "button", "input", "select", ".sev, .badge"):
    check(status(_PALETTE + f"{sel} {{ background: var(--canvas-subtle); }}\n", "dark-fill-selector") == "FAIL",
          f"{sel} is a control selector")

_fixed = _PALETTE + ".chip { background: var(--line-muted); }\n"
check(status(_fixed, "dark-fill-selector") == "PASS", "the same chip filled with #21262d passes")

_literal = _PALETTE + ".badge { background-color: #010409; }\n"
check(status(_literal, "dark-fill-selector") == "FAIL", "a literal dark fill and `background-color` are judged")

_shorthand = _PALETTE + ".chip { background: var(--canvas-subtle) url(x.svg) no-repeat; }\n"
check(status(_shorthand, "dark-fill-selector") == "FAIL", "a background shorthand's colour is judged")

_mix = _PALETTE + ".chip { background: color-mix(in srgb, var(--canvas-subtle) 50%, transparent); }\n"
check(status(_mix, "dark-fill-selector") == "FAIL", "a color-mix tint of a dark token is judged")

_tint = _PALETTE.replace("--canvas-subtle: #010409;", "--canvas-subtle: #010409; --accent: #58a6ff;") \
    + ".chip { background: color-mix(in srgb, var(--accent) 16%, transparent); }\n"
check(status(_tint, "dark-fill-selector") == "PASS", "an accent tint composites lighter than the card")

_transparent = _PALETTE + ".button-ghost { background: transparent; }\n.chip { background: none; }\n"
check(status(_transparent, "dark-fill-selector") == "PASS", "a transparent control is not a recess")

_dark_override = _PALETTE + (".chip { background: var(--canvas-subtle); }\n"
                             ':root[data-theme="dark"] .chip { background: var(--line-muted); }\n')
check(status(_dark_override, "dark-fill-selector") == "PASS", "a later dark-scoped rule overrides the bare selector")

_light_scoped = _PALETTE + ':root[data-theme="light"] .chip { background: var(--canvas-subtle); }\n'
check(status(_light_scoped, "dark-fill-selector") == "NA", "a light-theme-only rule is not judged in dark")

_light_media = _PALETTE + "@media (prefers-color-scheme: light) { .chip { background: var(--canvas-subtle); } }\n"
check(status(_light_media, "dark-fill-selector") == "NA", "a light prefers-color-scheme rule is not judged in dark")

_dark_scoped = _PALETTE + '[data-theme="dark"] .chip { background: var(--canvas-subtle); }\n'
check(status(_dark_scoped, "dark-fill-selector") == "FAIL", "a dark-scoped rule is judged")

_hover = _PALETTE + ".chip:hover { background: var(--canvas-subtle); }\n"
check(status(_hover, "dark-fill-selector") == "NA", "a pseudo-class rule is not judged against the resting card")

_container = _PALETTE + ".chip-row { background: var(--canvas-subtle); }\n.card { background: var(--canvas-subtle); }\n"
check(status(_container, "dark-fill-selector") == "NA", "a container whose class only starts with chip, and a plain card, are not controls")

_unresolved = _PALETTE + ".chip { background: oklch(10% 0 0); }\n"
check(status(_unresolved, "dark-fill-selector") == "WARN", "an unresolvable control background is WARN, not PASS")

_local = _PALETTE + ".chip { --tone: var(--line-muted); background: color-mix(in srgb, var(--tone) 50%, transparent); }\n"
check(status(_local, "dark-fill-selector") == "PASS", "a custom property the rule declares itself is followed")

_mix_card = _PALETTE.replace("--canvas-subtle: #010409;", "--canvas-subtle: #010409; --muted: #8b949e;") \
    + ".chip { background: color-mix(in srgb, var(--muted) 16%, var(--card)); }\n"
check(status(_mix_card, "dark-fill-selector") == "PASS", "a tint mixed into the card blends to a lighter fill")
_mix_dark = _PALETTE + ".chip { background: color-mix(in srgb, var(--canvas-subtle) 50%, var(--card)); }\n"
check(status(_mix_dark, "dark-fill-selector") == "FAIL", "a mix with a dark token that ends up darker than the card is judged")

_ink =_PALETTE.replace("--canvas-subtle: #010409;", "--canvas-subtle: #010409; --ink: #e6edf3;") \
    + ".rec-wave-meter { background: color-mix(in srgb, currentColor 24%, transparent); }\n"
check(status(_ink, "dark-track-fill") == "PASS", "currentColor is the dark text token, a light tint over the card")

_scoped_card = _PALETTE.replace("--canvas-subtle: #010409;", "--canvas-subtle: #010409; --input-bg: var(--line-muted);") \
    + '[data-theme="dark"] .board-modes { --card: #30363d; }\n'
check(status(_scoped_card, "dark-fill-token") == "PASS",
      "a component redeclaring --card for its own subtree does not change the theme's card")

# ---- tracks: WARN, listed apart from controls ----
_track = lint(_PALETTE + ".budget-meter { background: var(--canvas-subtle); }\n"
              ".mix-track { background: var(--canvas-subtle); }\nprogress { background: var(--canvas-subtle); }\n")
check(_track["dark-track-fill"]["status"] == "WARN",
      f"a recessed track is a WARN -- {_track['dark-track-fill']}")
check(_track["dark-fill-selector"]["status"] == "NA", "a track is not counted as a control")
check(all(s in _track["dark-track-fill"]["detail"] for s in (".budget-meter", ".mix-track", "progress")),
      f"every track hit is listed -- {_track['dark-track-fill']}")
check(status(_PALETTE + ".mix-track { background: var(--line-muted); }\n", "dark-track-fill") == "PASS",
      "a track above the card passes")

_h.report_and_exit("test_dark_fills_contract")
