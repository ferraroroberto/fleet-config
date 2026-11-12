"""Unit tests for the pure logic in skills/_lib/ux_surface.py (fleet-config#195).

No live git — these exercise the `## UX surface` block parser, brace expansion,
glob→regex translation, and the diff-intersection that gates the design check.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/web_review/test_ux_surface.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "skills" / "_lib"))
import ux_surface as ux  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check


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

# an annotated answer reads by its leading yes/no word (#1087): facilitation-suite's block
# as it stood before its #135 read SPEC_APPLIES=no, and every UI PR there skipped the gate
FS_ANNOTATED = """\
## UX surface
*The design-conformance gate the `/issue-{start,finish,yolo}` skills read (convention: `project-scaffolding#83`).*

- design spec applies: yes (the app and the presenter; the stage follows the session theme)
- paths:
  - app/webapp/static/**/*.css
  - app/webapp/static/**/*.{js,html}
- key views:
  - /           (Sessions · Plan · Groups · Results tabs, Settings behind the header gear)
  - /presenter  (the live cockpit)
"""
parsed_fs = ux.parse_ux_surface_block(FS_ANNOTATED)
check(parsed_fs is not None and parsed_fs["spec_applies"] is True and parsed_fs["key_views"] == ["/", "/presenter"],
      f"an annotated `yes (...)` answer applies the spec -- {parsed_fs}")
for _ans, _want in (("yes, app only", True), ("Yes.", True), ("true", True), ("no (a Streamlit spike)", False),
                    ("no, yes later", False), ("yesterday", False), ("maybe", False), ("", False)):
    _got = ux.parse_ux_surface_block(f"## UX surface\n- design spec applies: {_ans}\n")["spec_applies"]
    check(_got is _want, f"`design spec applies: {_ans}` reads {_want} -- got {_got}")
with tempfile.TemporaryDirectory() as _tmp:
    (Path(_tmp) / "CLAUDE.md").write_text(FS_ANNOTATED, encoding="utf-8")
    _buf = io.StringIO()
    with contextlib.redirect_stdout(_buf):
        ux.cmd_applies(Path(_tmp))
    check("SPEC_APPLIES=yes" in _buf.getvalue().splitlines(),
          f"`ux_surface.py applies` on the annotated block prints SPEC_APPLIES=yes -- {_buf.getvalue()!r}")


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


# ---- diff intersection (ui_paths) ----

PATHS = [CSS, TPL, JS]
changed = [
    "app/server.py",                       # backend — not UX
    "app/webapp/static/theme.css",         # UX
    "README.md",                           # docs — not UX
    "app/webapp/templates/home.html",      # UX
    "tests/test_x.py",                     # tests — not UX
]
hit = ux.ui_paths(changed, PATHS)
check(hit == ["app/webapp/static/theme.css", "app/webapp/templates/home.html"],
      "ui_paths returns only UX-surface files, in order")
check(ux.ui_paths(["app/server.py", "README.md"], PATHS) == [],
      "code/docs-only diff touches no UX surface")


# ---- ui_paths: what renders counts even when the block names only CSS ----

CSS_ONLY = ["app_web/static/**/*.css"]
check(ux.ui_paths(["app_web/static/machines.js"], CSS_ONLY) == ["app_web/static/machines.js"],
      "view JS under static/ counts as UI though the block declares only CSS")
check(ux.ui_paths(["app/templates/home.html", "app/templates/partials/row.j2"], CSS_ONLY)
      == ["app/templates/home.html", "app/templates/partials/row.j2"],
      "templates count as UI")
check(ux.ui_paths(["app/pages/2_Settings.py", "streamlit_app.py"], CSS_ONLY)
      == ["app/pages/2_Settings.py", "streamlit_app.py"],
      "Streamlit pages count as UI")
check(ux.ui_paths(["tests/e2e/_fixtures/layout/sheet.html", "docs/demo.html", "app/server.py"], CSS_ONLY) == [],
      "test fixtures, docs and backend code are not UI")
check(ux.ui_paths(["tests/e2e/_fixtures/page.html"], ["tests/e2e/_fixtures/**"])
      == ["tests/e2e/_fixtures/page.html"],
      "a declared glob always counts, even under tests/")

# ---- ui_paths: the mobile surface (installed-PWA shell, native iOS/Android views) ----

_PWA_SHELL = ["app/webapp/static/manifest.webmanifest", "app/webapp/static/icon-180.png",
              "app/webapp/static/apple-touch-icon.png", "app_web/static/manifest.json"]
check(ux.ui_paths(_PWA_SHELL, CSS_ONLY) == _PWA_SHELL,
      "the PWA shell (manifest and home-screen icons) counts as UI")
_NATIVE = ["ios/App/Views/HomeView.swift", "ios/App/Base.lproj/Main.storyboard",
           "android/app/src/main/res/layout/home.xml", "android/app/src/main/java/app/Home.kt"]
check(ux.ui_paths(_NATIVE, CSS_ONLY) == _NATIVE,
      "native iOS and Android views count as UI")
check(ux.ui_paths(["mac/worker/Sources/Worker/main.swift", "app/webapp/static/logo.png",
                   "custom_components/x/manifest.json", "tests/fixtures/manifest.webmanifest"], CSS_ONLY) == [],
      "a headless Swift worker, a non-icon image, a non-static manifest.json and a test fixture are not UI")


# ---- cmd_check: a diff that FAILED is `unknown`, never `no` (fleet-config#681) ----
# ux_surface used to carry a private `_changed_files` that swallowed git's
# non-zero exit and returned `[]`, printing the same `TOUCHED=no` a genuinely
# clean diff prints — so /issue-finish skipped the design gate on the strength
# of a probe that never ran.


def _run_check(changed_stub):
    repo = Path(tempfile.mkdtemp(prefix="test_ux_surface_"))
    (repo / "CLAUDE.md").write_text(BLOCK, encoding="utf-8")
    orig_changed, orig_base = ux.git_run.changed_files, ux._default_base
    ux.git_run.changed_files = lambda *a, **k: changed_stub
    ux._default_base = lambda *_a, **_k: "origin/main"
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ux.cmd_check(repo, None)
        return buf.getvalue()
    finally:
        ux.git_run.changed_files, ux._default_base = orig_changed, orig_base
        import shutil
        shutil.rmtree(repo, ignore_errors=True)


_out = _run_check(None)
check("TOUCHED=unknown" in _out,
      f"cmd_check: a failed diff reports TOUCHED=unknown, not `no` — got {_out.splitlines()!r}")
check(any(ln.startswith("KEY_VIEWS=") and len(ln) > len("KEY_VIEWS=") for ln in _out.splitlines()),
      "cmd_check: the unknown path still emits the KEY_VIEWS the gate needs to check every view")
check("TOUCHED=no" in _run_check([]),
      "cmd_check: a genuinely clean diff still reports TOUCHED=no — `unknown` did not swallow it")
check("TOUCHED=yes" in _run_check(["app/webapp/templates/home.html"]),
      "cmd_check: a real UX hit still reports TOUCHED=yes")
check("TOUCHED=yes" in _run_check(["app/static/pages.mjs"]),
      "cmd_check: view JS off the declared paths still reports TOUCHED=yes")


# ---- spec kind: web app vs web page ----

_wp = ux.parse_ux_surface_block("## UX surface\n- design spec applies: web-page  # a public site\n")
check(_wp["spec_applies"] is True and _wp["kind"] == ux.WEB_PAGE,
      f"`web-page` answer: the gate applies and the kind is web-page -- {_wp!r}")
check(ux.parse_ux_surface_block(BLOCK)["kind"] == ux.WEB_APP, "`yes` answer: kind is web-app")
check(ux.parse_ux_surface_block("## UX surface\n- design spec applies: no\n")["kind"] is None,
      "`no` answer: no kind of its own")
check(ux.spec_kind(_wp, None) == ux.WEB_PAGE, "spec_kind: the block's web-page answer wins")
check(ux.spec_kind(None, "static-site") == ux.WEB_PAGE,
      "spec_kind: shape = static-site marks a web page with no block")
check(ux.spec_kind({"kind": None}, "static-site") == ux.WEB_PAGE,
      "spec_kind: a static site whose gate is off is still a web page, never linted as a PWA")
check(ux.spec_kind(None, "webapp-tray") == ux.WEB_APP and ux.spec_kind(None, None) == ux.WEB_APP,
      "spec_kind: every other repo stays web-app, as before")


def _applies_out(claude_md: str, fleet: str) -> list:
    repo = Path(tempfile.mkdtemp(prefix="test_ux_kind_"))
    try:
        (repo / "CLAUDE.md").write_text(claude_md, encoding="utf-8")
        if fleet:
            (repo / ".fleet.toml").write_text(fleet, encoding="utf-8")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ux.cmd_applies(repo)
        return buf.getvalue().splitlines()
    finally:
        import shutil
        shutil.rmtree(repo, ignore_errors=True)


_site = _applies_out("## UX surface\n- design spec applies: no\n", 'shape = "static-site"\n')
check("SPEC_APPLIES=no" in _site and "SPEC_KIND=web-page" in _site,
      f"applies: a static site with its gate off prints SPEC_APPLIES=no and SPEC_KIND=web-page -- {_site!r}")
_app = _applies_out(BLOCK, 'shape = "webapp-tray"\n')
check("SPEC_APPLIES=yes" in _app and "SPEC_KIND=web-app" in _app,
      f"applies: a web app prints SPEC_APPLIES=yes and SPEC_KIND=web-app -- {_app!r}")

# ---- trigger(): the one decision cmd_check, proof and merge_gate share ----


def _trigger(block_text, diff_files, **kw):
    repo = Path(tempfile.mkdtemp(prefix="test_ux_trigger_"))
    if block_text is not None:
        (repo / "CLAUDE.md").write_text(block_text, encoding="utf-8")
    orig_changed, orig_base = ux.git_run.changed_files, ux._default_base
    ux.git_run.changed_files = lambda *a, **k: diff_files
    ux._default_base = lambda *_a, **_k: "origin/main"
    try:
        return ux.trigger(repo, **kw)
    finally:
        ux.git_run.changed_files, ux._default_base = orig_changed, orig_base
        import shutil
        shutil.rmtree(repo, ignore_errors=True)


_t = _trigger(BLOCK, ["app/webapp/static/a.css", "README.md"])
check(_t.applies and _t.touched == "yes" and _t.matched == ["app/webapp/static/a.css"] and _t.key_views,
      f"trigger: a touched surface is yes with its matches and key views -- {_t!r}")
_t = _trigger(BLOCK, ["README.md"])
check(_t.applies and _t.touched == "no" and _t.matched == [], f"trigger: an untouched surface is no -- {_t!r}")
_t = _trigger(BLOCK, None)
check(_t.applies and _t.touched == "unknown", f"trigger: a failed diff is unknown, never no -- {_t!r}")
_t = _trigger(BLOCK, None, changed=["app/webapp/static/a.css"])
check(_t.touched == "yes", f"trigger: a caller-supplied file list is matched without a diff -- {_t!r}")
_t = _trigger(BLOCK, ["app/webapp/static/a.css"], diff=False)
check(_t.applies and _t.touched == "unknown", f"trigger: diff=False answers applies and leaves touched unknown -- {_t!r}")
_t = _trigger(None, ["app/webapp/static/a.css"])
check(not _t.applies and _t.touched == "no" and _t.key_views == [], f"trigger: no CLAUDE.md means the spec does not apply -- {_t!r}")

# The private block loader and base resolver stay private: consumers go through trigger().
_LIB = Path(__file__).resolve().parents[2] / "skills" / "_lib"
_leaks = [str(p.relative_to(_LIB)) for p in _LIB.rglob("*.py")
          if p.name != "ux_surface.py" and ("ux_surface._load_block" in p.read_text(encoding="utf-8")
                                            or "ux_surface._default_base" in p.read_text(encoding="utf-8"))]
check(_leaks == [], f"nothing outside ux_surface reaches its private block loader or base resolver -- {_leaks}")


_h.report_and_exit("ux_surface")
