"""Unit tests for the pure logic in skills/_lib/e2e_route.py (fleet-config#556).

No live git and no real repos — synthetic trees in a tempdir exercise the
probe facts (classifier/table/suite/web-surface), the bootstrap copy-verbatim
+ refuse-on-divergence contract, and the route fallback when no classifier
exists. The real classifier's own behaviour is project-scaffolding's to test.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/test_e2e_route.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "_lib"))
import e2e_route as er  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check


def _capture(fn, *args) -> tuple[int, str]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = fn(*args)
    return rc, buf.getvalue()


with tempfile.TemporaryDirectory() as td:
    root = Path(td)

    # ---- synthetic scaffold (the classifier source of truth) ----
    scaffold = root / "scaffold"
    (scaffold / "scripts").mkdir(parents=True)
    (scaffold / "scripts" / "classify_e2e.py").write_text(
        "print('E2E_TIER=full')\nprint('E2E_REASON=stub')\n", encoding="utf-8")

    # ---- bare pipeline repo: nothing declared ----
    bare = root / "bare"
    bare.mkdir()
    check(er.classifier_state(bare, scaffold) == ("absent", "n/a"),
          "bare repo: classifier absent, match n/a")
    check(er.e2e_table_state(bare) == "absent", "bare repo: no .fleet.toml -> table absent")
    check(er.suite_state(bare) == "absent", "bare repo: no tests/e2e -> suite absent")
    check(er.detect_web_surface(bare) == ("no", "none", "no web framework signal"),
          "bare repo: no web surface")

    # ---- web repo by declared .fleet.toml layer ----
    web = root / "web"
    web.mkdir()
    (web / ".fleet.toml").write_text('layer = "working-web"\n[e2e]\nx = 1\n', encoding="utf-8")
    surface, kind, _ = er.detect_web_surface(web)
    check((surface, kind) == ("yes", "webapp"), "layer=working-web -> webapp surface")
    check(er.e2e_table_state(web) == "present", "[e2e] table detected via tomllib")

    # ---- streamlit repo by dependency; malformed .fleet.toml reads invalid ----
    st = root / "st"
    st.mkdir()
    (st / "requirements.txt").write_text("streamlit==1.30\npandas\n", encoding="utf-8")
    (st / ".fleet.toml").write_text("layer = \n", encoding="utf-8")
    surface, kind, _ = er.detect_web_surface(st)
    check((surface, kind) == ("yes", "streamlit"), "streamlit dependency -> streamlit surface")
    check(er.e2e_table_state(st) == "invalid", "malformed .fleet.toml -> table invalid")

    # ---- fastapi repo by dependency; suite presence needs a real test file ----
    api = root / "api"
    (api / "tests" / "e2e").mkdir(parents=True)
    (api / "requirements.txt").write_text("fastapi\nuvicorn\n", encoding="utf-8")
    surface, kind, _ = er.detect_web_surface(api)
    check((surface, kind) == ("yes", "webapp"), "fastapi dependency -> webapp surface")
    check(er.suite_state(api) == "absent", "empty tests/e2e dir is not a suite")
    (api / "tests" / "e2e" / "test_smoke.py").write_text("def test_up(): pass\n", encoding="utf-8")
    check(er.suite_state(api) == "present", "a test module under tests/e2e -> suite present")

    # ---- probe output shape ----
    rc, out = _capture(er.cmd_probe, api, scaffold)
    check(rc == 0 and "CLASSIFIER=absent" in out and "WEB_KIND=webapp" in out
          and "SUITE=present" in out, "probe prints the fact block")

    # ---- route: no classifier -> judgment fallback, never a silent skip ----
    rc, out = _capture(er.cmd_route, api, [])
    check(rc == 0 and "SOURCE=judgment" in out and "E2E_TIER=unknown" in out,
          "route without classifier -> judgment fallback with explicit unknown tier")

    # ---- bootstrap: copy verbatim, then idempotent ----
    rc, out = _capture(er.cmd_bootstrap, api, scaffold, False)
    check(rc == 0 and "BOOTSTRAP=copied" in out, "bootstrap copies the scaffold classifier")
    check(er.files_identical(scaffold / "scripts" / "classify_e2e.py",
                             api / "scripts" / "classify_e2e.py"),
          "bootstrap copy is byte-identical")
    check(er.classifier_state(api, scaffold) == ("present", "yes"),
          "post-bootstrap probe reads present + matches scaffold")
    rc, out = _capture(er.cmd_bootstrap, api, scaffold, False)
    check(rc == 0 and "BOOTSTRAP=exists-identical" in out, "re-bootstrap is a no-op")

    # ---- route: classifier present -> E2E_* pass-through ----
    rc, out = _capture(er.cmd_route, api, [])
    check(rc == 0 and "SOURCE=classifier" in out and "E2E_TIER=full" in out,
          "route runs the repo classifier and passes E2E_* through")

    # ---- route: subprocess decoding is pinned, never the ambient locale ----
    # `subprocess.run(..., text=True)` with no `encoding=`/`errors=` decodes
    # using the ambient codec; a byte that codec rejects raises
    # UnicodeDecodeError *inside* subprocess.run itself -- a ValueError the
    # `except (OSError, subprocess.TimeoutExpired)` guard does not catch, so
    # the deliberate E2E_TIER=full fail-safe never ran (fleet-config#709).
    _captured_kwargs: dict = {}
    _real_run = er.subprocess.run

    def _spy_run(*args, **kwargs):
        _captured_kwargs.update(kwargs)
        return _real_run(*args, **kwargs)

    er.subprocess.run = _spy_run
    try:
        _capture(er.cmd_route, api, [])
    finally:
        er.subprocess.run = _real_run
    check(_captured_kwargs.get("encoding") == "utf-8" and _captured_kwargs.get("errors") == "replace",
          "cmd_route pins encoding=utf-8, errors=replace on the classifier subprocess")

    # ---- bootstrap: refuses to clobber a diverged (custom) classifier ----
    custom = root / "custom"
    (custom / "scripts").mkdir(parents=True)
    (custom / "scripts" / "classify_e2e.py").write_text("# hardcoded legacy\n", encoding="utf-8")
    rc, out = _capture(er.cmd_bootstrap, custom, scaffold, False)
    check(rc == 1 and "BOOTSTRAP=refused" in out,
          "bootstrap refuses an existing different classifier without --force")
    rc, out = _capture(er.cmd_bootstrap, custom, scaffold, True)
    check(rc == 0 and "BOOTSTRAP=copied" in out and er.files_identical(
        scaffold / "scripts" / "classify_e2e.py", custom / "scripts" / "classify_e2e.py"),
        "--force migrates the custom classifier to the scaffold copy")

    # ---- broken classifier -> fail-safe full, never narrow ----
    broken = root / "broken"
    (broken / "scripts").mkdir(parents=True)
    (broken / "scripts" / "classify_e2e.py").write_text("raise SystemExit(3)\n", encoding="utf-8")
    rc, out = _capture(er.cmd_route, broken, [])
    check(rc == 0 and "SOURCE=classifier-error" in out and "E2E_TIER=full" in out,
          "classifier error escalates to full (fail-safe), never skip/unknown")

    # ---- surface tier (fleet-config#902, project-scaffolding#258) ----
    def _fake_classifier(name: str, body: str) -> Path:
        repo = root / name
        (repo / "scripts").mkdir(parents=True)
        (repo / "scripts" / "classify_e2e.py").write_text(body, encoding="utf-8")
        return repo

    surface = _fake_classifier("surface", (
        "print('E2E_TIER=surface')\n"
        "print('E2E_BROWSERS=')\n"
        "print('E2E_PYTEST_TARGET=tests/e2e/test_nav.py tests/e2e/test_smoke.py')\n"
        "print('E2E_REASON=surface nav: app/nav/nav.css')\n"
        "print('E2E_SURFACE=nav')\n"
    ))
    rc, out = _capture(er.cmd_route, surface, [])
    check(rc == 0 and "SOURCE=classifier" in out and "E2E_TIER=surface" in out
          and "E2E_SURFACE=nav" in out
          and "E2E_PYTEST_TARGET=tests/e2e/test_nav.py tests/e2e/test_smoke.py" in out,
          "a well-formed surface verdict passes through verbatim, multi-path target intact")

    for name, lines, why in (
        ("surface-no-targets",
         "print('E2E_TIER=surface')\nprint('E2E_PYTEST_TARGET=')\nprint('E2E_SURFACE=nav')\n",
         "a surface verdict with an empty target list"),
        ("surface-no-name",
         "print('E2E_TIER=surface')\nprint('E2E_PYTEST_TARGET=tests/e2e/test_nav.py')\n",
         "a surface verdict naming no surface"),
        ("unknown-tier",
         "print('E2E_TIER=partial')\nprint('E2E_PYTEST_TARGET=tests/e2e/test_nav.py')\n",
         "an unrecognised tier"),
    ):
        rc, out = _capture(er.cmd_route, _fake_classifier(name, lines), [])
        check(rc == 0 and "SOURCE=classifier-error" in out and "E2E_TIER=full" in out
              and "E2E_TIER=surface" not in out and "E2E_TIER=partial" not in out,
              f"{why} escalates to whole-suite full, never passes through")

check(er.unusable_verdict(["E2E_TIER=skip", "E2E_PYTEST_TARGET="]) is None,
      "unusable_verdict: skip with an empty target is a legitimate verdict")
check(er.unusable_verdict(["E2E_TIER=static", "E2E_PYTEST_TARGET=tests/e2e/test_smoke.py"]) is None,
      "unusable_verdict: static passes")


_h.report_and_exit("e2e_route")
