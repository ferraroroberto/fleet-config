"""design_lint's web-page surface: `--surface web-page` against design.web.md.

A public static site is held to `design.web.md`, never the PWA spec: its own
token names (values are the brand), its own contract set, the vendored and
sibling lenses narrowed to what a site can act on, and a missing spec reported
as `not_found` (exit 3) rather than an empty, clean-looking lint. Synthetic
trees only (`repo_files` falls back to a walk without git); the spec is the
repo's own `design.web.md`.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/design_lint/test_web_page.py`  (also invoked by tests/run_acceptance.py)
"""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
from typing import Dict, List

from _suite import REPO, check, _h

import design_lint as dl  # noqa: E402
from design_lint.web_page import adopted_only, contract_token_names, without_build_copies  # noqa: E402
from throwaway_dir import remove_dir, throwaway_dir  # noqa: E402

SPEC = REPO / "design.web.md"


def make_tree(files: Dict[str, str]) -> Path:
    root = throwaway_dir("design_lint_web_")
    for name, body in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return root


def run(argv: List[str]) -> tuple:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = dl.main(argv)
    return code, json.loads(buf.getvalue())


GOOD_CSS = """:root { --paper: #fff; --ink: #111; --control: 48px; --footer-band: #595959; }
:focus-visible { outline: 2px solid var(--ink); outline-offset: 2px; }
.site-header { position: sticky; top: 0; background: var(--paper); }
.wrap { container-type: inline-size; }
.card a::after { content: ""; position: absolute; inset: 0; }
.carousel { display: grid; grid-auto-flow: column; grid-auto-columns: 82%; scroll-snap-type: x mandatory; }
.site-footer { background: var(--footer-band); }
@media (prefers-reduced-motion: reduce) { * { scroll-behavior: auto; } }
"""
GOOD_HTML = """<html lang="en"><body>
<header class="site-header"><button aria-expanded="false" aria-controls="nav">Menu</button>
<nav id="nav"><a href="/" aria-current="page">Home</a></nav></header>
<main><img src="a.webp" width="10" height="10" alt="a"></main><footer class="site-footer"></footer>
</body></html>
"""
BAD_CSS = """:root { --paper: #fff; --control: 40px; }
.site-header { position: sticky; backdrop-filter: blur(8px); }
.row { grid-template-columns: minmax(0, 520px) 1fr; }
.carousel { display: flex; overflow-x: auto; }
.tile { box-shadow: 0 1px 2px #000; }
"""
BAD_HTML = "<html><body><header class=\"site-header\"></header><img src=\"a.webp\" alt=\"a\"></body></html>\n"

# ---- the spec's token names ----

names = contract_token_names(SPEC.read_text(encoding="utf-8"))
check("--paper" in names and "--img-thumb" in names and "--fs-label" in names,
      f"token names come from the spec's section 2 table -- {names[:6]}")
check([n for n in names if n.startswith("--s") and n[3:].isdigit()] == [f"--s{i}" for i in range(1, 10)],
      "the `--s1` ... `--s9` range expands to nine names")
check(len(names) == len(set(names)), "no token name is listed twice")
check(contract_token_names("# no table here\n") == [], "a spec with no token table yields no names")

# ---- a conforming site ----

good = make_tree({"css/site.css": GOOD_CSS, "index.html": GOOD_HTML, "js/site.js": "function setMenu() {}\n",
                  "site/js/site.js": "function setMenu() {}\n"})
try:
    code, out = run(["all", str(good), "--surface", "web-page", "--spec", str(SPEC)])
    check(code == 0 and out["surface"] == "web-page", f"web-page all exits 0 -- {code}")
    rows = {c["id"]: c["status"] for c in out["contracts"]}
    check(all(i.startswith("web-") for i in rows), f"every contract id is a web-page one, none PWA -- {sorted(rows)}")
    for cid in ("web-focus-visible", "web-reduced-motion", "web-header", "web-header-current",
                "web-phone-menu", "web-sections-container", "web-no-shadow", "web-card",
                "web-carousel", "web-footer", "web-image-roles", "web-image-dimensions"):
        check(rows.get(cid) == "PASS", f"conforming site: {cid} PASS -- got {rows.get(cid)}")
    tok = out["tokens"]
    check("--paper" in tok["present"] and "--measure" in tok["missing"],
          "tokens: declared names are present, undeclared ones missing")
    check(tok["floors"] == [{"token": "--control", "status": "PASS", "detail": "48px against the 48px floor"}],
          f"tokens: --control meets the spec floor -- {tok['floors']}")
    check(out["vendored"].get("components") in ({}, None),
          f"vendored: no PWA component is listed as not adopted -- {out['vendored']}")
    check(out["siblings"] == [], "siblings: a committed build copy of a script is not a duplicate")
finally:
    remove_dir(good)

# ---- a site that breaks the contract ----

bad = make_tree({"css/site.css": BAD_CSS, "index.html": BAD_HTML})
try:
    code, out = run(["all", str(bad), "--surface", "web-page", "--spec", str(SPEC)])
    rows = {c["id"]: c for c in out["contracts"]}
    check(rows["web-header"]["status"] == "FAIL" and "backdrop-filter" in rows["web-header"]["detail"],
          "backdrop-filter on the header fails: it traps the fixed phone menu")
    check(rows["web-header"]["evidence"] == "css/site.css:2", f"the header finding points at its line -- {rows['web-header']['evidence']}")
    check(rows["web-image-roles"]["status"] == "FAIL", "a minmax(0, 520px) 1fr track fails the tablet band")
    check(rows["web-carousel"]["status"] == "FAIL", "a carousel without grid-auto-columns and scroll snap fails")
    check(rows["web-phone-menu"]["status"] == "FAIL", "no menu button with aria-expanded fails")
    check(rows["web-footer"]["status"] == "FAIL", "no <footer> landmark fails")
    check(rows["web-no-shadow"]["status"] == "WARN", "a box-shadow is flagged")
    check(rows["web-image-dimensions"]["status"] == "WARN", "an <img> without width and height is flagged")
    check(rows["web-card"]["status"] == "NA", "no card component is NA, never a pass")
    check(out["tokens"]["floors"][0]["status"] == "FAIL", "a 40px --control fails the 48px floor")
finally:
    remove_dir(bad)

# ---- the spec file is its own outcome ----

empty = make_tree({"css/site.css": GOOD_CSS})
try:
    code, out = run(["all", str(empty), "--surface", "web-page", "--spec", str(empty / "absent.md")])
    check(code == 3 and out.get("spec_status") == "not_found",
          f"a missing web-page spec is not_found with exit 3, never a clean lint -- {code} {out}")
    code, out = run(["vendored", str(empty), "--surface", "web-page"])
    check(code == 0, "a lens that reads no spec runs without one")
finally:
    remove_dir(empty)

# ---- vendored: a site is compared against the scaffold's site set ----

scaf = make_tree({
    "app/site/static/_vendored/site-card/site-card.css": ".card{}",
    "app/site/static/_vendored/site-card/README.md": "card",
    "app/site/static/_vendored/site-button/site-button.css": ".btn{}",
    "app/webapp/static/_vendored/switch/switch.css": ".toggle{}",
})
same = make_tree({"content/static/_vendored/site-card/site-card.css": ".card{}",
                  "content/static/_vendored/site-card/README.md": "card",
                  "content/static/_vendored/site-button/site-button.css": ".btn{}"})
drift = make_tree({"content/static/_vendored/site-card/site-card.css": ".card{color:red}",
                   "content/static/_vendored/site-card/README.md": "card",
                   "content/static/_vendored/site-button/site-button.css": ".btn{}"})
foreign = make_tree({"app/webapp/static/_vendored/switch/switch.css": ".toggle{}"})
bare = make_tree({"index.html": GOOD_HTML})
try:
    for tree, what in ((same, "byte-identical"), (drift, "drifted")):
        code, out = run(["vendored", str(tree), "--surface", "web-page", "--scaffold", str(scaf)])
        check(code == 0, f"{what} site: the lens runs")
        comps = out["components"]
        check(sorted(comps) == ["site-button", "site-card"],
              f"{what} site: compared against the site set, not the PWA set -- {sorted(comps)}")
    code, out = run(["vendored", str(same), "--surface", "web-page", "--scaffold", str(scaf)])
    check(all(c["status"] == "IDENTICAL" for c in out["components"].values()),
          f"a byte-identical site tree reports IDENTICAL -- {out}")
    code, out = run(["vendored", str(drift), "--surface", "web-page", "--scaffold", str(scaf)])
    check(out["components"]["site-card"]["status"] == "FORKED"
          and out["components"]["site-card"]["files"]["site-card.css"] == "FORKED",
          f"one drifted byte fails and names the file -- {out['components']['site-card']}")
    check(out["components"]["site-button"]["status"] == "IDENTICAL", "the untouched component stays IDENTICAL")

    code, out = run(["vendored", str(foreign), "--surface", "web-page", "--scaffold", str(scaf)])
    check("error" in out and "components" not in out,
          f"a _vendored folder sharing no site component is not confirmed, never a pass -- {out}")
    code, out = run(["vendored", str(bare), "--surface", "web-page", "--scaffold", str(scaf)])
    check(out["app_has_vendored_dir"] is False and out["components"] == {},
          f"a site with no _vendored folder keeps today's empty result -- {out}")
    code, out = run(["vendored", str(same), "--surface", "web-page", "--scaffold", str(bare)])
    check("error" in out, f"a scaffold without the site set is an error, not a pass -- {out}")

    # web apps are unchanged: still the PWA set, and a site tree is NOT_ADOPTED there
    code, out = run(["vendored", str(foreign), "--scaffold", str(scaf)])
    check(out["components"] == {"switch": {"status": "IDENTICAL",
                                           "files": {"switch.css": "IDENTICAL"}}},
          f"web-app surface still compares the PWA set -- {out}")
    code, out = run(["vendored", str(same), "--scaffold", str(scaf)])
    check(out["components"]["switch"]["status"] == "NOT_ADOPTED", "web-app surface ignores site components")

    # an LF site copy of a CRLF scaffold checkout is the same git blob
    crlf = make_tree({"app/site/static/_vendored/site-card/site-card.css": ""})
    lfsite = make_tree({"content/static/_vendored/site-card/site-card.css": ""})
    try:
        (crlf / "app/site/static/_vendored/site-card/site-card.css").write_bytes(b".a{}\r\n.b{}\r\n")
        (lfsite / "content/static/_vendored/site-card/site-card.css").write_bytes(b".a{}\n.b{}\n")
        code, out = run(["vendored", str(lfsite), "--surface", "web-page", "--scaffold", str(crlf)])
        check(out["components"]["site-card"]["status"] == "IDENTICAL",
              f"web-page: CRLF vs LF checkouts of one blob are IDENTICAL -- {out}")
    finally:
        remove_dir(crlf)
        remove_dir(lfsite)

    # a file that cannot be read is its own state
    import sys
    vmod = sys.modules["design_lint.vendored"]  # the package re-exports the function under this name
    real = vmod._digest

    def _boom(p, lf=False):
        raise OSError("locked")
    vmod._digest = _boom
    try:
        report = dl.vendored(same, scaf, "web-page")
    finally:
        vmod._digest = real
    check(report["components"]["site-card"]["status"] == "NOT_CONFIRMED"
          and report["components"]["site-card"]["files"]["site-card.css"] == "UNREADABLE",
          f"an unreadable file is NOT_CONFIRMED, not IDENTICAL -- {report['components']['site-card']}")
finally:
    for t in (scaf, same, drift, foreign, bare):
        remove_dir(t)

# ---- the scope helpers on their own ----

report = {"components": {"nav": {"status": "NOT_ADOPTED", "files": {}},
                         "header": {"status": "IDENTICAL", "files": {"a": "IDENTICAL"}}}, "app_has_vendored_dir": True}
check(list(adopted_only(report)["components"]) == ["header"], "adopted_only keeps only what the site vendors")
check(adopted_only({"error": "x"}) == {"error": "x"}, "adopted_only passes an error report through")
fork = make_tree({"a.js": "function f() { return 1; }\n", "b.js": "function f() { return 2; }\n"})
try:
    rows = [{"name": "f", "sites": ["a.js:1", "b.js:1"]}]
    check(without_build_copies(fork, rows) == rows, "two different files defining f stay a duplicate")
finally:
    remove_dir(fork)

_h.report_and_exit("design_lint_web_page")
