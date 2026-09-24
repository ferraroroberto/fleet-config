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
import json
import queue
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
import git_run  # noqa: E402
import hooks_state  # noqa: E402
from no_window import NO_WINDOW  # noqa: E402

from . import measure  # noqa: E402
from .plan import PlanError, Target, synthetic_block  # noqa: E402
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


def script_params(rubric: Rubric, spec_light: Dict[str, str]) -> Dict[str, object]:
    """`{"script": <what the page script receives>, "resolved": <rubric params>}`."""
    resolved = resolve_params(rubric, spec_light)
    script = measure.default_params(
        hit_min=float(resolved.get("hit_min") or 44.0),
        primary_min=float(resolved.get("primary_min") or 48.0),
        # action-row's budget besides the row itself: leading toggle + extra action + trailing accessory (#996)
        row_controls_max=int(sum(float(resolved.get(k) or 0) for k in
                                 ("row_leading_toggles", "row_extra_actions", "row_trailing_accessories"))),
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
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True,
                               creationflags=NO_WINDOW)
            else:
                proc.kill()
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
                   synthetic: bool = False) -> Dict[str, object]:
    """The whole measure leg; returns the `metrics.json` document (also written to the run dir).

    `synthetic` walks the target's declared throwaway instance instead of the
    live app (#995): the live port is never probed, the launcher is always
    stopped, and how it stopped is recorded under `synthetic.stop`.
    """
    run_dir = run_dir or run_dir_for(target.name)
    params = script_params(rubric, spec_light)
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


def _write(doc: Dict[str, object], run_dir: Path) -> Dict[str, object]:
    (run_dir / "metrics.json").write_text(json.dumps(doc, indent=1, ensure_ascii=True), encoding="utf-8")
    return doc
