"""Unit tests for the pure logic in skills/_lib/browser_verify.py (fleet-config#351).

No live browser / app — these exercise backend selection, the browser-safety
launch kwargs, the KEY_VIEWS x light/dark capture plan, the distinct capability
failures, and the venv/probe command construction.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/test_browser_verify.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "_lib"))
import browser_verify as bv  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check


# ---- backend selection (iab preferred, Playwright fallback) ----

check(bv.choose_backend(True) == bv.BACKEND_IAB, "iab available -> iab preferred")
check(bv.choose_backend(False) == bv.BACKEND_PLAYWRIGHT, "iab absent -> playwright fallback")
check(bv.BACKEND_IAB == "iab" and bv.BACKEND_PLAYWRIGHT == "playwright", "backend constants stable")


# ---- browser-safety launch kwargs (the stealth contract, as data) ----

kw = bv.playwright_launch_kwargs("C:/tmp/prof")
check(kw["channel"] == "chrome", "real Chrome, not bundled Chromium")
check(kw["user_data_dir"] == "C:/tmp/prof", "persistent profile dir threaded through")
check(kw["viewport"] == {"width": 1280, "height": 900}, "viewport 1280x900")
check(kw["headless"] is False, "headed launch")
check("--enable-automation" in kw["ignore_default_args"], "--enable-automation stripped")
check("--enable-blink-features=IdleDetection" in kw["ignore_default_args"], "IdleDetection stripped")
check("--disable-blink-features=AutomationControlled" in kw["args"], "AutomationControlled arg present")
check(all(a in kw["args"] for a in ("--disable-features=Translate", "--no-default-browser-check", "--no-first-run")),
      "translate/first-run/browser-check flags present")
check("navigator" in bv.WEBDRIVER_INIT_SCRIPT and "webdriver" in bv.WEBDRIVER_INIT_SCRIPT
      and "undefined" in bv.WEBDRIVER_INIT_SCRIPT,
      "webdriver init script hides navigator.webdriver")
# mutating a returned kwargs dict must not corrupt the module-level defaults
kw["args"].append("--mutated")
check("--mutated" not in bv.playwright_launch_kwargs("x")["args"], "launch kwargs return fresh copies")


# ---- capture plan: every KEY_VIEWS entry x {light, dark} ----

plan = bv.capture_plan(["/", "/settings"], "http://127.0.0.1:8501/", "/scratch")
check(len(plan) == 4, "2 views x 2 themes -> 4 captures")
check([p["theme"] for p in plan] == ["light", "dark", "light", "dark"], "light then dark per view")
check([p["view"] for p in plan] == ["/", "/", "/settings", "/settings"], "views kept in order")
check(plan[0]["url"] == "http://127.0.0.1:8501/", "root url, trailing slash normalized")
check(plan[2]["url"] == "http://127.0.0.1:8501/settings", "sub-view url joined")
check(plan[0]["screenshot"].endswith("root-light.png"), "root view slugs to 'root'")
check(plan[2]["screenshot"].endswith("settings-light.png"), "'/settings' slugs to 'settings'")
check(Path(plan[0]["screenshot"]).parent == Path("/scratch"), "screenshots land under scratch dir")
check(bv.capture_plan([], "http://x", "/s") == [], "no views -> empty plan")
# a view given without a leading slash is still joined correctly
check(bv.capture_plan(["home"], "http://x", "/s")[0]["url"] == "http://x/home", "slashless view joined")
check(bv._slug("/deep/nested/view") == "deep-nested-view", "nested path slug is fs-safe")


# ---- distinct, actionable capability failures ----

expected_codes = {
    "IAB_UNAVAILABLE", "PLAYWRIGHT_MISSING", "CHROME_MISSING",
    "APP_UNREACHABLE", "PROFILE_LOCK_EXHAUSTED", "RENDER_FAILED",
}
check(set(bv.FAILURES) == expected_codes, "all four+2 failure codes present")
messages = [bv.FAILURES[c] for c in expected_codes]
check(len(set(messages)) == len(messages), "every failure message is distinct")
check(all(bv.FAILURES[c].strip() for c in expected_codes), "no empty failure message")
# APP_UNREACHABLE carries the base_url placeholder, filled by failure_message
check("<base_url>" in bv.FAILURES["APP_UNREACHABLE"], "APP_UNREACHABLE has a base_url placeholder")
filled = bv.failure_message("APP_UNREACHABLE", base_url="http://127.0.0.1:9999")
check("http://127.0.0.1:9999" in filled and "<base_url>" not in filled, "failure_message fills base_url")
# the four the issue names must each read distinctly (missing playwright != missing chrome, etc.)
check("Playwright" in bv.FAILURES["PLAYWRIGHT_MISSING"] and "Chromium" not in bv.FAILURES["PLAYWRIGHT_MISSING"],
      "PLAYWRIGHT_MISSING is about the dependency")
check("Chrome" in bv.FAILURES["CHROME_MISSING"] and "Chromium" in bv.FAILURES["CHROME_MISSING"],
      "CHROME_MISSING distinguishes real Chrome from Chromium")
check("kill" in bv.FAILURES["PROFILE_LOCK_EXHAUSTED"].lower() and "backoff" in bv.FAILURES["PROFILE_LOCK_EXHAUSTED"].lower(),
      "PROFILE_LOCK_EXHAUSTED warns against killing the holder + names backoff")
check(bv.PROFILE_LOCK_BACKOFF == (60, 120, 240, 480), "backoff schedule matches the shared-profile rule")
# an unknown code is a hard error, not a silent pass
try:
    bv.failure_message("NOPE")
    _raised = False
except KeyError:
    _raised = True
check(_raised, "unknown failure code raises KeyError")


# ---- venv discovery + probe command construction ----

check(bv.playwright_probe_cmd("py.exe") == [
    "py.exe", "-c", "import playwright; print(getattr(playwright, '__version__', 'unknown'))",
], "playwright probe cmd is deterministic")

_tmp = Path(tempfile.mkdtemp(prefix="bv-venv-"))
try:
    check(bv.discover_venv_python(str(_tmp)) is None, "no .venv -> None")
    scripts = _tmp / ".venv" / "Scripts"
    scripts.mkdir(parents=True)
    (scripts / "python.exe").write_text("", encoding="utf-8")
    check(bv.discover_venv_python(str(_tmp)) == scripts / "python.exe", "Windows .venv/Scripts/python.exe found")
finally:
    import shutil
    shutil.rmtree(_tmp, ignore_errors=True)

# POSIX layout is also discovered
_tmp2 = Path(tempfile.mkdtemp(prefix="bv-venv2-"))
try:
    binp = _tmp2 / ".venv" / "bin"
    binp.mkdir(parents=True)
    (binp / "python").write_text("", encoding="utf-8")
    check(bv.discover_venv_python(str(_tmp2)) == binp / "python", "POSIX .venv/bin/python found")
finally:
    import shutil
    shutil.rmtree(_tmp2, ignore_errors=True)


# ---- scratch dir is outside any repo (never committable) ----

sd = bv.scratch_dir_for("fleet-config")
check("fleet-config" in sd and Path(sd).name == "fleet-visual-fleet-config", "scratch dir named per repo")
check(Path(tempfile.gettempdir()) in Path(sd).parents, "scratch dir lives under system temp, not the repo")


_h.report_and_exit("browser_verify")
