"""Unit tests for design_lint's color-scheme-theme contract: `color-scheme` resolves from the chosen `data-theme`, not the OS.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/design_lint/test_color_scheme_contract.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import shutil
from pathlib import Path

import design_lint as dl  # noqa: E402
from _suite import _h, check  # noqa: E402
from throwaway_dir import throwaway_dir  # noqa: E402

_STATIC = "app/webapp/static"
# The vendored base.css rules, verbatim: the passing shape.
_BASE = (':root { color-scheme: light dark; }\n'
         ':root[data-theme="light"] { color-scheme: light; }\n'
         ':root[data-theme="dark"] { color-scheme: dark; }\n')


def color_scheme(css: str | None = _BASE, *, html: str = "<body><main class=\"app\">x</main></body>\n") -> dict:
    t = Path(throwaway_dir("dl-cs-"))
    try:
        files = {f"{_STATIC}/index.html": html}
        if css is not None:
            files[f"{_STATIC}/styles.css"] = css
        for name, body in files.items():
            path = t / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
        out = dl.contracts(t, sorted(t.rglob("*.css")), sorted(t.rglob("*.html")),
                           sorted(t.rglob("*.js")), {"icons.size.inline": "16px"})
        rows = [c for c in out if c["id"] == "color-scheme-theme"]
        check(len(rows) == 1, f"color-scheme-theme: exactly one row -- {rows}")
        return rows[0] if rows else {"status": None, "detail": "", "evidence": None}
    finally:
        shutil.rmtree(t, ignore_errors=True)


# ---- passing shapes ----
_ok = color_scheme()
check(_ok["status"] == "PASS", f"the vendored base.css shape -> PASS -- {_ok}")
check(_ok["evidence"] == f"{_STATIC}/styles.css:2", f"PASS evidence is the light override -- {_ok}")

_single = color_scheme(":root { color-scheme: light; }\n:root[data-theme=dark] { color-scheme: dark; }\n")
check(_single["status"] == "PASS", f"single-scheme base covers its own theme, unquoted attr value -- {_single}")

_desc = color_scheme(':root { color-scheme: light dark; }\n[data-theme="light"] select { color-scheme: light; }\n'
                     '[data-theme="dark"] select { color-scheme: dark; }\n')
check(_desc["status"] == "PASS", f"per-element rules keyed to data-theme count -- {_desc}")

_inline = color_scheme(css=None, html="<head><style>\n:root { color-scheme: light dark; }\n"
                       ':root[data-theme="light"] { color-scheme: light; }\n'
                       ':root[data-theme="dark"] { color-scheme: dark; }\n</style></head>\n')
check(_inline["status"] == "PASS", f"an inline <style> block is read -- {_inline}")

_comment = color_scheme(_BASE + "/* :root { color-scheme: light dark; } */\n")
check(_comment["status"] == "PASS", f"a commented-out rule is ignored -- {_comment}")

# ---- failing shapes ----
_os_only = color_scheme(":root { color-scheme: light dark; }\n")
check(_os_only["status"] == "FAIL", f"`light dark` with no overrides -> FAIL -- {_os_only}")
check(_os_only["evidence"] == f"{_STATIC}/styles.css:1", f"FAIL evidence is the declaration -- {_os_only}")
check('[data-theme="light"]' in _os_only["detail"] and '[data-theme="dark"]' in _os_only["detail"],
      f"detail names both missing rules -- {_os_only}")

_half = color_scheme(':root { color-scheme: light dark; }\n:root[data-theme="dark"] { color-scheme: dark; }\n')
check(_half["status"] == "FAIL" and 'color-scheme: light; }' in _half["detail"]
      and 'color-scheme: dark; }' not in _half["detail"],
      f"a missing override for one theme -> FAIL naming only that theme -- {_half}")

_wrong = color_scheme(':root { color-scheme: light dark; }\n:root[data-theme="light"] { color-scheme: dark; }\n'
                      ':root[data-theme="dark"] { color-scheme: dark; }\n')
check(_wrong["status"] == "FAIL", f"an override that sets the other scheme does not cover its theme -- {_wrong}")

_media = color_scheme(":root { color-scheme: light; }\n@media (prefers-color-scheme: dark) { :root { color-scheme: dark; } }\n")
check(_media["status"] == "FAIL", f"a dark rule gated on the OS media query follows the OS -> FAIL -- {_media}")

_both = color_scheme(":root { color-scheme: light dark; }\n:root[data-theme='dark'] { color-scheme: light dark; }\n")
check(_both["status"] == "FAIL", f"an override that keeps `light dark` -> FAIL -- {_both}")

# ---- not applicable ----
_na = color_scheme(":root { --canvas: #fff; }\n")
check(_na["status"] == "NA", f"no color-scheme declared -> NA -- {_na}")

_h.report_and_exit("test_color_scheme_contract")
