"""The measure leg's orchestrator — resolve, probe, spawn the walk, assemble `metrics.json`.

Sits between `plan.py` (what to visit) and `walk.py` (the Playwright child)
and owns everything that is *not* browser code:

  * the **run directory**, outside every *tracked* tree:
    `<hooks state>/design-review/<target>/<UTC stamp>/` (the shared
    `hooks_state.state_dir()` root — `~/.claude/hooks/state/`, which on this
    host is a junction into the primary clone's gitignored `hooks/state/`;
    `CLAUDE_HOOKS_STATE_DIR` redirects it in tests). Screenshots land in
    `shots/` beside `metrics.json`; nothing is written into the target repo
    and nothing here can be committed. No retention sweep yet — a full
    app-launcher run leaves ~144 PNGs; #972/#974 own the policy;
  * the **browser interpreter**: the target repo's own `.venv` python
    (`browser_verify.discover_venv_python`), probed with
    `browser_verify.playwright_probe_cmd`. This repo's venv is stdlib-only by
    contract, so the walk always runs as a child of the target's interpreter.
    `PLAYWRIGHT_MISSING` (no venv, or `import playwright` fails there) is its
    own `unmeasured` reason;
  * the **liveness probe** before any browser starts: a TCP connect to the
    loopback port distinguishes `NOT_LISTENING` (refused) from `TIMEOUT`
    (no answer inside the deadline) — two different remediations, two
    different words;
  * the **vendored-component catalog** (#1497): project-scaffolding's
    `.fleet.toml` `[components]` table, read once from the scaffold checkout
    (`fleet_toml`, no cross-repo import) and turned into each web component's
    root and part classes, which ride into the script's params so the
    `inventory` section can tell a vendored control from a bespoke one. No
    scaffold or no catalog → `componentsError`, and every control reads
    `unknown`, never `bespoke`;
  * the **e2e leg** (`measure --e2e`, #1498): after the walk, the target's
    own e2e suite runs in the foreground under the same interpreter with
    project-scaffolding's vendored `surface()` hook armed, and every surface
    it captured is appended to `screens` as `<device>-<theme>-e2e-<surface>`
    (`source: "e2e"`), graded by the same rubric and counted by the same
    overlay-coverage line. Its outcome is the `e2e` record (`measure_e2e`),
    present only on an `--e2e` run;
  * the **envelope**: `schema_version`, `rubric_version`, `target`, `commit`
    (target HEAD via `git_run.run_git`), `generated_at` (UTC ISO),
    `base_url`, `run_dir`, `devices`, `params` (the floors the script
    measured with, and where each came from), `unmeasured` (null, or
    `{reason, detail}` when the whole run could not measure), and `screens`.

Every spawn passes `creationflags=NO_WINDOW`; every git call goes through
`git_run.run_git` (fleet-config#399, #677).

stdlib only.
"""
from __future__ import annotations

import dataclasses
import datetime as _dt
import hashlib
import json
import os
import queue
import re
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import browser_verify  # noqa: E402
import fleet_toml  # noqa: E402
import git_run  # noqa: E402
import hooks_state  # noqa: E402
from no_window import NO_WINDOW  # noqa: E402

from . import measure  # noqa: E402
from .evaluate import theme_mismatches  # noqa: E402
from .plan import (  # noqa: E402
    E2E_HOOK, THEMES, PlanError, Target, e2e_block, exclude_selectors, screen_id, synthetic_block,
)
from .rubric import Rubric, resolve_params  # noqa: E402

WALK_PY = Path(__file__).resolve().parent / "walk.py"
DEFAULT_SCAFFOLD = "E:/automation/project-scaffolding"
PROBE_TIMEOUT_S = 5.0
WALK_TIMEOUT_S = 1800.0
SYNTHETIC_STOP_S = 30.0   # a launcher gets this long to clean up after its stdin closes
SCHEMA_VERSION = measure.SCHEMA_VERSION


def utc_now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def iso(ts: _dt.datetime) -> str:
    return ts.astimezone(_dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def run_dir_for(target_name: str, now: Optional[_dt.datetime] = None) -> Path:
    """`<state>/design-review/<target>/<YYYYMMDDTHHMMSSZ>` — created, outside every tree."""
    stamp = (now or utc_now()).astimezone(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = hooks_state.state_dir() / "design-review" / target_name / stamp
    path.mkdir(parents=True, exist_ok=True)
    return path


def probe_listening(base_url: str, timeout: float = PROBE_TIMEOUT_S) -> Dict[str, Optional[str]]:
    """`{"status": "listening"|"NOT_LISTENING"|"TIMEOUT"|"BAD_URL", "detail": ...}`.

    A raw TCP connect, not an HTTP request: the question is only "is anything
    bound to that port". `file://` (static fixture pages) is always listening.

    Measured on this Windows host: a refused loopback connect surfaces as
    `WinError 10061` only after ~2.0s of SYN retransmits, so `timeout` must
    stay comfortably above that — a 2s probe reads every dead port as
    `TIMEOUT` and the two remediations collapse into one word.
    """
    parts = urlsplit(base_url)
    if parts.scheme == "file":
        return {"status": "listening", "detail": None}
    host, port = parts.hostname, parts.port
    if not host or not port:
        return {"status": "BAD_URL", "detail": f"no host/port in {base_url}"}
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return {"status": "listening", "detail": None}
    except ConnectionRefusedError as exc:
        return {"status": "NOT_LISTENING", "detail": f"{host}:{port} refused the connection ({exc})"}
    except socket.timeout:
        return {"status": "TIMEOUT", "detail": f"{host}:{port} did not answer within {timeout:g}s"}
    except OSError as exc:
        return {"status": "NOT_LISTENING", "detail": f"{host}:{port}: {exc}"}


def resolve_interpreter(target: Target, override: Optional[str] = None) -> Dict[str, Optional[str]]:
    """The interpreter that runs the walk, or a `PLAYWRIGHT_MISSING` verdict.

    `override` (tests, a non-fleet target) is probed the same way. The probe
    is `browser_verify.playwright_probe_cmd`, run with `NO_WINDOW`.
    """
    python: Optional[Path] = Path(override) if override else (
        browser_verify.discover_venv_python(str(target.root)) if target.root else None)
    if python is None or not Path(python).is_file():
        return {"python": None, "status": "PLAYWRIGHT_MISSING",
                "detail": f"no .venv interpreter under {target.root or '(no repo root)'}"}
    try:
        proc = subprocess.run(browser_verify.playwright_probe_cmd(str(python)), capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=60,
                              creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"python": str(python), "status": "PLAYWRIGHT_MISSING", "detail": f"probe failed: {exc}"}
    if proc.returncode != 0:
        return {"python": str(python), "status": "PLAYWRIGHT_MISSING",
                "detail": f"`import playwright` failed in {python}: {(proc.stderr or '').strip()[-300:]}"}
    return {"python": str(python), "status": "ok", "detail": None}


def target_commit(target: Target) -> Optional[str]:
    if not target.root:
        return None
    proc = git_run.run_git(["-C", str(target.root), "rev-parse", "HEAD"])
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


VENDORED_PREFIX = "app/webapp/static/_vendored/"
_CSS_COMMENT_RE = re.compile(r"/\*.*?\*/", re.S)
_CSS_STRING_RE = re.compile(r"\"[^\"]*\"|'[^']*'")
_CSS_ATTR_RE = re.compile(r"\[[^\]]*\]")
_CSS_PARENS_RE = re.compile(r"\([^()]*\)")
_CSS_CLASS_RE = re.compile(r"\.(-?[A-Za-z_][\w-]*)")


def _css_class_uses(css: str) -> Dict[str, bool]:
    """`{class: leads}` for every class that opens a compound selector in `css`.

    `leads` is True when the class is the first class of some selector (a
    leading `html[data-theme=dark]` or `:root` carries none, so it does not
    count): the component's root. A class only ever nested under another
    (`.toggle .knob`) is a part. A class chained after another in one compound
    (`.toggle.on`) is a state, never a marker. Function arguments, attribute
    values and strings are dropped first, so `:not(.x)` and `[href$=".css"]`
    name nothing."""
    uses: Dict[str, bool] = {}
    text = _CSS_ATTR_RE.sub("", _CSS_STRING_RE.sub("", _CSS_COMMENT_RE.sub("", css)))
    for prelude in re.findall(r"([^{};]*)\{", text):
        prelude = prelude.strip()
        if not prelude or prelude.startswith("@"):
            continue
        while True:
            stripped = _CSS_PARENS_RE.sub("", prelude)
            if stripped == prelude:
                break
            prelude = stripped
        for selector in prelude.split(","):
            leading = True
            for compound in re.split(r"[\s>+~]+", selector.strip()):
                first = _CSS_CLASS_RE.match(compound[compound.find("."):]) if "." in compound else None
                if not first:
                    continue
                uses[first.group(1)] = uses.get(first.group(1), False) or leading
                leading = False
    return uses


def vendored_components(scaffold_root: Optional[str]) -> Dict[str, object]:
    """The scaffold's web components as `{"components": {name: {"roots": [..], "parts": [..]}}, "error": None}`.

    Read from `<scaffold>/.fleet.toml`'s `[components]` table (the entries under
    `_vendored/`) and each component's own stylesheets. A class used by several
    components belongs to the one that leads a selector with it, else to the one
    whose name prefixes it (`icon-button` styled inside `modal`), else to none
    (`.icon`, `.app`): a class nobody owns marks no component. A component
    with no classes (`base`, `icons`) is left out. `{"components": None, "error":
    why}` when there is no scaffold checkout or no catalog to read."""
    if not scaffold_root:
        return {"components": None, "error": "no scaffold checkout"}
    root = Path(scaffold_root)
    data, error = fleet_toml.load_checked(root)
    if data is None:
        return {"components": None, "error": f"no catalog at {root / '.fleet.toml'}: {error or 'file not found'}"}
    table = data.get("components")
    if not isinstance(table, dict) or not table:
        return {"components": None, "error": f"no [components] table in {root / '.fleet.toml'}"}
    uses: Dict[str, Dict[str, bool]] = {}
    for name in sorted(table):
        src = table[name].get("src") if isinstance(table[name], dict) else None
        if not isinstance(src, str) or not src.startswith(VENDORED_PREFIX):
            continue
        for css in sorted((root / src).glob("*.css")):
            try:
                text = css.read_text(encoding="utf-8")
            except OSError:
                continue
            for cls, leads in _css_class_uses(text).items():
                by = uses.setdefault(cls, {})
                by[name] = by.get(name, False) or leads
    out: Dict[str, Dict[str, List[str]]] = {}
    for cls in sorted(uses):
        by = uses[cls]
        cands = [k for k, leads in by.items() if leads] or list(by)
        owner = cands[0] if len(cands) == 1 else max(
            (k for k in cands if cls == k or cls.startswith(k + "-")), key=len, default=None)
        if owner:
            out.setdefault(owner, {"roots": [], "parts": []})["roots" if by[owner] else "parts"].append(cls)
    if not out:
        return {"components": None, "error": f"no web components under {VENDORED_PREFIX} in {root / '.fleet.toml'}"}
    return {"components": {k: out[k] for k in sorted(out)}, "error": None}


def script_params(rubric: Rubric, spec_light: Dict[str, str], review: Optional[Dict[str, object]] = None,
                  catalog: Optional[Dict[str, object]] = None) -> Dict[str, object]:
    """`{"script": <what the page script receives>, "resolved": <rubric params>}`.

    `review` is the target's `[design.review]` block: its `exclude_selectors` ride into the script's params,
    so `metrics.json` records what a run left out of scoring (#1185). `catalog` is `vendored_components()`'s
    result: its map rides in the same way, for the `inventory` section (#1497)."""
    resolved = resolve_params(rubric, spec_light)
    script = measure.default_params(
        hit_min=float(resolved.get("hit_min") or 44.0),
        primary_min=float(resolved.get("primary_min") or 48.0),
        # action-row's budget besides the row itself: leading toggle + extra action + trailing accessory
        row_controls_max=int(sum(float(resolved.get(k) or 0) for k in
                                 ("row_leading_toggles", "row_extra_actions", "row_trailing_accessories"))),
        exclude_selectors=exclude_selectors(review or {}),
        components=(catalog or {}).get("components"),
        components_error=(catalog or {}).get("error"),
    )
    return {"script": script, "resolved": resolved}


def envelope(target: Target, rubric: Rubric, run_dir: Path, devices: List[str], params: Dict[str, object],
             commit: Optional[str], now: Optional[_dt.datetime] = None) -> Dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "rubric_version": rubric.version,
        "target": target.name,
        "commit": commit,
        "generated_at": iso(now or utc_now()),
        "base_url": target.base_url,
        "mode": "live",
        "run_dir": str(run_dir),
        "devices": list(devices),
        "review": target.review,
        "wide_views": list(target.wide_views),
        "wide_views_error": target.wide_views_error,
        "no_chat_surface": target.no_chat_surface,
        "no_chat_surface_error": target.no_chat_surface_error,
        "params": params,
        "interpreter": None,
        "unmeasured": None,
        "walk": None,
        "screens": [],
    }


def spawn_walk(python: str, target: Target, run_dir: Path, devices: List[str], params: Dict[str, object],
               scaffold: str, timeout: float = WALK_TIMEOUT_S, synthetic: bool = False) -> Dict[str, object]:
    """Run `walk.py` under the target interpreter; return `{returncode, stderr_tail, timed_out}`."""
    (run_dir / "params.json").write_text(json.dumps(params["script"]), encoding="utf-8")
    (run_dir / "review.json").write_text(json.dumps(target.review), encoding="utf-8")
    argv = [python, str(WALK_PY), "--url", target.base_url, "--out", str(run_dir),
            "--devices", ",".join(devices), "--scaffold", scaffold,
            "--params", str(run_dir / "params.json"), "--review", str(run_dir / "review.json")]
    if synthetic:
        argv.append("--synthetic")
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=timeout, creationflags=NO_WINDOW)
    except subprocess.TimeoutExpired as exc:
        tail = (exc.stderr or b"")
        tail = tail.decode("utf-8", "replace") if isinstance(tail, bytes) else str(tail)
        return {"returncode": None, "stderr_tail": tail[-2000:], "timed_out": True}
    (run_dir / "walk.log").write_text(proc.stderr or "", encoding="utf-8")
    return {"returncode": proc.returncode, "stderr_tail": (proc.stderr or "")[-2000:], "timed_out": False}


def kill_tree(proc: subprocess.Popen) -> None:
    """Kill a child this module started and every process it started in turn (`taskkill /T` on Windows)."""
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True,
                       creationflags=NO_WINDOW)
    else:
        proc.kill()


class SyntheticInstance:
    """A target's declared synthetic launcher (#995), run under the target interpreter.

    Contract: from the target root, `<python> <command...>` boots a throwaway
    instance with synthetic data, prints `URL=<base>` on stdout once it is
    ready, and runs until its stdin reaches EOF, then cleans up after itself
    and exits. Its stdout and stderr go to `<run_dir>/synthetic.log`.
    """

    def __init__(self, python: str, target: Target, block: Dict[str, object], run_dir: Path) -> None:
        self.command = [python] + list(block["command"])  # type: ignore[call-overload]
        self.cwd = str(target.root)
        self.startup_s = float(block["startup_timeout_s"])  # type: ignore[arg-type]
        self.log_path = run_dir / "synthetic.log"
        self.proc: Optional[subprocess.Popen] = None
        self._lines: "queue.Queue[Optional[str]]" = queue.Queue()
        self._log: List[str] = []

    def _pump(self, stream) -> None:
        for line in stream:
            self._log.append(line)
            self._lines.put(line)
        self._lines.put(None)

    def start(self) -> Dict[str, Optional[str]]:
        """`{url, error}`: the printed URL, or why none came (exit, timeout, spawn failure)."""
        try:
            self.proc = subprocess.Popen(self.command, cwd=self.cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                                         creationflags=NO_WINDOW)
        except OSError as exc:
            return {"url": None, "error": f"could not start {self.command[1]}: {exc}"}
        threading.Thread(target=self._pump, args=(self.proc.stdout,), daemon=True).start()
        deadline = time.monotonic() + self.startup_s
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                return {"url": None, "error": f"no URL= line within {self.startup_s:g}s"}
            try:
                line = self._lines.get(timeout=min(left, 1.0))
            except queue.Empty:
                continue
            if line is None:
                return {"url": None, "error": f"launcher exited ({self.proc.wait()}) before printing URL="}
            if line.startswith("URL="):
                return {"url": line[4:].strip().rstrip("/"), "error": None}

    def stop(self) -> str:
        """`stopped` (exited after its stdin closed), `killed` (tree-killed after the grace), or `unknown`."""
        proc = self.proc
        if proc is None:
            return "not_started"
        try:
            try:
                if proc.stdin:
                    proc.stdin.close()
            except OSError:
                pass
            try:
                proc.wait(timeout=SYNTHETIC_STOP_S)
                return "stopped"
            except subprocess.TimeoutExpired:
                pass
            kill_tree(proc)
            try:
                proc.wait(timeout=10)
                return "killed"
            except subprocess.TimeoutExpired:
                return "unknown"
        finally:
            self.log_path.write_text("".join(self._log)[-200000:], encoding="utf-8")


def measure_target(target: Target, rubric: Rubric, spec_light: Dict[str, str], devices: List[str],
                   python_override: Optional[str] = None, scaffold: str = DEFAULT_SCAFFOLD,
                   run_dir: Optional[Path] = None, walk_timeout: float = WALK_TIMEOUT_S,
                   synthetic: bool = False, e2e: bool = False) -> Dict[str, object]:
    """The whole measure leg; returns the `metrics.json` document (also written to the run dir).

    `synthetic` walks the target's declared throwaway instance instead of the
    live app (#995): the live port is never probed, the launcher is always
    stopped, and how it stopped is recorded under `synthetic.stop`.

    `e2e` then runs the target's e2e suite in surface-capture mode and appends
    its screens (#1498, `measure_e2e`). Without it the document carries no
    `e2e` key at all, so a plain run's output is unchanged.
    """
    run_dir = run_dir or run_dir_for(target.name)
    doc = _measure_walk(target, rubric, spec_light, devices, python_override, scaffold, run_dir, walk_timeout, synthetic)
    if e2e:
        doc["e2e"] = measure_e2e(doc, target, run_dir, scaffold)
        _write(doc, run_dir)
    return doc


def _measure_walk(target: Target, rubric: Rubric, spec_light: Dict[str, str], devices: List[str],
                  python_override: Optional[str], scaffold: str, run_dir: Path, walk_timeout: float,
                  synthetic: bool) -> Dict[str, object]:
    """The walk, live or on the synthetic instance: the document `measure_target` returns without `e2e`."""
    params = script_params(rubric, spec_light, target.review, vendored_components(scaffold))
    doc = envelope(target, rubric, run_dir, devices, params, target_commit(target))
    if not synthetic:
        return _measure(doc, target, run_dir, devices, params, python_override, scaffold, walk_timeout, False)

    doc["mode"] = "synthetic"
    try:
        block = synthetic_block(target)
    except PlanError as exc:
        doc["unmeasured"] = {"reason": "SYNTHETIC_UNDECLARED", "detail": str(exc)}
        return _write(doc, run_dir)
    interp = resolve_interpreter(target, python_override)
    doc["interpreter"] = interp["python"]
    if interp["status"] != "ok":
        doc["unmeasured"] = {"reason": "PLAYWRIGHT_MISSING", "detail": interp["detail"]}
        return _write(doc, run_dir)
    instance = SyntheticInstance(str(interp["python"]), target, block, run_dir)
    record: Dict[str, object] = {"command": block["command"], "log": str(instance.log_path), "stop": None}
    doc["synthetic"] = record
    try:
        started = instance.start()
        if not started["url"]:
            doc["unmeasured"] = {"reason": "SYNTHETIC_FAILED", "detail": started["error"]}
            return doc
        target = dataclasses.replace(target, base_url=str(started["url"]))
        doc["base_url"] = target.base_url
        return _measure(doc, target, run_dir, devices, params, python_override, scaffold, walk_timeout, True)
    finally:
        record["stop"] = instance.stop()
        _write(doc, run_dir)


def _measure(doc: Dict[str, object], target: Target, run_dir: Path, devices: List[str], params: Dict[str, object],
             python_override: Optional[str], scaffold: str, walk_timeout: float, synthetic: bool) -> Dict[str, object]:
    live = probe_listening(target.base_url)
    if live["status"] != "listening":
        doc["unmeasured"] = {"reason": live["status"], "detail": live["detail"]}
        return _write(doc, run_dir)

    interp = resolve_interpreter(target, python_override)
    doc["interpreter"] = interp["python"]
    if interp["status"] != "ok":
        doc["unmeasured"] = {"reason": "PLAYWRIGHT_MISSING", "detail": interp["detail"]}
        return _write(doc, run_dir)

    walk = spawn_walk(str(interp["python"]), target, run_dir, devices, params, scaffold, walk_timeout, synthetic)
    doc["walk"] = walk
    screens_path = run_dir / "screens.json"
    if walk["timed_out"]:
        doc["unmeasured"] = {"reason": "TIMEOUT", "detail": f"walk exceeded {walk_timeout:g}s"}
    elif not screens_path.is_file():
        doc["unmeasured"] = {"reason": "WALK_FAILED",
                             "detail": f"walk exit {walk['returncode']}: {walk['stderr_tail'][-500:]}"}
    else:
        doc["screens"] = json.loads(screens_path.read_text(encoding="utf-8"))
        walk_info = run_dir / "walk.json"
        if walk_info.is_file():
            doc["walk"]["info"] = json.loads(walk_info.read_text(encoding="utf-8"))
        if walk["returncode"] not in (0, None):
            doc["walk"]["note"] = "walk exited non-zero after writing screens; see walk.log"
    return _write(doc, run_dir)


# ---- measure --e2e: the target's own e2e suite in surface-capture mode ----------------
#
# The walk opens tabs, the Settings gear, details and declared extra steps; overlays nothing opens stay invisible.
# The target's e2e suite already opens most of them on its own disposable server, and project-scaffolding's
# vendored `tests/e2e/_surface.py` hook captures each one a test names with `surface(page, name)` once
# `DESIGN_SURFACE_CAPTURE=<dir>` is set. The directory is the whole contract (the hook imports nothing from here):
# `script.js` -- the walk's own page script, emitted by `walk.py --emit-script` under the target interpreter
# because the scaffold's `_geometry.py` imports Playwright -- plus `params.json`, the walk's params, and an
# optional `capture.json` carrying the target's `theme_storage_key`. Each capture becomes a screen record.

E2E_DIR = "e2e"
E2E_LOG = "e2e.log"
E2E_CAPTURE_ENV = "DESIGN_SURFACE_CAPTURE"
E2E_SCRIPT_TIMEOUT_S = 120.0
E2E_DRAIN_S = 10.0       # a killed suite gets this long to release its output pipe
PYTEST_NO_TESTS = 5      # pytest's exit code when collection selected nothing: no test calls surface(


def e2e_device(capture: Dict[str, object]) -> str:
    """The device name a capture was taken on, from the test's own context: no phone user agent is `desktop`,
    a phone on WebKit `iphone`, any other phone `android`. The engine and viewport ride on the screen too."""
    if not capture.get("phone"):
        return "desktop"
    return "iphone" if capture.get("engine") == "webkit" else "android"


def e2e_theme(theme: str, observed: object) -> "tuple[Optional[bool], Optional[dict]]":
    """`(applied, observed)` from what the hook read after its flip: the page's `data-theme` and the media query.

    `applied` is False when either names the other theme, None when the hook recorded nothing. The hook reads no
    canvas luminance, so an app that ignores both and paints its own theme is not caught here as the walk catches it.
    """
    if not isinstance(observed, dict):
        return None, None
    applied, seen = measure.theme_agreement(theme, {"attr": observed.get("attr"), "luminance": None})
    dark = observed.get("dark")
    if isinstance(dark, bool) and seen is not None:
        media = "dark" if dark else "light"
        applied = applied and media == theme
        seen["rendered"] = seen.get("rendered") or media
    return applied, seen


def e2e_screens(capture_dir: Path, shots: Path) -> Dict[str, List]:
    """Every `<surface>.<theme>.json` the hook wrote, as screen records `<device>-<theme>-e2e-<surface>`.

    Returns `{screens, duplicates, unreadable}`. Each capture's PNG moves into `shots/` under its screen id, so the
    judge and the report find it beside the walk's. A capture whose script threw is a screen in error
    (`E2E_MEASURE_FAILED`), never an empty success; two surface names that slug to one id keep the first.
    """
    out: Dict[str, List] = {"screens": [], "duplicates": [], "unreadable": []}
    seen: set = set()
    shots.mkdir(parents=True, exist_ok=True)
    found = []
    for path in capture_dir.glob("*.json"):
        name, _, theme = path.name[:-len(".json")].rpartition(".")
        if name and theme in THEMES:  # never params.json or capture.json
            found.append((name, THEMES.index(theme), theme, path))
    for name, _, theme, path in sorted(found):
        try:
            cap = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            out["unreadable"].append(f"{path.name}: {exc}")
            continue
        if not isinstance(cap, dict):
            out["unreadable"].append(f"{path.name}: not a JSON object")
            continue
        device, view = e2e_device(cap), f"e2e-{name}"
        sid = screen_id(device, theme, view)
        if sid in seen:
            out["duplicates"].append(f"{name} ({sid})")
            continue
        seen.add(sid)
        png, shot = capture_dir / f"{name}.{theme}.png", None
        if png.is_file():
            os.replace(png, shots / f"{sid}.png")
            shot = f"{sid}.png"
        metrics = cap.get("metrics")
        ok = cap.get("error") is None and isinstance(metrics, dict)
        applied, observed = e2e_theme(theme, cap.get("observed"))
        out["screens"].append({
            "id": sid, "device": device, "theme": theme, "view": view, "kind": "step",
            "status": "ok" if ok else "error", "reason": None if ok else "E2E_MEASURE_FAILED",
            "error": None if ok else str(cap.get("error") or "the capture carries no metrics")[:300],
            "screenshot": shot, "screenshot_full": None, "note": None, "metrics": metrics if ok else None,
            "theme_applied": applied, "theme_observed": observed,
            "source": "e2e", "surface": name, "engine": cap.get("engine"), "viewport": cap.get("viewport"),
        })
    return out


def prepare_e2e_dir(python: str, run_dir: Path, params: Dict[str, object], review: Dict[str, object],
                    scaffold: str) -> Dict[str, Optional[str]]:
    """Build `<run_dir>/e2e/` for the hook: `{dir, geometry, error}`. Files left by an earlier run there are removed
    first, so a stale capture can never read as this run's."""
    cap = run_dir / E2E_DIR
    cap.mkdir(parents=True, exist_ok=True)
    for stale in cap.iterdir():
        if stale.is_file():
            stale.unlink()
    try:
        proc = subprocess.run([python, str(WALK_PY), "--emit-script", str(cap / "script.js"), "--scaffold", scaffold],
                              capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=E2E_SCRIPT_TIMEOUT_S, creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"dir": str(cap), "geometry": None, "error": f"walk.py --emit-script did not run: {exc}"}
    if proc.returncode != 0 or not (cap / "script.js").is_file():
        return {"dir": str(cap), "geometry": None,
                "error": f"walk.py --emit-script exit {proc.returncode}: {(proc.stderr or '').strip()[-300:]}"}
    geometry = next((l[len("GEOMETRY="):] for l in proc.stdout.splitlines() if l.startswith("GEOMETRY=")), None)
    (cap / "params.json").write_text(json.dumps(params["script"]), encoding="utf-8")
    key = review.get("theme_storage_key") if isinstance(review, dict) else None
    if isinstance(key, str) and key:
        (cap / "capture.json").write_text(json.dumps({"theme_storage_key": key}), encoding="utf-8")
    return {"dir": str(cap), "geometry": geometry, "error": None}


def run_e2e_suite(python: str, root: Path, command: List[str], capture_dir: Path, log_path: Path,
                  timeout: float) -> Dict[str, object]:
    """Run the suite in the foreground from the target root with the hook armed: `{returncode, timed_out, tail}`.

    Its output goes to `log_path`. Past `timeout` its process tree is killed (a pytest run owns browsers)."""
    env = {**os.environ, E2E_CAPTURE_ENV: str(capture_dir), "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1"}
    try:
        proc = subprocess.Popen([python] + list(command), cwd=str(root), stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                errors="replace", env=env, creationflags=NO_WINDOW)
    except OSError as exc:
        log_path.write_text(f"could not start {python}: {exc}\n", encoding="utf-8")
        return {"returncode": None, "timed_out": False, "tail": f"could not start {python}: {exc}"}
    timed_out = False
    try:
        output, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        kill_tree(proc)
        try:
            output, _ = proc.communicate(timeout=E2E_DRAIN_S)
        except subprocess.TimeoutExpired:
            output = ""
    log_path.write_text(output or "", encoding="utf-8")
    return {"returncode": proc.returncode, "timed_out": timed_out, "tail": (output or "")[-2000:]}


def _tree_state(root: Path) -> "tuple[Optional[Path], Dict[str, tuple], str]":
    """`(toplevel, {path: (porcelain XY, content hash)}, error)` for the git tree holding `root`.

    `toplevel` is None, with `error` saying why, when the tree's state cannot be read (no git, not a checkout): the
    caller reports that as unchecked, never as clean. Paths are relative to the toplevel; a path git lists that is
    not a file now (a deletion) hashes to ""."""
    top = git_run.run_git(["-C", str(root), "rev-parse", "--show-toplevel"])
    if top.returncode != 0:
        return None, {}, f"git rev-parse --show-toplevel exit {top.returncode}: {(top.stderr or '').strip()[-200:]}"
    toplevel = Path(top.stdout.strip())
    proc = git_run.run_git(["-C", str(toplevel), "status", "--porcelain=v1", "-z", "--untracked-files=all"])
    if proc.returncode != 0:
        return None, {}, f"git status exit {proc.returncode}: {(proc.stderr or '').strip()[-200:]}"
    state: Dict[str, tuple] = {}
    entries = iter(proc.stdout.split("\0"))
    for entry in entries:
        if len(entry) < 4:
            continue
        xy, path = entry[:2], entry[3:]
        if xy[0] in "RC":
            next(entries, None)  # a rename or copy carries its source as the next field
        file = toplevel / path
        state[path] = (xy, hashlib.sha1(file.read_bytes()).hexdigest() if file.is_file() else "")
    return toplevel, state, ""


def restore_tree(toplevel: Path, before: Dict[str, tuple]) -> Dict[str, object]:
    """Put the target tree back as `before` found it: `{state, restored, unrestored, detail}`.

    The suite rewrites tracked files (its gallery screenshots) and leaves new ones. A path clean before the run is
    restored from HEAD (tracked) or removed (untracked); a path already dirty before the run that the run changed
    cannot be told from the user's own edit and is never touched. `state` is `clean` (the run changed nothing),
    `restored`, or `restore_failed` (the tree still differs from `before`: `unrestored` names the paths).
    """
    _, after, error = _tree_state(toplevel)
    if error:
        return {"state": "restore_failed", "restored": [], "unrestored": [], "detail": f"the tree was not re-read: {error}"}
    restored: List[str] = []
    for path in sorted(set(after) - set(before)):
        if after[path][0] == "??":
            try:
                (toplevel / path).unlink()
                restored.append(path)
            except OSError:
                pass  # the re-read below names it
        elif git_run.run_git(["-C", str(toplevel), "restore", "--source=HEAD", "--staged", "--worktree", "--", path]).returncode == 0:
            restored.append(path)
    _, now, error = _tree_state(toplevel)
    unrestored = sorted(p for p in set(now) | set(before) if now.get(p) != before.get(p))
    if error or unrestored:
        detail = error or (f"{len(unrestored)} path(s) differ from the tree before the run (dirty before it, or git could not "
                           f"restore them): {', '.join(unrestored[:8])}")
        return {"state": "restore_failed", "restored": restored, "unrestored": unrestored, "detail": detail}
    return {"state": "restored" if restored else "clean", "restored": restored, "unrestored": [], "detail": ""}


def measure_e2e(doc: Dict[str, object], target: Target, run_dir: Path, scaffold: str) -> Dict[str, object]:
    """Run the target's e2e suite in capture mode and append its screens to `doc["screens"]`; return the `e2e` record.

    The record's `unmeasured` is null or `{reason, detail}`, one word per condition, and never touches the walk's own
    result: `E2E_SKIPPED` (the walk measured nothing, so no suite ran), `E2E_BAD_CONFIG`, `E2E_HOOK_MISSING` (no
    vendored `tests/e2e/_surface.py`), `E2E_SCRIPT_FAILED` (the page script could not be emitted), `E2E_TIMEOUT` /
    `E2E_FAILED` (past the timeout / a non-zero exit, before any capture) and `E2E_NO_SURFACES` (it ran and captured
    nothing). A suite that fails or times out after capturing keeps its screens, with a `note`, as the walk does.

    Two more states ride beside that outcome, neither folded into it (#1507): `warning` (`E2E_THEME_MISMATCH`, with
    `theme_mismatch` listing the screens whose page kept another theme than the hook asked for) and `tree`, what
    `restore_tree` did to the target's working tree after the suite (`clean` / `restored` / `restore_failed` /
    `unchecked` when git could not be read).
    """
    rec: Dict[str, object] = {"command": None, "timeout_s": None, "capture_dir": None, "log": None, "geometry": None,
                              "returncode": None, "timed_out": False, "surfaces": [], "screens": 0,
                              "duplicates": [], "unreadable": [], "unmeasured": None, "note": None,
                              "warning": None, "theme_mismatch": [], "tree": None}

    def unmeasured(reason: str, detail: str) -> Dict[str, object]:
        rec["unmeasured"] = {"reason": reason, "detail": detail}
        return rec

    walk_unmeasured = doc.get("unmeasured")
    if walk_unmeasured:
        why = walk_unmeasured.get("reason") if isinstance(walk_unmeasured, dict) else walk_unmeasured
        return unmeasured("E2E_SKIPPED", f"the walk did not measure ({why}), so the e2e suite was not run")
    try:
        block = e2e_block(target)
    except PlanError as exc:
        return unmeasured("E2E_BAD_CONFIG", str(exc))
    rec["command"], rec["timeout_s"] = block["command"], block["timeout_s"]
    hook = Path(target.root).joinpath(*E2E_HOOK) if target.root else None
    if hook is None or not hook.is_file():
        return unmeasured("E2E_HOOK_MISSING", f"no vendored surface() hook at {hook or '(no repo root)'}: copy "
                          "project-scaffolding's tests/e2e/_surface.py in and track it as `surface_capture`")
    python = str(doc["interpreter"])
    prep = prepare_e2e_dir(python, run_dir, doc["params"], target.review, scaffold)  # type: ignore[arg-type]
    rec["capture_dir"], rec["geometry"] = prep["dir"], prep["geometry"]
    if prep["error"]:
        return unmeasured("E2E_SCRIPT_FAILED", str(prep["error"]))
    log_path = run_dir / E2E_LOG
    rec["log"] = str(log_path)
    toplevel, before, tree_error = _tree_state(Path(target.root))
    run = run_e2e_suite(python, Path(target.root), list(block["command"]), Path(str(prep["dir"])), log_path,  # type: ignore[arg-type]
                        float(block["timeout_s"]))  # type: ignore[arg-type]
    rec["returncode"], rec["timed_out"] = run["returncode"], run["timed_out"]
    if toplevel is None:
        rec["tree"] = {"state": "unchecked", "restored": [], "unrestored": [],
                       "detail": f"the target tree's state was not read before the run, so nothing was restored: {tree_error}"}
    else:
        rec["tree"] = restore_tree(toplevel, before)
    got = e2e_screens(Path(str(prep["dir"])), run_dir / "shots")
    screens = got["screens"]
    rec["surfaces"] = sorted({str(s["surface"]) for s in screens})
    rec["screens"], rec["duplicates"], rec["unreadable"] = len(screens), got["duplicates"], got["unreadable"]
    rc, timeout = run["returncode"], float(block["timeout_s"])  # type: ignore[arg-type]
    if not screens:
        unread = f"; unreadable captures: {'; '.join(got['unreadable'][:3])}" if got["unreadable"] else ""
        if run["timed_out"]:
            return unmeasured("E2E_TIMEOUT", f"the suite exceeded {timeout:g}s before any capture; its process tree "
                              f"was killed{unread}")
        if rc in (0, PYTEST_NO_TESTS):
            return unmeasured("E2E_NO_SURFACES", f"the suite ran (exit {rc}) but captured no surface: no test it ran "
                              f"calls surface(page, name){unread}")
        return unmeasured("E2E_FAILED", f"exit {rc} before any capture{unread}: {str(run['tail'])[-500:]}")
    doc["screens"].extend(screens)  # type: ignore[union-attr]
    mismatched = theme_mismatches(screens)
    if mismatched:
        shown = ", ".join(f"{m['id']} (asked {m['requested']}, rendered {m['rendered'] or 'another theme'})" for m in mismatched[:4])
        rec["theme_mismatch"] = mismatched
        rec["warning"] = {"reason": "E2E_THEME_MISMATCH",
                          "detail": f"{len(mismatched)} of {len(screens)} e2e screens did not render the requested theme: {shown}"
                                    f"{' ...' if len(mismatched) > 4 else ''}. An app that stamps data-theme at load needs "
                                    "[design.review] theme_storage_key in its .fleet.toml, or every dark capture is light"}
    if run["timed_out"]:
        rec["note"] = f"the suite exceeded {timeout:g}s after capturing and was killed; its captures are kept, see {E2E_LOG}"
    elif rc != 0:
        rec["note"] = f"the suite exited {rc} after capturing; its captures are kept, see {E2E_LOG}"
    return rec


def _write(doc: Dict[str, object], run_dir: Path) -> Dict[str, object]:
    (run_dir / "metrics.json").write_text(json.dumps(doc, indent=1, ensure_ascii=True), encoding="utf-8")
    return doc
