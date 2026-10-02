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
        ("contradictory-tier",
         "print('E2E_TIER=full')\nprint('E2E_PYTEST_TARGET=tests/e2e')\n"
         "print('E2E_TIER=surface')\nprint('E2E_PYTEST_TARGET=tests/e2e/test_nav.py')\n"
         "print('E2E_SURFACE=nav')\n",
         "a classifier printing E2E_TIER twice"),
    ):
        rc, out = _capture(er.cmd_route, _fake_classifier(name, lines), [])
        check(rc == 0 and "SOURCE=classifier-error" in out and "E2E_TIER=full" in out
              and "E2E_TIER=surface" not in out and "E2E_TIER=partial" not in out,
              f"{why} escalates to whole-suite full, never passes through")

    check(er.unusable_verdict(["E2E_TIER=skip", "E2E_PYTEST_TARGET="]) is None,
          "unusable_verdict: skip with an empty target is a legitimate verdict")
    check(er.unusable_verdict(["E2E_TIER=static", "E2E_PYTEST_TARGET=tests/e2e/test_smoke.py"]) is None,
          "unusable_verdict: static passes")


# ---- gate contract: the gate splits the classifier's space-joined targets (fleet-config#1134) ----------
# home-automation#784: the classifier joins a surface's targets with spaces, the gate passed them to
# pytest as one argument, and every branch touching two e2e modules failed with exit 4.

HA_BEFORE = (
    '$tier = "full"; $e2eTarget = "tests/e2e"; $e2eBrowsers = ""\n'
    '$classifyOut = & $py "scripts/classify_e2e.py"\n'
    '    $e2eTarget = $kv["E2E_PYTEST_TARGET"]\n'
    '$e2eArgs = @($e2eTarget, "-p", "tests._progress_log")\n'
    'foreach ($b in ($e2eBrowsers -split \',\' | Where-Object { $_ })) { $e2eArgs += @("--browser", $b) }\n'
)
HA_AFTER = HA_BEFORE.replace('@($e2eTarget, "-p", "tests._progress_log")',
                             '@($e2eTarget -split \'\\s+\' | Where-Object { $_ }) + @("-p", "tests._progress_log")')
INLINE = '    $targets = @(([string]$kv["E2E_PYTEST_TARGET"]) -split \'\\s+\' | Where-Object { $_ })\n'
check(er.target_split(HA_BEFORE) == "no", "home-automation#784's gate: the target lands in a variable never split")
check(er.target_split(HA_AFTER) == "yes", "#785's fix: the variable is split on whitespace before pytest")
check(er.target_split(INLINE) == "yes", "app-launcher's shape: split on the line that reads the key")
check(er.target_split("pytest tests/e2e -q\n") is None, "a gate that never reads the key is not a reader")
check(er.target_split("# reads E2E_PYTEST_TARGET\n& $py -m pytest @args\n") is None, "a comment is not a read")
check(er.target_split('Run-Gate (Get-E2E "E2E_PYTEST_TARGET")\n') == "unknown", "a read this check cannot follow is unknown")

SURFACE_CLASSIFIER = 'def route():\n    return Routing("surface", [], " ".join(targets), [], "self")\n'
with tempfile.TemporaryDirectory() as gd:
    g = Path(gd)
    check(er.gate_contract(g)[0] == "n/a", "no classifier: nothing to consume")
    (g / "scripts").mkdir()
    (g / "scripts" / "classify_e2e.py").write_text(SURFACE_CLASSIFIER, encoding="utf-8")
    (g / "scripts" / "verify-before-ship.ps1").write_text("& $py -m pytest tests/e2e -q\n", encoding="utf-8")
    nc = er.gate_contract(g)
    check(nc[0] == "not-consumed" and nc[2] == [],
          f"voice-transcriber's shape: the gate never runs the classifier, so the [e2e] table changes nothing -- {nc}")
    (g / "scripts" / "verify-before-ship.ps1").write_text(HA_BEFORE, encoding="utf-8")
    br = er.gate_contract(g)
    check(br[0] == "broken" and br[2] == [("scripts/verify-before-ship.ps1", "no")],
          f"an unsplit read with a classifier that emits several targets is broken -- {br}")
    (g / "scripts" / "classify_e2e.py").write_text("def route():\n    return Routing('full', [], 'tests/e2e')\n",
                                                   encoding="utf-8")
    check(er.gate_contract(g)[0] == "latent", "the same gate over a one-target classifier is latent")
    (g / "scripts" / "classify_e2e.py").write_text(SURFACE_CLASSIFIER, encoding="utf-8")
    (g / "scripts" / "verify-before-ship.ps1").write_text(HA_AFTER, encoding="utf-8")
    check(er.gate_contract(g)[0] == "ok", "a split read is ok")
    (g / "scripts" / "verify-before-ship.ps1").write_text('$o = & $py "scripts/classify_e2e.py"\n& .\\scripts\\route.ps1 $o\n',
                                                          encoding="utf-8")
    (g / "scripts" / "route.ps1").write_text(INLINE, encoding="utf-8")
    ok2 = er.gate_contract(g)
    check(ok2[0] == "ok" and ok2[2] == [("scripts/route.ps1", "yes")],
          f"app-launcher's split across two scripts: the reader is found in the helper -- {ok2}")
    (g / "scripts" / "route.ps1").unlink()
    check(er.gate_contract(g)[0] == "unknown", "the classifier is run but nothing reads the key: unknown, never ok")

_h.report_and_exit("e2e_route")
