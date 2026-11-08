"""Unit tests for design_lint's page-foot contract: a web app mounts the one vendored build footer, with no private copy beside it.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/design_lint/test_page_foot_contract.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import shutil
from pathlib import Path

from _suite import _h, check  # noqa: E402

# isort: split
import design_lint as dl  # noqa: E402
from throwaway_dir import throwaway_dir  # noqa: E402

_STATIC = "app/webapp/static"
_VENDORED = {
    f"{_STATIC}/_vendored/page-foot/page-foot.css": ".page-foot { display: block; }\n",
    # The component's own source defines fmtBuildTime and the `Build:` text: it is never a finding.
    f"{_STATIC}/_vendored/page-foot/page-foot.js":
        "export function fmtBuildTime(iso) { return iso; }\n"
        "export function buildReadoutText(sha, at) { return 'Build: ' + sha + ' · ' + at; }\n",
    f"{_STATIC}/_vendored/page-foot/page-foot.html": '<footer class="page-foot"><p id="buildReadout"></p></footer>\n',
}
_INDEX_OK = ('<body><main class="app"><section>tab</section>\n'
             '<footer class="page-foot"><p id="buildReadout"></p></footer></main></body>\n')
_JS_OK = ("import { buildReadoutText } from '/static/_vendored/page-foot/page-foot.js';\n"
          "els.buildReadout.textContent = buildReadoutText(body.git_sha, body.built_at);\n")


def page_foot(index: str | None = _INDEX_OK, js: str = _JS_OK, *, vendored: bool = True,
              extra: dict[str, str] | None = None) -> dict:
    t = Path(throwaway_dir("dl-pf-"))
    try:
        files = {f"{_STATIC}/styles.css": ":root { --muted: #656d76; }\n"}
        if index is not None:
            files[f"{_STATIC}/index.html"] = index
        if js:
            files[f"{_STATIC}/app.js"] = js
        if vendored:
            files.update(_VENDORED)
        files.update(extra or {})
        for name, body in files.items():
            path = t / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
        out = dl.contracts(t, sorted(t.rglob("*.css")), sorted(t.rglob("*.html")),
                           sorted(t.rglob("*.js")), {"icons.size.inline": "16px"})
        rows = [c for c in out if c["id"] == "page-foot"]
        check(len(rows) == 1, f"page-foot: exactly one row -- {rows}")
        return rows[0] if rows else {"status": None, "detail": "", "evidence": None}
    finally:
        shutil.rmtree(t, ignore_errors=True)


# ---- conforming app ----
_ok = page_foot()
check(_ok["status"] == "PASS", f"vendored, mounted once, filled by buildReadoutText -> PASS -- {_ok}")
check(_ok["evidence"] == f"{_STATIC}/index.html:2", f"PASS evidence is the mount site -- {_ok}")

# ---- no vendored page-foot ----
_none = page_foot(index="<body><main class=\"app\">no footer</main></body>\n", js="", vendored=False)
check(_none["status"] == "WARN", f"no vendored page-foot WARNs while apps adopt one by one -- {_none}")
check("no _vendored/page-foot" in _none["detail"] and _none["evidence"] is None,
      f"missing-footer WARN names the component and has no evidence line -- {_none}")

# ---- hand-rolled build line, no vendored component ----
_hand = page_foot(index="<body><main class=\"app\">x</main></body>\n", js="el.textContent = 'Build: ' + sha;\n", vendored=False)
check(_hand["status"] == "WARN" and "hand-rolled build line" in _hand["detail"],
      f"a private Build: line with no vendored footer WARNs and says so -- {_hand}")
check(_hand["evidence"] == f"{_STATIC}/app.js:1", f"hand-rolled WARN points at the line -- {_hand}")

# ---- hand-rolled second build line beside the vendored footer ----
_dup = page_foot(js=_JS_OK + "settings.build.textContent = `Build: ${sha}`;\n")
check(_dup["status"] == "FAIL" and "hand-rolled build line or formatter" in _dup["detail"],
      f"a private Build: line beside the vendored footer FAILs -- {_dup}")
check(_dup["evidence"] == f"{_STATIC}/app.js:3", f"duplicate FAIL points at the private line -- {_dup}")

_fmt = page_foot(js=_JS_OK + "function fmtBuildTime(iso) { return iso; }\n")
check(_fmt["status"] == "FAIL", f"a private fmtBuildTime beside the vendored footer FAILs -- {_fmt}")

_two = page_foot(index=_INDEX_OK.replace("</main>", '<footer class="page-foot"><p></p></footer></main>'))
check(_two["status"] == "FAIL" and "2 page-foot elements" in _two["detail"],
      f"two page-foot elements FAIL -- {_two}")

# ---- vendored but not wired ----
_unmounted = page_foot(index="<body><main class=\"app\">x</main></body>\n", js=_JS_OK)
check(_unmounted["status"] == "WARN" and "never mounted" in _unmounted["detail"],
      f"vendored but no <footer class=page-foot> WARNs -- {_unmounted}")

_unfilled = page_foot(js="console.log('no version fetch');\n")
check(_unfilled["status"] == "WARN" and "buildReadoutText() is never called" in _unfilled["detail"],
      f"a mounted footer nothing fills WARNs -- {_unfilled}")

# ---- scoping ----
_comment = page_foot(js=_JS_OK + "// the old line was 'Build: ' + sha\n")
check(_comment["status"] == "PASS", f"a comment naming the old build line is not a hit -- {_comment}")

_third = page_foot(extra={f"{_STATIC}/vendor/lib/lib.js": "x.textContent = 'Build: ' + v;\n"})
check(_third["status"] == "PASS", f"a third-party vendor/ bundle's Build: text is not the app's -- {_third}")

_settings_row = page_foot(index=_INDEX_OK.replace("<section>", "<section><span>Build</span> "))
check(_settings_row["status"] == "PASS", f"a Settings 'Build' label without a build line may stay -- {_settings_row}")

# The error fallback written into the vendored element itself is not a second build line.
_fallback = page_foot(js=_JS_OK + "els.buildReadout.textContent = 'Build: unknown';\n")
check(_fallback["status"] == "PASS", f"a 'Build: unknown' fallback in the vendored element is not a duplicate -- {_fallback}")

_na = page_foot(index=None)
check(_na["status"] == "NA", f"no index.html -> NA -- {_na}")

_h.report_and_exit("test_page_foot_contract")
