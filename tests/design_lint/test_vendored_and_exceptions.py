"""Unit tests for design_lint's vendored byte-compare, sibling duplicate detection, the shipped-spec smoke, accepted exceptions (fleet-config#836) and the retired range-tab (fleet-config#1407).

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/design_lint/test_vendored_and_exceptions.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import shutil
import subprocess
from pathlib import Path

from _suite import _h, check  # noqa: E402
import design_lint as dl  # noqa: E402

from throwaway_dir import remove_dir, throwaway_dir  # noqa: E402
from design_lint_support import (  # noqa: E402
    APP_ICON_INDEX, GOOD_CSS, HIT_SPEC_940, PRE_MODAL_HTML,
    legacy_files,
)


# ---- vendored byte-compare ----

scaffold = Path(throwaway_dir("dl-scaf-"))
approot = Path(throwaway_dir("dl-app-"))
try:
    ref = scaffold / "app/webapp/static/_vendored/switch"
    ref.mkdir(parents=True)
    (ref / "switch.css").write_text(".toggle{}", encoding="utf-8")
    (ref / "switch.js").write_text("export function switchEl(){}", encoding="utf-8")
    ref2 = scaffold / "app/webapp/static/_vendored/card"
    ref2.mkdir(parents=True)
    (ref2 / "card.css").write_text(".card{}", encoding="utf-8")

    mine = approot / "app/webapp/static/_vendored/switch"
    mine.mkdir(parents=True)
    (mine / "switch.css").write_text(".toggle{}", encoding="utf-8")       # identical
    (mine / "switch.js").write_text("export function forked(){}", encoding="utf-8")  # forked

    res = dl.vendored(approot, scaffold)
    comps = res["components"]
    check(comps["switch"]["status"] == "FORKED", "edited vendored file -> FORKED")
    check(comps["switch"]["files"]["switch.css"] == "IDENTICAL", "byte-identical file detected")
    check(comps["switch"]["files"]["switch.js"] == "FORKED", "diverged file flagged FORKED")
    check(comps["card"]["status"] == "NOT_ADOPTED", "component absent from app -> NOT_ADOPTED")
finally:
    shutil.rmtree(scaffold, ignore_errors=True)
    shutil.rmtree(approot, ignore_errors=True)


# ---- vendored icons-sprite: per-symbol compare, not whole-file (#284 finding 4) ----

_spr_scaf = Path(throwaway_dir("dl-sprscaf-"))
_spr_app = Path(throwaway_dir("dl-sprapp-"))
try:
    _sref = _spr_scaf / "app/webapp/static/_vendored/icons"
    _sref.mkdir(parents=True)
    (_sref / "icons-sprite.html").write_text(
        '<symbol id="i-home"><path d="M0 0"/></symbol>'
        '<symbol id="i-star"><path d="M1 1"/></symbol>',
        encoding="utf-8")

    _sapp = _spr_app / "app/webapp/static/_vendored/icons"
    _sapp.mkdir(parents=True)
    # trimmed subset, byte-identical to the scaffold's matching symbol
    (_sapp / "icons-sprite.html").write_text(
        '<symbol id="i-home"><path d="M0 0"/></symbol>', encoding="utf-8")

    _sres = dl.vendored(_spr_app, _spr_scaf)
    check(_sres["components"]["icons"]["files"]["icons-sprite.html"] == "IDENTICAL (trimmed)",
          "trimmed-but-identical sprite subset -> IDENTICAL (trimmed), not FORKED")
    check(_sres["components"]["icons"]["status"] == "IDENTICAL",
          "a trimmed-identical sprite doesn't fork the whole icons component")

    # a hand-edited symbol IS a genuine fork
    (_sapp / "icons-sprite.html").write_text(
        '<symbol id="i-home"><path d="M9 9"/></symbol>', encoding="utf-8")
    _sres2 = dl.vendored(_spr_app, _spr_scaf)
    check(_sres2["components"]["icons"]["files"]["icons-sprite.html"] == "FORKED",
          "a hand-edited symbol is still genuinely FORKED")

    # untrimmed, fully identical set — no "(trimmed)" suffix
    (_sapp / "icons-sprite.html").write_text(
        '<symbol id="i-home"><path d="M0 0"/></symbol>'
        '<symbol id="i-star"><path d="M1 1"/></symbol>',
        encoding="utf-8")
    _sres3 = dl.vendored(_spr_app, _spr_scaf)
    check(_sres3["components"]["icons"]["files"]["icons-sprite.html"] == "IDENTICAL",
          "a full, untrimmed identical set reports plain IDENTICAL")

    # app-only symbol absent from the reference (local-llm-hub scenario, #389):
    # every shared symbol is byte-identical, but the app also vendors a
    # glyph the reference sprite never had -> still IDENTICAL (trimmed),
    # not FORKED, since the extra symbol isn't drift by itself.
    (_sapp / "icons-sprite.html").write_text(
        '<symbol id="i-home"><path d="M0 0"/></symbol>'
        '<symbol id="i-only-in-app"><path d="M2 2"/></symbol>',
        encoding="utf-8")
    _sres4 = dl.vendored(_spr_app, _spr_scaf)
    check(_sres4["components"]["icons"]["files"]["icons-sprite.html"] == "IDENTICAL (trimmed)",
          "app-only symbol absent from reference -> IDENTICAL (trimmed), not FORKED (#389)")
    check(_sres4["components"]["icons"]["status"] == "IDENTICAL",
          "an app-only extra symbol doesn't fork the whole icons component (#389)")

    # a genuinely different shared symbol must still fork, even with an
    # unrelated app-only extra symbol present alongside it.
    (_sapp / "icons-sprite.html").write_text(
        '<symbol id="i-home"><path d="M9 9"/></symbol>'
        '<symbol id="i-only-in-app"><path d="M2 2"/></symbol>',
        encoding="utf-8")
    _sres5 = dl.vendored(_spr_app, _spr_scaf)
    check(_sres5["components"]["icons"]["files"]["icons-sprite.html"] == "FORKED",
          "a genuinely-diverged shared symbol still forks, even alongside an app-only extra (#389)")
finally:
    shutil.rmtree(_spr_scaf, ignore_errors=True)
    shutil.rmtree(_spr_app, ignore_errors=True)


# ---- vendored-root discovery: non-scaffold static layouts (fleet-config#291, #292) ----

for _layout_name, _static_prefix in (("app_web/static", "app_web/static"),  # local-llm-hub (#291)
                                       ("app/static", "app/static")):        # grocery (#292)
    _scaf = Path(throwaway_dir("dl-scaf2-"))
    _app = Path(throwaway_dir("dl-app2-"))
    try:
        _ref = _scaf / "app/webapp/static/_vendored/switch"
        _ref.mkdir(parents=True)
        (_ref / "switch.css").write_text(".toggle{}", encoding="utf-8")

        _mine = _app / _static_prefix / "_vendored/switch"
        _mine.mkdir(parents=True)
        (_mine / "switch.css").write_text(".toggle{}", encoding="utf-8")

        _res = dl.vendored(_app, _scaf)
        check(_res["app_has_vendored_dir"] is True,
              f"{_layout_name}: discovered without the hardcoded app/webapp/static/ prefix")
        check(_res["components"]["switch"]["status"] == "IDENTICAL",
              f"{_layout_name}: byte-identical vendored copy reports IDENTICAL, not NOT_ADOPTED")
    finally:
        shutil.rmtree(_scaf, ignore_errors=True)
        shutil.rmtree(_app, ignore_errors=True)

# a second `_vendored` copy elsewhere is not the app's: the scaffold layout wins over an
# alphabetically earlier `app/player/static`, and else the static dir holding the app shell (#1087)
for _case, _dirs, _want in (
        ("scaffold layout over app/player", {"app/player/static": False, "app/webapp/static": False}, "app/webapp/static"),
        ("the shell's static dir over a bare one", {"web/a/static": False, "web/b/static": True}, "web/b/static")):
    _app = Path(throwaway_dir("dl-app3-"))
    try:
        for _d, _shell in _dirs.items():
            (_app / _d / "_vendored/switch").mkdir(parents=True)
            if _shell:
                (_app / _d / "index.html").write_text("<!doctype html>", encoding="utf-8")
        _found = dl.find_vendored_root(_app)
        check(_found is not None and _found.relative_to(_app).as_posix() == f"{_want}/_vendored",
              f"vendored root: {_case} -> {_want} -- {_found}")
    finally:
        shutil.rmtree(_app, ignore_errors=True)

# nav-contract provenance follows the same discovery, not just the vendored lens (#291)
_t = Path(throwaway_dir("dl-nav2-"))
try:
    (_t / "s.css").write_text(GOOD_CSS, encoding="utf-8")
    _vend = _t / "app_web/static/_vendored/nav"
    _vend.mkdir(parents=True)
    (_vend / "nav-tabs.css").write_text("/* vendored */", encoding="utf-8")
    _out = {c["id"]: c for c in dl.contracts(_t, [_t / "s.css"], [], [], {})}
    check(_out["nav-contract"]["status"] == "PASS", "app_web/static layout: nav signals + shell PASS")
    check("vendored" in _out["nav-contract"]["detail"],
          "app_web/static layout: nav-contract provenance reads vendored, not hand-carried (#291)")
finally:
    shutil.rmtree(_t, ignore_errors=True)


# ---- sibling duplicate detection ----

sib = Path(throwaway_dir("dl-sib-"))
try:
    (sib / "a.js").write_text(
        "function schedule(ms) {}\nexport function unique1() {}\n", encoding="utf-8")
    (sib / "b.js").write_text(
        "function schedule(ms) {}\nconst fmtW = (v) => v;\n"
        "/* function schedule(fake) {} */\n", encoding="utf-8")
    (sib / "c.js").write_text("const fmtW = () => 0;\nfunction init() {}\n", encoding="utf-8")
    dupes = dl.siblings(sib, [sib / "a.js", sib / "b.js", sib / "c.js"])
    names = {d["name"] for d in dupes}
    check("schedule" in names, "same-name function across 2 files flagged (the #369 case)")
    check("fmtW" in names, "const-arrow duplicates flagged")
    check("unique1" not in names, "single-file definition not flagged")
    check("init" not in names, "stoplist name ignored")
    sched = next(d for d in dupes if d["name"] == "schedule")
    check(len(sched["sites"]) == 2, "commented-out definition not counted")
finally:
    shutil.rmtree(sib, ignore_errors=True)


# ---- real-spec smoke: the shipped design.md parses and carries v2 groups ----

real_spec = Path(__file__).resolve().parent.parent.parent / "design.md"   # the repo's own spec, so a branch checks its own edit
if real_spec.is_file():
    parsed = dl.parse_spec(real_spec.read_text(encoding="utf-8", errors="replace"))
    check(parsed.get("colors.canvas") == "#ffffff", "real design.md: canvas parses")
    check(parsed.get("components.switch.trackOn") == parsed.get("colors.accent-fill"),
          "real design.md: switch trackOn resolves to accent-fill (the accent decision, #1200)")
    check(parsed.get("icons.size.nav-tab") == "20px",
          "real design.md: nav-tab icon step is the phone-validated 20px")
    check((parsed.get("colors.accent-soft") or "").startswith("color-mix("),
          "real design.md: accent-soft is the color-mix derivation (#296)")
    check(parsed.get("components.button-tint.backgroundColor")
          == parsed.get("colors.accent-soft"),
          "real design.md: button-tint fill resolves to accent-soft")
    check(parsed.get("components.button-ghost.backgroundColor") == "transparent",
          "real design.md: ghost = transparent (the settled vocabulary, #296)")

real_dark = Path.home() / ".claude" / "design.dark.md"
if real_dark.is_file():
    parsed_dark = dl.parse_spec(real_dark.read_text(encoding="utf-8", errors="replace"))
    check(parsed_dark.get("colors.canvas") == "#0d1117",
          "real design.dark.md: dark canvas parses (the dark theme-color meta, #290)")

source_spec = Path(__file__).resolve().parent.parent.parent / "design.md"
parsed_source = dl.parse_spec(source_spec.read_text(encoding="utf-8", errors="replace"))
check(parsed_source.get("app-icon.generator") == "brand_gen"
      and parsed_source.get("app-icon.maskable") == "icon-512-maskable.png",
      "source design.md: app-icon family contract parses")


# ---- accepted exceptions: [[design.accepted]] in .fleet.toml (fleet-config#836) ----

ACCEPT_SPEC = """---
icons:
  size:
    inline: 16px
app-icon:
  generator: brand_gen
  apple: icon-180.png
  regular-small: icon-192.png
  regular-large: icon-512.png
  maskable: icon-512-maskable.png
  favicon: favicon.ico
---
"""
ICON_PATHS = ["static/icon-180.png", "static/icon-192.png", "static/icon-512.png",
              "static/icon-512-maskable.png", "static/favicon.ico"]
GENERATOR_DETAIL = "shared brand_gen.render_set generator not adopted"


def _git_repo(path: Path, files: dict[str, str]) -> None:
    for name, body in files.items():
        (path / name).parent.mkdir(parents=True, exist_ok=True)
        (path / name).write_text(body, encoding="utf-8")
    # An empty hooksPath keeps the user's global commit hooks off a synthetic repo.
    for args in (["init", "-q"], ["add", "-A"],
                 ["-c", f"core.hooksPath={path / 'no-hooks'}", "-c", "user.name=t",
                  "-c", "user.email=t@t", "commit", "-qm", "init"]):
        subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)


def lint_accepted(fleet_toml: str | None, index: str = APP_ICON_INDEX,
                  upstream: dict[str, str] | None = None) -> dict:
    """Run the real `contracts` CLI on a legacy-generator app repo; return
    `{"rows": {id: row}, "warns": [accepted-exception details]}`."""
    base = Path(throwaway_dir("dl-acc-"))
    try:
        app_files = {"index.html": index, **legacy_files}
        if fleet_toml is not None:
            app_files[".fleet.toml"] = fleet_toml
        (base / "app").mkdir()
        _git_repo(base / "app", app_files)
        if upstream is not None:
            (base / "upstream").mkdir()
            _git_repo(base / "upstream", upstream)
        (base / "spec.md").write_text(ACCEPT_SPEC, encoding="utf-8")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            dl.main(["contracts", str(base / "app"), "--spec", str(base / "spec.md"),
                     "--spec-dark", str(base / "spec.md")])
        out = json.loads(buf.getvalue())
        return {"rows": {c["id"]: c for c in out if c["id"] != "accepted-exception"},
                "warns": [c["detail"] for c in out if c["id"] == "accepted-exception"]}
    finally:
        remove_dir(base)


def accepted_toml(**overrides: object) -> str:
    fields = {"check": "app-icon-family", "target": "index.html",
              "detail": GENERATOR_DETAIL, "reason": "copies of upstream's generated set",
              "record": "https://example.invalid/issues/23",
              "identical_to": "../upstream", "paths": ICON_PATHS}
    fields.update(overrides)
    lines = ["[[design.accepted]]"]
    lines += [f"{k} = {json.dumps(v)}" for k, v in fields.items() if v is not None]
    return "\n".join(lines) + "\n"


UPSTREAM_SAME = {p: legacy_files[p] for p in ICON_PATHS}

baseline = lint_accepted(None)
check(baseline["rows"]["app-icon-family"]["status"] == "FAIL"
      and baseline["rows"]["app-icon-family"]["detail"] == GENERATOR_DETAIL
      and not baseline["warns"],
      "no declaration -> the generator FAIL is raised exactly as before")

live = lint_accepted(accepted_toml(), upstream=UPSTREAM_SAME)
icon = live["rows"]["app-icon-family"]
check(icon["status"] == "ACCEPTED" and icon["detail"] == GENERATOR_DETAIL
      and icon.get("accepted", {}).get("raised_status") == "FAIL"
      and icon["accepted"]["reason"] == "copies of upstream's generated set"
      and "sha256 identical" in icon["accepted"]["verified"]
      and not live["warns"],
      "live exception with a passing sha256 assertion -> ACCEPTED, still listed, "
      "never dropped (#836)")

drifted = lint_accepted(accepted_toml(),
                        upstream={**UPSTREAM_SAME, "static/icon-512.png": "regenerated"})
icon = drifted["rows"]["app-icon-family"]
check(icon["status"] == "FAIL"
      and icon.get("exception", {}).get("state") == "verify-fail"
      and "static/icon-512.png" in icon["detail"],
      "upstream blob no longer identical -> the finding is re-raised with why")

gone = lint_accepted(accepted_toml())
icon = gone["rows"]["app-icon-family"]
check(icon["status"] == "FAIL"
      and icon.get("exception", {}).get("state") == "verify-unknown",
      "assertion that cannot be established (no upstream repo) -> re-raised, "
      "unknown is never accepted")

unasserted = lint_accepted(accepted_toml(identical_to=None, paths=None))
icon = unasserted["rows"]["app-icon-family"]
check(icon["status"] == "ACCEPTED"
      and icon["accepted"]["verified"] == "no verifiable assertion declared",
      "exception without an assertion -> ACCEPTED, and says nothing was verified")

new_problem = lint_accepted(
    accepted_toml(), upstream=UPSTREAM_SAME,
    index='<head><link rel="manifest" href="/static/manifest.webmanifest">'
          '<link rel="icon" href="/static/favicon.ico"></head>')
check(new_problem["rows"]["app-icon-family"]["status"] == "FAIL"
      and any("matched no current" in w for w in new_problem["warns"]),
      "a new problem in the same check changes the detail -> FAIL re-raised and "
      "the stale declaration reported")

malformed = lint_accepted(accepted_toml(reason=None), upstream=UPSTREAM_SAME)
check(malformed["rows"]["app-icon-family"]["status"] == "FAIL"
      and any("missing reason" in w for w in malformed["warns"]),
      "declaration without a reason is ignored and reported, never applied")

half_assert = lint_accepted(accepted_toml(paths=None), upstream=UPSTREAM_SAME)
check(half_assert["rows"]["app-icon-family"]["status"] == "FAIL"
      and any("identical_to and paths go together" in w for w in half_assert["warns"]),
      "identical_to without paths is ignored and reported")

broken = lint_accepted("[[design.accepted]\n")
check(broken["rows"]["app-icon-family"]["status"] == "FAIL"
      and any("could not read .fleet.toml" in w for w in broken["warns"]),
      "unparseable .fleet.toml -> findings raised, the read failure reported")

# a [design] table with no [[design.accepted]] array (e.g. only [design.review],
# as parking-manager declares) is not malformed -> no accepted-exception WARN
# (fleet-config#1061)
review_only = lint_accepted("[design.review]\nsomething = true\n")
check(review_only["rows"]["app-icon-family"]["status"] == "FAIL"
      and not review_only["warns"],
      "[design] present without [[design.accepted]] -> no accepted-exception WARN")

# a modal-header FAIL is acceptable: its evidence names the dialog's file, so
# `target` matches (fleet-config#953 — it once carried evidence: null)
modal_raised = lint_accepted(None, index=PRE_MODAL_HTML)["rows"]["modal-header"]
check(modal_raised["status"] == "FAIL"
      and (modal_raised["evidence"] or "").startswith("index.html:"),
      "modal-header FAIL carries the dialog's file:line as evidence")
modal_accepted = lint_accepted(
    accepted_toml(check="modal-header", target="index.html",
                  detail=modal_raised["detail"], identical_to=None, paths=None),
    index=PRE_MODAL_HTML)
check(modal_accepted["rows"]["modal-header"]["status"] == "ACCEPTED"
      and not modal_accepted["warns"],
      "a modal-header FAIL is accepted via [[design.accepted]], no stray WARN")

# The detail embeds `<target>:<line>`, so an edit above the dialog shifts it. An
# entry pins the check, the file and the message, not the line (fleet-config#1475).
check(re.search(r"index\.html:\d+", modal_raised["detail"]) is not None,
      "modal-header detail embeds the evidence line the entry used to pin")
modal_shifted = lint_accepted(
    accepted_toml(check="modal-header", target="index.html",
                  detail=modal_raised["detail"], identical_to=None, paths=None),
    index="\n\n\n\n" + PRE_MODAL_HTML)
shifted_row = modal_shifted["rows"]["modal-header"]
check(shifted_row["evidence"] != modal_raised["evidence"]
      and shifted_row["status"] == "ACCEPTED" and not modal_shifted["warns"],
      "an entry still matches when only the line number after the target moved")
modal_other = lint_accepted(
    accepted_toml(check="modal-header", target="index.html",
                  detail=re.sub(r"(index\.html:\d+).*", r"\1 a different problem", modal_raised["detail"]),
                  identical_to=None, paths=None),
    index="\n\n\n\n" + PRE_MODAL_HTML)
check(modal_other["rows"]["modal-header"]["status"] == "FAIL"
      and any("matched no current" in w for w in modal_other["warns"]),
      "a different message in the same check and file is still raised after the line shifts")

# A repo-level finding has no evidence file, so no `target` can name it. The
# minimal two-line entry (`check` + `reason`) accepts it (fleet-config#1322).
RENDERED_LEG = '[[design.accepted]]\ncheck = "rendered-leg"\nreason = "no live instance here"\n'
leg_raised = baseline["rows"]["rendered-leg"]
check(leg_raised["status"] == "WARN" and not leg_raised["evidence"],
      "rendered-leg WARN is repo-level: it carries no evidence file")
minimal = lint_accepted(RENDERED_LEG)
leg = minimal["rows"]["rendered-leg"]
check(leg["status"] == "ACCEPTED" and leg["detail"] == leg_raised["detail"]
      and leg["accepted"]["raised_status"] == "WARN"
      and leg["accepted"]["reason"] == "no live instance here"
      and not minimal["warns"],
      "check + reason alone accepts a repo-level finding, still listed, no stray WARN")
pinned = lint_accepted(RENDERED_LEG + f"detail = {json.dumps(leg_raised['detail'])}\n")
check(pinned["rows"]["rendered-leg"]["status"] == "ACCEPTED" and not pinned["warns"],
      "a repo-level entry with the finding's exact detail accepts it")
changed = lint_accepted(RENDERED_LEG + 'detail = "an older detail"\n')
check(changed["rows"]["rendered-leg"]["status"] == "WARN"
      and any("matched no current" in w for w in changed["warns"]),
      "a repo-level entry whose detail changed re-raises the finding and reports the entry")
untargeted = lint_accepted('[[design.accepted]]\ncheck = "app-icon-family"\nreason = "r"\n')
check(untargeted["rows"]["app-icon-family"]["status"] == "FAIL"
      and any("matched no current" in w for w in untargeted["warns"]),
      "an entry without target never accepts a finding that has an evidence file")
nothing = lint_accepted('[[design.accepted]]\ncheck = "no-such-check"\nreason = "r"\n')
check(any("no-such-check" in w and "matched no current" in w for w in nothing["warns"]),
      "a minimal entry whose check matches nothing is a stale-declaration WARN")
no_detail = lint_accepted(accepted_toml(detail=None), upstream=UPSTREAM_SAME)
check(no_detail["rows"]["app-icon-family"]["status"] == "FAIL"
      and any("missing detail" in w for w in no_detail["warns"]),
      "a targeted entry still needs its detail, exactly as before")
no_check = lint_accepted('[[design.accepted]]\nreason = "r"\n')
check(any("missing check" in w for w in no_check["warns"]),
      "an entry without check is ignored and reported")


# ---- range-tab retired in favour of segmented (fleet-config#1407) ----

def _contracts_range_tab(files: dict[str, str]) -> dict:
    t = Path(throwaway_dir("dl-rt-"))
    try:
        for name, body in files.items():
            path = t / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
        return {c["id"]: c for c in dl.contracts(
            t, sorted(t.rglob("*.css")), sorted(t.rglob("*.html")),
            sorted(t.rglob("*.js")), HIT_SPEC_940)}
    finally:
        shutil.rmtree(t, ignore_errors=True)


_RT_HTML = '<div class="range-tabs"><button class="range-tab active">Day</button></div>\n'
_RT_JS = 'const row = `<div class="range-tabs">${opts}</div>`;\n'
_RT_CSS = ".range-tabs { display: flex; }\n.range-tab.active { background: var(--accent-soft); }\n"
_SEG_HTML = '<div class="segmented"><button aria-pressed="true">Day</button></div>\n'

rt_app = _contracts_range_tab({"app/static/index.html": _RT_HTML,
                               "app/static/view.js": _RT_JS,
                               "app/static/style.css": _RT_CSS})["range-tab"]
check(rt_app["status"] == "WARN" and "use segmented" in rt_app["detail"]
      and "app/static/index.html:1" in rt_app["detail"]
      and "app/static/view.js:1" in rt_app["detail"]
      and "app/static/style.css:1" in rt_app["detail"],
      "range-tab: app markup, JS and CSS are flagged with file:line and 'use segmented' (#1407)")

rt_clean = _contracts_range_tab({"app/static/index.html": _SEG_HTML})["range-tab"]
check(rt_clean["status"] == "PASS",
      "range-tab: an app already on segmented passes (#1407)")

rt_vendored = _contracts_range_tab({
    "app/static/_vendored/range-tab/range-tab.css": _RT_CSS,
    "app/static/_vendored/range-tab/range-tab.js": _RT_JS,
    "app/static/index.html": _SEG_HTML})["range-tab"]
check(rt_vendored["status"] == "PASS",
      "range-tab: the scaffold's deprecated `_vendored/` copy is not an app-authored site (#1407)")

rt_mixed = _contracts_range_tab({
    "app/static/_vendored/range-tab/range-tab.css": _RT_CSS,
    "app/static/index.html": _RT_HTML})["range-tab"]
check(rt_mixed["status"] == "WARN" and "_vendored" not in rt_mixed["detail"].split(": ", 1)[-1],
      "range-tab: an app's own use is flagged even beside the vendored copy, naming only its own file (#1407)")

rt_names = _contracts_range_tab({
    "app/static/style.css": ".time-range-tab { x: 1 }\n:root { --range-tab-bg: red; }\n/* .range-tab old */\n"})["range-tab"]
check(rt_names["status"] == "PASS",
      "range-tab: a longer class name, a custom property and a comment are not hits (#1407)")


_h.report_and_exit("test_vendored_and_exceptions")
