"""Unit tests for the pure logic in skills/_lib/ux_surface.py (fleet-config#195).

No live git — these exercise the `## UX surface` block parser, brace expansion,
glob→regex translation, and the diff-intersection that gates the design check.

Run: `py tests/test_ux_surface.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "_lib"))
import ux_surface as ux  # noqa: E402

_fails: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        _fails.append(msg)


# ---- block parsing ----

BLOCK = """\
# Project Instructions

Some intro.

## UX surface
- design spec applies: yes      # `no` for Streamlit spikes / non-web repos
- paths:
  - app/webapp/static/**/*.css
  - app/webapp/templates/**
  - app/webapp/static/**/*.{js,html}
- key views:
  - /          (home + bottom nav)
  - /settings

## Next section
- design spec applies: no   # must NOT leak in from the following section
"""

parsed = ux.parse_ux_surface_block(BLOCK)
check(parsed is not None, "block found")
assert parsed is not None
check(parsed["spec_applies"] is True, "spec applies parsed true")
check(parsed["paths"] == [
    "app/webapp/static/**/*.css",
    "app/webapp/templates/**",
    "app/webapp/static/**/*.{js,html}",
], "paths parsed in order")
check(parsed["key_views"] == ["/", "/settings"], "key views keep only the path token")

# absent block -> None
check(ux.parse_ux_surface_block("# Title\n\n## Other\n- x\n") is None, "absent block -> None")

# spec applies: no
no_block = "## UX surface\n- design spec applies: no\n- paths:\n  - app/x.css\n"
parsed_no = ux.parse_ux_surface_block(no_block)
assert parsed_no is not None
check(parsed_no["spec_applies"] is False, "spec applies parsed false")
check(parsed_no["paths"] == ["app/x.css"], "paths still parsed when spec applies no")

# the next-section guard: the trailing `design spec applies: no` must not win
check(parsed["spec_applies"] is True, "next-section spec value did not leak")

# a `## UX surface` heading *inside a fenced code block* (a documented template,
# as in the convention doc / READMEs) must be ignored, not read as a live block.
FENCED = """\
# Some CLAUDE.md that only *documents* the template

Copy-paste default:

```markdown
## UX surface
- design spec applies: yes
- paths:
  - app/x.css
```

That's the whole file — no live block.
"""
check(ux.parse_ux_surface_block(FENCED) is None, "fenced template heading is ignored")

# a heading with a descriptive suffix (as the canonical scaffold carries) still
# parses as a live block, so a de-fenced scaffold heading is not silently dropped.
SUFFIXED = """\
## UX surface — diff-keyed design-conformance gate
- design spec applies: yes
- paths:
  - app/webapp/static/**/*.css
"""
parsed_suffixed = ux.parse_ux_surface_block(SUFFIXED)
check(parsed_suffixed is not None, "suffixed heading parses")
assert parsed_suffixed is not None
check(parsed_suffixed["spec_applies"] is True, "suffixed heading: spec applies parsed")
check(parsed_suffixed["paths"] == ["app/webapp/static/**/*.css"], "suffixed heading: paths parsed")

# a colon-style suffix is also tolerated
check(ux.parse_ux_surface_block("## UX surface: home views\n- design spec applies: yes\n")
      is not None, "colon-suffixed heading parses")

# but a different word starting with the same prefix must NOT match
check(ux.parse_ux_surface_block("## UX surfaces\n- design spec applies: yes\n") is None,
      "'## UX surfaces' is not a UX surface block")

# a fenced *suffixed* template heading is still ignored, like the bare one
FENCED_SUFFIXED = """\
Copy-paste default:

```markdown
## UX surface — diff-keyed design-conformance gate
- design spec applies: yes
```
"""
check(ux.parse_ux_surface_block(FENCED_SUFFIXED) is None, "fenced suffixed heading is ignored")


# ---- brace expansion ----

check(ux.expand_braces("*.css") == ["*.css"], "no-brace passthrough")
check(ux.expand_braces("*.{js,html}") == ["*.js", "*.html"], "single brace group")
check(sorted(ux.expand_braces("a/{x,y}.{js,ts}")) ==
      ["a/x.js", "a/x.ts", "a/y.js", "a/y.ts"], "two brace groups cross-product")


# ---- glob matching ----

CSS = "app/webapp/static/**/*.css"
check(ux.matches_any("app/webapp/static/main.css", [CSS]), "** matches zero dirs")
check(ux.matches_any("app/webapp/static/css/theme.css", [CSS]), "** matches one dir")
check(ux.matches_any("app/webapp/static/a/b/c.css", [CSS]), "** matches deep dirs")
check(not ux.matches_any("app/webapp/static/main.js", [CSS]), "wrong extension excluded")
check(not ux.matches_any("docs/notes.css", [CSS]), "outside prefix excluded")

TPL = "app/webapp/templates/**"
check(ux.matches_any("app/webapp/templates/index.html", [TPL]), "trailing ** matches a file")
check(ux.matches_any("app/webapp/templates/partials/nav.html", [TPL]), "trailing ** matches nested")
check(not ux.matches_any("app/webapp/static/index.html", [TPL]), "trailing ** respects its prefix")

JS = "app/webapp/static/**/*.{js,html}"
check(ux.matches_any("app/webapp/static/app.js", [JS]), "brace glob matches .js")
check(ux.matches_any("app/webapp/static/views/home.html", [JS]), "brace glob matches .html")
check(not ux.matches_any("app/webapp/static/app.css", [JS]), "brace glob excludes .css")

# windows backslashes are normalized
check(ux.matches_any("app\\webapp\\static\\main.css", [CSS]), "backslash paths normalized")


# ---- diff intersection (touched_paths) ----

PATHS = [CSS, TPL, JS]
changed = [
    "app/server.py",                       # backend — not UX
    "app/webapp/static/theme.css",         # UX
    "README.md",                           # docs — not UX
    "app/webapp/templates/home.html",      # UX
    "tests/test_x.py",                     # tests — not UX
]
hit = ux.touched_paths(changed, PATHS)
check(hit == ["app/webapp/static/theme.css", "app/webapp/templates/home.html"],
      "touched_paths returns only UX-surface files, in order")
check(ux.touched_paths(["app/server.py", "README.md"], PATHS) == [],
      "code/docs-only diff touches no UX surface")


if _fails:
    print(f"FAILED {len(_fails)} check(s):")
    for f in _fails:
        print(f"  - {f}")
    raise SystemExit(1)
print("ux_surface: all pure-logic checks passed")
raise SystemExit(0)
