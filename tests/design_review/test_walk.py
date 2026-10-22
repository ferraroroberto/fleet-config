"""Unit tests for design_review's capture probe and walk: error classification, oversize captures, every walk_context arm and its golden browser trace (fleet-config#1085, #1244).

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/design_review/test_walk.py`  (also invoked by tests/run_acceptance.py)
"""
from __future__ import annotations

import json
import logging
import os
import socket
import sys
import tempfile
from pathlib import Path

from _suite import REPO, STATE, _h, check  # noqa: E402
from throwaway_dir import throwaway_dir  # noqa: E402

from design_review import capture, plan  # noqa: E402
from design_review_support import (  # noqa: E402
    FIX, rubric,
    light_c,
)


# ---- capture: liveness probe, interpreter, run dir ---------------------------

check(capture.probe_listening("file:///x.html")["status"] == "listening", "file:// is always listening")
# Windows surfaces the refusal after ~2s of SYN retries (see probe_listening); the default 5s covers it.
check(capture.probe_listening("http://127.0.0.1:1")["status"] == "NOT_LISTENING", "refused port -> NOT_LISTENING")
check(capture.probe_listening("nonsense")["status"] == "BAD_URL", "bad url -> BAD_URL")
_real = socket.create_connection


def _hang(*a, **k):
    raise socket.timeout("timed out")


socket.create_connection = _hang  # type: ignore[assignment]
try:
    probe_t = capture.probe_listening("http://127.0.0.1:8445", timeout=0.1)
finally:
    socket.create_connection = _real  # type: ignore[assignment]
check(probe_t["status"] == "TIMEOUT" and "did not answer" in str(probe_t["detail"]), "connect timeout -> TIMEOUT, distinct wording")

tmp = Path(throwaway_dir("design-review-capture-"))
no_venv = plan.Target(name="x", root=tmp / "absent", base_url="http://127.0.0.1:1")
check(capture.resolve_interpreter(no_venv)["status"] == "PLAYWRIGHT_MISSING", "no .venv -> PLAYWRIGHT_MISSING")
stdlib_only = capture.resolve_interpreter(no_venv, sys.executable)
check(stdlib_only["status"] in ("ok", "PLAYWRIGHT_MISSING"), "probe returns a verdict for any interpreter")
if stdlib_only["status"] == "PLAYWRIGHT_MISSING":
    check("import playwright" in str(stdlib_only["detail"]), "PLAYWRIGHT_MISSING names the failed import")
rd = capture.run_dir_for("demo-app")
check(rd.is_dir() and rd.parent == STATE / "design-review" / "demo-app" and rd.name.endswith("Z"), "run dir under the hooks state dir")

down_doc = capture.measure_target(plan.Target(name="down", root=None, base_url="http://127.0.0.1:1"), rubric, light_c, ["desktop"])
check(down_doc["unmeasured"] == {"reason": "NOT_LISTENING", "detail": down_doc["unmeasured"]["detail"]} and down_doc["screens"] == []
      and (Path(down_doc["run_dir"]) / "metrics.json").is_file(), "measure_target on a dead port writes an unmeasured metrics.json")
check({"schema_version", "rubric_version", "target", "commit", "generated_at", "base_url", "run_dir", "devices",
       "params", "unmeasured", "screens"} <= set(down_doc), "metrics.json envelope keys")

# ---- walk.classify_error (no Playwright needed) -------------------------------

sys.path.insert(0, str(REPO / "skills" / "_lib" / "design_review"))
import walk  # noqa: E402

check(walk.classify_error(TimeoutError("Timeout 15000ms exceeded")) == "TIMEOUT", "timeout classified")
check(walk.classify_error(RuntimeError("net::ERR_CONNECTION_REFUSED at https://127.0.0.1:8445")) == "NOT_LISTENING", "refused classified")
check(walk.classify_error(RuntimeError("Element is not visible")) == "RENDER_FAILED", "other -> RENDER_FAILED")

# ---- walk: a full-page capture over the engine's size limit keeps the screen's metrics (#1085) ----

_TOO_TALL = "Page.screenshot: Cannot take screenshot larger than 32767 pixels on any dimension"


class _FakePage:
    """A Playwright page stub: one tab-less root screen; `full` decides how a full-page capture behaves."""

    def __init__(self, full: str) -> None:
        self.full, self.shots = full, []

    def set_default_timeout(self, _ms): pass
    def goto(self, *_a, **_k): pass
    def wait_for_timeout(self, _ms): pass
    def on(self, *_a): pass
    def locator(self, _sel): return type("L", (), {"count": lambda _self: 0})()

    def evaluate(self, js, *_args):
        if js is walk._TABS_JS or js is walk._DIALOG_IDS_JS:
            return []
        if js is walk._OPEN_DETAILS_JS:
            return 1
        if "devicePixelRatio" in js:
            return {"dpr": 3, "width": 390, "height": 11338}
        return {"measured": True} if js == "MEASURE" else None

    def screenshot(self, path, full_page=False, clip=None):
        self.shots.append({"full_page": full_page, "clip": clip})
        if full_page and (self.full == "raise" or (self.full == "clip" and clip is None)):
            raise RuntimeError(_TOO_TALL)


def _walk_one(full: str):
    page = _FakePage(full)
    ctx = type("C", (), {"add_init_script": lambda *_: None, "new_page": lambda _s: page, "close": lambda _s: None})()
    browser = type("B", (), {"new_context": lambda _s, **_k: ctx, "close": lambda _s: None})()
    pw = type("P", (), {"webkit": type("E", (), {"launch": lambda _s: browser})(), "devices": {"iPhone 13": {}}})()
    args = type("A", (), {"url": "https://127.0.0.1:1", "timeout_ms": 1000, "synthetic": False})()
    with tempfile.TemporaryDirectory() as tmp:
        screens = walk.walk_context(pw, "iphone", "light", args, "MEASURE", {}, {}, Path(tmp))
    # The stub has no Settings gear to find (the Settings step is covered by the browser leg below, #1217).
    return [s for s in screens if s["view"] != plan.SETTINGS_GEAR_VIEW], page


for _mode in ("raise", "clip"):
    (_scr,), _pg = _walk_one(_mode)
    check(_scr["status"] == "ok" and {k: v for k, v in _scr["metrics"].items() if k != "glance"} == {"measured": True},
          f"a full-page capture over the 32767px limit ({_mode}) keeps the screen ok with its metrics -- {_scr}")
_scr_raise = _walk_one("raise")[0][0]
check(_scr_raise["screenshot_full"] is None and "skipped" in (_scr_raise.get("note") or ""),
      f"a full-page capture that can't be taken even clipped is skipped with a note -- {_scr_raise}")
_scr_clip, _pg_clip = _walk_one("clip")
_scr_clip = _scr_clip[0]
check(_scr_clip["screenshot_full"] == "iphone-light-root-full.png" and "clipped" in (_scr_clip.get("note") or "")
      and _pg_clip.shots[-1]["clip"] == {"x": 0, "y": 0, "width": 390, "height": 32767 // 3},
      f"a too-tall page is captured clipped to the limit's first device px, with a note -- {_scr_clip} {_pg_clip.shots}")
_scr_ok = _walk_one("ok")[0][0]
check(_scr_ok["status"] == "ok" and _scr_ok["screenshot_full"] == "iphone-light-root-full.png" and _scr_ok.get("note") is None,
      f"a page within the limit is captured whole, without a note -- {_scr_ok}")

# ---- walk: every ok/error/absent arm of walk_context, records + log lines (#1244) ----


class _ArmLoc:
    """A Playwright locator stub: actions on `sel` raise whatever the page's `fail` map names."""

    def __init__(self, page, sel: str) -> None:
        self.page, self.sel = page, sel

    first = property(lambda self: self)
    def count(self): return 1
    def nth(self, i): return _ArmLoc(self.page, f"{self.sel}#{i}")
    def locator(self, sel): return _ArmLoc(self.page, sel) if self.sel == "[role=tablist]" else self
    def wait_for(self, **_k): self.page.act("wait", self.sel)
    def click(self): self.page.act("click", self.sel)

    def evaluate(self, js, *_args):
        return self.sel in self.page.no_go_hits if js is walk._NO_GO_JS else None


class _ArmPage:
    """Three tabs (one fine, one broken, one timing out), two dialogs (one won't open), a scripted gear."""

    def __init__(self, fail: dict, no_go_hits: set, goto_error=None) -> None:
        self.fail, self.no_go_hits, self.goto_error = fail, no_go_hits, goto_error

    def act(self, kind, sel):
        if (kind, sel) in self.fail:
            raise self.fail[(kind, sel)]

    def set_default_timeout(self, _ms): pass
    def wait_for_timeout(self, _ms): pass
    def on(self, *_a): pass
    def screenshot(self, **_k): pass
    def locator(self, sel): return _ArmLoc(self, sel)
    def get_by_role(self, *_a, **_k): return _ArmLoc(self, "gear")

    def goto(self, *_a, **_k):
        if self.goto_error:
            raise self.goto_error

    def evaluate(self, js, *args):
        if js is walk._TABS_JS:
            return [{"index": 0, "id": "home"}, {"index": 1, "id": "broken"}, {"index": 2, "id": "slow"}]
        if js is walk._DIALOG_IDS_JS:
            return ["fine", "stuck"]
        if "showModal" in js and args == ("stuck",):
            raise RuntimeError("dialog stuck would not open")
        if js is walk.measure.RENDERED_THEME_JS:
            return {"attr": "light", "luminance": 0.95}
        return {"m": 1} if js == "MEASURE" else 0


_ARM_STEPS = [
    {"id": "declared", "click": "#danger"},
    {"id": "gone", "click": "#gone"},
    {"id": "inside", "click": "#inside"},
    {"id": "crash", "click": "#crash"},
    {"id": "waiterr", "click": "#waiterr"},
    {"id": "fine", "tab": "home", "open": "#det", "clicks": ["#a", "#b"]},
    {"id": "live-only", "click": "#x", "synthetic": True},
    {"id": "noop"},
]
_ARM_FAIL = {
    ("click", "[role=tab]#1"): RuntimeError("Element is not visible"),
    ("click", "[role=tab]#2"): TimeoutError("Timeout 1000ms exceeded"),
    ("wait", "#gone"): TimeoutError("Timeout 5000ms exceeded"),
    ("click", "#crash"): RuntimeError("boom"),
    ("wait", "#waiterr"): RuntimeError("element detached"),
}
_GEAR_MODES = {
    "ok": ({}, set()),
    "absent": ({("wait", "gear"): TimeoutError("Timeout 1000ms exceeded")}, set()),
    "nogo": ({}, {"gear"}),
    "crash": ({("click", "gear"): RuntimeError("gear click failed")}, set()),
}


class _LogTap(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(f"{record.levelname} {record.getMessage()}")


def _walk_arms(gear: str = "ok", launch_error=None, goto_error=None):
    fail, hits = _GEAR_MODES[gear]
    page = _ArmPage({**_ARM_FAIL, **fail}, hits | {"#inside"}, goto_error)
    ctx = type("C", (), {"add_init_script": lambda *_: None, "new_page": lambda _s: page, "close": lambda _s: None})()
    browser = type("B", (), {"new_context": lambda _s, **_k: ctx, "close": lambda _s: None})()

    def launch(_s):
        if launch_error:
            raise launch_error
        return browser
    pw = type("P", (), {"chromium": type("E", (), {"launch": launch})(), "devices": {}})()
    args = type("A", (), {"url": "https://127.0.0.1:1", "timeout_ms": 1000, "synthetic": False})()
    tap, wlog = _LogTap(), logging.getLogger("design_review.walk")
    level = wlog.level
    wlog.addHandler(tap)
    wlog.setLevel(logging.INFO)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            screens = walk.walk_context(pw, "desktop", "light", args, "MEASURE", {},
                                        {"no_go": ["#danger"], "extra_steps": _ARM_STEPS}, Path(tmp))
    finally:
        wlog.removeHandler(tap)
        wlog.setLevel(level)
    return [(s["id"], s["kind"], s["status"], s["reason"], s["error"], s["metrics"], s["theme_applied"]) for s in screens], tap.lines


_arm_screens, _arm_log = _walk_arms("ok")
check(_arm_screens == [
    ("desktop-light-home", "tab", "ok", None, None, {"m": 1, "glance": {"state": "unavailable", "idle": True, "idle_ms": 400}}, True),
    ("desktop-light-broken", "tab", "error", "TAB_FAILED", "Element is not visible", None, None),
    ("desktop-light-slow", "tab", "error", "TIMEOUT", "Timeout 1000ms exceeded", None, None),
    ("desktop-light-dialog-fine", "dialog", "ok", None, None, {"m": 1}, True),
    ("desktop-light-dialog-stuck", "dialog", "error", "DIALOG_FAILED", "dialog stuck would not open", None, None),
    ("desktop-light-gear-settings", "step", "ok", None, None, {"m": 1}, True),
    ("desktop-light-root-declared", "step", "error", "NO_GO", "#danger is declared no_go", None, None),
    ("desktop-light-root-gone", "step", "absent", "STEP_TARGET_ABSENT", "#gone never appeared within 5000 ms", None, None),
    ("desktop-light-root-inside", "step", "error", "NO_GO", "#inside sits inside a no_go selector", None, None),
    ("desktop-light-root-crash", "step", "error", "RENDER_FAILED", "boom", None, None),
    ("desktop-light-root-waiterr", "step", "error", "RENDER_FAILED", "element detached", None, None),
    ("desktop-light-home-fine", "step", "ok", None, None, {"m": 1}, True),
], f"walk_context records every tab/dialog/step arm with its own status and reason -- {_arm_screens}")
check(_arm_log == [
    "INFO ok desktop-light-home",
    "WARNING FAIL desktop-light-broken: Element is not visible",
    "WARNING FAIL desktop-light-slow: Timeout 1000ms exceeded",
    "INFO ok desktop-light-dialog-fine",
    "WARNING FAIL desktop-light-dialog-stuck: dialog stuck would not open",
    "INFO ok desktop-light-gear-settings",
    "INFO absent desktop-light-root-gone: #gone never appeared within 5000 ms",
    "WARNING NO_GO desktop-light-root-inside: #inside sits inside a no_go selector",
    "WARNING FAIL desktop-light-root-crash: boom",
    "WARNING FAIL desktop-light-root-waiterr: element detached",
    "INFO ok desktop-light-home-fine",
], f"walk_context logs one line per arm, at its own level -- {_arm_log}")
_gear_arms = {}
for _mode in ("absent", "nogo", "crash"):
    _scr, _lines = _walk_arms(_mode)
    _gear_arms[_mode] = ([s for s in _scr if s[0] == "desktop-light-gear-settings"], [l for l in _lines if "gear-settings" in l])
check(_gear_arms == {
    "absent": ([("desktop-light-gear-settings", "step", "absent", "SETTINGS_GEAR_ABSENT", "no visible button named Settings", None, None)],
               ["INFO absent desktop-light-gear-settings: no visible button named Settings"]),
    "nogo": ([("desktop-light-gear-settings", "step", "error", "NO_GO", "the Settings gear sits inside a no_go selector", None, None)],
             ["WARNING NO_GO desktop-light-gear-settings: the Settings gear sits inside a no_go selector"]),
    "crash": ([("desktop-light-gear-settings", "step", "error", "RENDER_FAILED", "gear click failed", None, None)],
              ["WARNING FAIL desktop-light-gear-settings: gear click failed"]),
}, f"the Settings gear's absent / no_go / failed arms -- {_gear_arms}")
check(_walk_arms(launch_error=RuntimeError("no chromium")) == (
    [("desktop-light-root", "tab", "error", "BROWSER_FAILED", "no chromium", None, None)], []),
      "a browser that won't launch is one BROWSER_FAILED root screen, nothing logged")
check(_walk_arms(goto_error=RuntimeError("net::ERR_CONNECTION_REFUSED")) == (
    [("desktop-light-root", "tab", "error", "NOT_LISTENING", "net::ERR_CONNECTION_REFUSED", None, None)], []),
      "a base page that won't load is one classified root screen, nothing logged")

# ---- walk: every browser action of every walk_context path, against a golden trace (#1244) ----
# The records and log lines above can't see the order of gotos, clicks, waits, captures and
# evaluates, so a restructure could reorder what a browser sees and still pass them. Each
# scenario here records every call the walk makes on the playwright/browser/context/page/locator
# fakes, plus the full screen records and log lines, and must equal the golden captured from the
# code before the per-leg split. Regenerate only for an intended behaviour change:
# DESIGN_REVIEW_WRITE_WALK_GOLDEN=1.

WALK_GOLDEN = FIX / "walk_trace.json"
_TRACE_JS = {value: name for name, value in vars(walk).items() if name.endswith("_JS") and isinstance(value, str)}
_TRACE_JS[walk.measure.RENDERED_THEME_JS] = "measure.RENDERED_THEME_JS"
for _gl_name in ("GLANCE_TRACK_JS", "GLANCE_READ_JS"):  # the glance tracker (#1354); a source without it fails the golden, never crashes here
    if hasattr(walk.measure, _gl_name):
        _TRACE_JS[getattr(walk.measure, _gl_name)] = f"measure.{_gl_name}"


class _TraceLoc:
    """A locator stub that logs every call to the page's trace."""

    def __init__(self, page, sel: str) -> None:
        self.page, self.sel = page, sel

    @property
    def first(self):
        self.page.log("first", self.sel)
        return self

    def count(self):
        self.page.log("count", self.sel)
        return 1 if self.page.has_tablist or self.sel != "[role=tablist]" else 0

    def nth(self, i):
        self.page.log("nth", self.sel, i)
        return _TraceLoc(self.page, f"{self.sel}#{i}")

    def locator(self, sel):
        self.page.log("loc.locator", self.sel, sel)
        return _TraceLoc(self.page, sel if self.sel == "[role=tablist]" else self.sel)

    def wait_for(self, **kwargs):
        self.page.log("wait_for", self.sel, kwargs)
        self.page.act("wait", self.sel)

    def click(self):
        self.page.log("click", self.sel)
        self.page.act("click", self.sel)

    def evaluate(self, js, *args):
        self.page.log("loc.evaluate", self.sel, _TRACE_JS.get(js, js), list(args))
        return self.sel in self.page.no_go_hits if js is walk._NO_GO_JS else None


class _TracePage:
    """A page stub that logs every call; the scenario decides what the page holds and what fails."""

    def __init__(self, trace: list, cfg: dict) -> None:
        self.trace, self.cfg = trace, cfg
        self.fail = cfg.get("fail", {})
        self.no_go_hits = cfg.get("no_go_hits", set())
        self.has_tablist = cfg.get("has_tablist", True)

    def log(self, *entry):
        self.trace.append(list(entry))

    def act(self, kind, sel):
        if (kind, sel) in self.fail:
            raise self.fail[(kind, sel)]

    def set_default_timeout(self, ms): self.log("set_default_timeout", ms)
    def wait_for_timeout(self, ms): self.log("wait_for_timeout", ms)
    def on(self, event, _handler): self.log("on", event)

    def locator(self, sel):
        self.log("locator", sel)
        return _TraceLoc(self, sel)

    def get_by_role(self, role, name=None):
        self.log("get_by_role", role, getattr(name, "pattern", name))
        return _TraceLoc(self, "gear")

    def goto(self, url, **kwargs):
        self.log("goto", url, kwargs)
        if self.cfg.get("goto_error"):
            raise self.cfg["goto_error"]

    def screenshot(self, path, full_page=False, clip=None):
        self.log("screenshot", Path(path).name, full_page, clip)
        full = self.cfg.get("full", "ok")
        if full_page and (full == "raise" or (full == "clip" and clip is None)):
            raise RuntimeError(_TOO_TALL)

    def evaluate(self, js, *args):
        name = _TRACE_JS.get(js, js)
        self.log("evaluate", name, list(args))
        if name == "_TABS_JS":
            return self.cfg.get("tabs", [])
        if name == "_DIALOG_IDS_JS":
            return self.cfg.get("dialogs", [])
        if name in ("_OPEN_DETAILS_JS", "_OPEN_SCOPE_DETAILS_JS"):
            return self.cfg.get("opened", 0)
        if name == "_PAGE_SIZE_JS":
            return {"dpr": 3, "width": 390, "height": 40000}
        if name == "measure.GLANCE_READ_JS":
            return self.cfg.get("glance")
        if name == "measure.RENDERED_THEME_JS":
            if self.cfg.get("theme_read") == "raise":
                raise RuntimeError("page closed")
            return self.cfg.get("rendered", {"attr": "light", "luminance": 0.95})
        if "showModal" in js and args and args[0] in self.cfg.get("stuck_dialogs", ()):
            raise RuntimeError(f"dialog {args[0]} would not open")
        return {"m": 1} if js == "MEASURE" else None


def _walk_traced(cfg: dict) -> dict:
    trace: list = []
    page = _TracePage(trace, cfg)

    class _Ctx:
        def add_init_script(self, script): trace.append(["ctx.add_init_script", _TRACE_JS.get(script, script)])
        def close(self): trace.append(["ctx.close"])

        def new_page(self):
            trace.append(["ctx.new_page"])
            return page

        def new_cdp_session(self, _page):
            trace.append(["ctx.new_cdp_session"])
            return type("Cdp", (), {"send": lambda _s, method, params: trace.append(["cdp.send", method, params])})()

    class _Browser:
        def close(self): trace.append(["browser.close"])

        def new_context(self, **kwargs):
            trace.append(["browser.new_context", kwargs])
            return _Ctx()

    class _Engine:
        def __init__(self, engine: str) -> None:
            self.engine = engine

        def launch(self):
            trace.append(["launch", self.engine])
            if cfg.get("launch_error"):
                raise cfg["launch_error"]
            return _Browser()

    pw = type("P", (), {"webkit": _Engine("webkit"), "chromium": _Engine("chromium"),
                        "devices": {"iPhone 13": {"viewport": {"width": 390, "height": 664}, "has_touch": True},
                                    "Pixel 7": {"viewport": {"width": 412, "height": 839}, "has_touch": True}}})()
    args = type("A", (), {"url": "https://127.0.0.1:1", "timeout_ms": 1000, "synthetic": cfg.get("synthetic", False)})()
    tap, wlog = _LogTap(), logging.getLogger("design_review.walk")
    level = wlog.level
    wlog.addHandler(tap)
    wlog.setLevel(logging.INFO)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            screens = walk.walk_context(pw, cfg["device"], cfg["theme"], args, "MEASURE", {"p": 1},
                                        cfg.get("review", {}), Path(tmp))
    finally:
        wlog.removeHandler(tap)
        wlog.setLevel(level)
    # A JSON round trip, so the in-memory result compares equal to the golden file's.
    return json.loads(json.dumps({"screens": screens, "log": tap.lines, "trace": trace}))


_ARMS_PAGE = {"tabs": [{"index": 0, "id": "home"}, {"index": 1, "id": "broken"}, {"index": 2, "id": "slow"}],
              "dialogs": ["fine", "stuck"], "stuck_dialogs": ("stuck",), "no_go_hits": {"#inside"}, "fail": _ARM_FAIL,
              "review": {"no_go": ["#danger"], "extra_steps": _ARM_STEPS}}
_TRACE_SCENARIOS = {
    "arms": {**_ARMS_PAGE, "device": "desktop", "theme": "light"},
    "gear-absent": {**_ARMS_PAGE, "device": "desktop", "theme": "light",
                    "fail": {**_ARM_FAIL, ("wait", "gear"): TimeoutError("Timeout 1000ms exceeded")}},
    "gear-nogo": {**_ARMS_PAGE, "device": "desktop", "theme": "light", "no_go_hits": {"#inside", "gear"}},
    "gear-crash": {**_ARMS_PAGE, "device": "desktop", "theme": "light",
                   "fail": {**_ARM_FAIL, ("click", "gear"): RuntimeError("gear click failed")}},
    "gear-wait-error": {**_ARMS_PAGE, "device": "desktop", "theme": "light",
                        "fail": {**_ARM_FAIL, ("wait", "gear"): RuntimeError("gear detached")}},
    "launch-error": {"device": "desktop", "theme": "dark", "launch_error": RuntimeError("no chromium")},
    "goto-error": {"device": "iphone", "theme": "light", "goto_error": RuntimeError("net::ERR_CONNECTION_REFUSED")},
    # Touch restore after every full-page capture, a stored theme, a custom tab selector, an app that
    # re-applied its own theme, clipped full captures, a step whose tab is missing, one with only `open`.
    "android-touch": {"device": "android", "theme": "dark", "opened": 2, "full": "clip",
                      "rendered": {"attr": "light", "luminance": 0.95},
                      "tabs": [{"index": 0, "id": "board"}, {"index": 1, "id": None}], "dialogs": ["edit"],
                      "review": {"theme_storage_key": "app-theme", "tab_selector": ".tab",
                                 "extra_steps": [{"id": "menu", "tab": "missing", "open": "#det"},
                                                 {"id": "pick", "tab": "board", "clicks": ["#a", ""]},
                                                 "not-a-step", {"click": "#no-id"}]}},
    # No tablist and no tabs: the root screen; full captures that fail even clipped; an unreadable theme.
    "iphone-root": {"device": "iphone", "theme": "dark", "has_tablist": False, "opened": 1, "full": "raise",
                    "theme_read": "raise"},
    # A synthetic run swaps in its own no_go and runs the synthetic-only steps.
    "synthetic": {"device": "desktop", "theme": "dark", "synthetic": True,
                  "tabs": [{"index": 0, "id": "home"}],
                  "review": {"no_go": ["#live"], "synthetic": {"no_go": ["#synthdanger"]},
                             "extra_steps": [{"id": "s1", "click": "#synthdanger", "synthetic": True},
                                             {"id": "s2", "click": "#live", "synthetic": True},
                                             {"id": "s3", "click": "#ok"}]}},
    # A slow target declares its readiness (#1385): a tab `ready` selector that attaches, one that never does
    # (the tab is still captured, `met: false`), a tab with none; per-step `wait_ms` honoured, capped, or
    # ignored when it is not a positive integer. The `gone` step stays absent after its longer bound.
    "ready-and-wait": {"device": "desktop", "theme": "light",
                       "tabs": [{"index": 0, "id": "machines"}, {"index": 1, "id": "slow"}, {"index": 2, "id": "plain"}],
                       "fail": {("wait", "#gone"): TimeoutError("Timeout 12000ms exceeded"),
                                ("wait", ".slow-ready"): TimeoutError("Timeout 15000ms exceeded")},
                       "review": {"tab_ready": {"machines": ".row", "slow": ".slow-ready"},
                                  "extra_steps": [{"id": "long", "tab": "machines", "click": "#sheet", "wait_ms": 12000},
                                                  {"id": "gone", "click": "#gone", "wait_ms": 12000},
                                                  {"id": "huge", "click": "#h", "wait_ms": 9999999},
                                                  {"id": "neg", "click": "#n", "wait_ms": -5},
                                                  {"id": "flag", "click": "#f", "wait_ms": True},
                                                  {"id": "none", "click": "#z"}]}},
    # A live run skips synthetic steps and keeps the live no_go even when a synthetic one is declared.
    "live-with-synthetic-block": {"device": "desktop", "theme": "light",
                                  "review": {"no_go": ["#live"], "synthetic": {"no_go": ["#synthdanger"]},
                                             "extra_steps": [{"id": "s1", "click": "#synthdanger", "synthetic": True},
                                                             {"id": "s3", "click": "#live"}]}},
}

_walk_traces = {name: _walk_traced(cfg) for name, cfg in _TRACE_SCENARIOS.items()}
if os.environ.get("DESIGN_REVIEW_WRITE_WALK_GOLDEN") == "1":
    WALK_GOLDEN.write_text(json.dumps(_walk_traces, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
_walk_golden = json.loads(WALK_GOLDEN.read_text(encoding="utf-8"))
check(set(_walk_traces) == set(_walk_golden), f"walk trace scenarios match the golden's -- {sorted(_walk_traces)}")
for _name in _TRACE_SCENARIOS:
    for _part in ("screens", "log", "trace"):
        _got, _want = _walk_traces[_name][_part], _walk_golden.get(_name, {}).get(_part)
        _at = next((i for i, (g, w) in enumerate(zip(_got, _want or [])) if g != w), min(len(_got), len(_want or [])))
        check(_got == _want, f"walk_context {_name}: {_part} matches the golden (first difference at #{_at}: "
                             f"got {_got[_at:_at + 1]} want {(_want or [])[_at:_at + 1]})")

# The readiness declarations (#1385), asserted directly so the golden is not the only witness.
_rw = _walk_traces["ready-and-wait"]
_rw_by = {sc["view"]: sc for sc in _rw["screens"]}
_rw_waits = [e[2] for e in _rw["trace"] if e[0] == "wait_for" and e[1] in ("#sheet", "#gone", "#h", "#n", "#f", "#z")]
_rw_clicks = [e[1] for e in _rw["trace"] if e[0] == "click" and not e[1].startswith("[role=tab]")]
check(_rw_by["machines"]["ready"] == {"selector": ".row", "wait_ms": walk.READY_WAIT_MS, "met": True}
      and _rw_by["slow"]["ready"] == {"selector": ".slow-ready", "wait_ms": walk.READY_WAIT_MS, "met": False}
      and _rw_by["slow"]["status"] == "ok" and "ready" not in _rw_by["plain"],
      f"a tab `ready` selector is recorded met or not met (the tab still captured), and absent when undeclared -- {[(v, sc.get('ready')) for v, sc in _rw_by.items()]}")
check([w["timeout"] for w in _rw_waits] == [12000, 12000, walk.WAIT_MS_MAX, walk.STEP_TARGET_WAIT_MS, walk.STEP_TARGET_WAIT_MS, walk.STEP_TARGET_WAIT_MS]
      and _rw_by["machines-long"]["wait_ms"] == 12000 and _rw_by["root-huge"]["wait_ms"] == walk.WAIT_MS_MAX
      and "wait_ms" not in _rw_by["root-none"],
      f"step `wait_ms` replaces the 5 s wait, is capped at WAIT_MS_MAX, is ignored unless a positive integer, and is recorded only when declared -- {_rw_waits}")
check(_rw_by["root-gone"]["status"] == "absent" and _rw_by["root-gone"]["reason"] == "STEP_TARGET_ABSENT"
      and "12000 ms" in _rw_by["root-gone"]["error"] and _rw_clicks.count("#gone") == 0,
      f"a target absent after its longer bound stays absent, and its click is never made -- {_rw_by['root-gone']}")
check(all(_rw_clicks.count(c) == 1 for c in ("#sheet", "#h", "#n", "#f", "#z")),
      f"a longer wait never retries a click -- {_rw_clicks}")
check([walk.bounded_wait_ms(v, 5000) for v in (None, 0, -1, "9", 1.5, False, 100, walk.WAIT_MS_MAX, walk.WAIT_MS_MAX + 1)]
      == [5000, 5000, 5000, 5000, 5000, 5000, 100, walk.WAIT_MS_MAX, walk.WAIT_MS_MAX],
      "bounded_wait_ms: defaults unless a positive int, caps at WAIT_MS_MAX")

# ---- walk: the glance card's late reads wait for a quiet network, bounded (#1354) ----------------------------------


class _IdlePage:
    """Records the waits and the request listeners; never a real browser."""

    def __init__(self) -> None:
        self.handlers, self.waited = {}, 0

    def on(self, event, handler): self.handlers[event] = handler
    def wait_for_timeout(self, ms): self.waited += ms


_idle_req = lambda url, kind="fetch": type("Req", (), {"url": url, "resource_type": kind})()
check(hasattr(walk, "_Inflight"), "walk counts the requests in flight for the glance read (#1354)")
if hasattr(walk, "_Inflight"):
    _ip = _IdlePage()
    _inflight = walk._Inflight(_ip)
    _ip_read = _idle_req("https://127.0.0.1:9/api/late?token=x")
    _ip.handlers["request"](_idle_req("https://127.0.0.1:9/api/stream", "eventsource"))
    _ip.handlers["request"](_idle_req("wss://127.0.0.1:9/ws", "websocket"))
    _ip.handlers["request"](_ip_read)
    check(_inflight.wait_quiet(_ip) == {"idle": False, "idle_ms": walk.IDLE_WAIT_MS, "pending": ["/api/late"]} and _ip.waited == walk.IDLE_WAIT_MS,
          f"a read still in flight holds the glance read for IDLE_WAIT_MS and no longer, naming its path without host or query -- waited {_ip.waited}")
    _ip.handlers["requestfinished"](_ip_read)
    _ip.waited = 0
    _ip_quiet = _inflight.wait_quiet(_ip)
    check(_ip_quiet["idle"] is True and _ip_quiet["idle_ms"] < walk.IDLE_QUIET_MS and _ip.waited < walk.IDLE_QUIET_MS,
          f"an open EventSource or WebSocket is not a read in flight: quiet after IDLE_QUIET_MS -- {_ip_quiet}")
    _ip.handlers["request"](_ip_read)
    _ip.handlers["requestfailed"](_ip_read)
    check(_inflight.wait_quiet(_ip)["idle"] is True, "a failed request is no longer in flight")

_h.report_and_exit("test_walk")
