"""The static preview a web-page review measures: resolved from the target repo, started, stopped.

A web page has no running app to probe. The review starts the repo's own
preview server on a free loopback port, measures against it, and stops it —
only the PID it started, never anything found already listening.

Which preview runs, in order:

  1. `.fleet.toml` `[design.review.preview]`, when declared:

         [design.review.preview]
         command  = ["scripts/site_serve.py"]   # argv after the target's python, run from the target root;
                                                # the review appends `--port <N>` (or puts <N> where "{port}" is)
         site_dir = "site"                      # the built folder, for `_redirects` / `_headers` / the page list

  2. else the first of `PREVIEW_SCRIPTS` that exists in the target checkout —
     the scaffold's vendored `scripts/site_serve.py`, then the older
     `scripts/serve.py` — each taking `--host` and `--port`.

No preview at all is `NO_PREVIEW`, an unmeasured run, never a guess.
`site_dir` defaults to `site/` when it holds an `index.html`, else the repo root
when that does.
"""
from __future__ import annotations

import logging
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from no_window import NO_WINDOW  # noqa: E402

LOOPBACK = "127.0.0.1"
PREVIEW_SCRIPTS = ("scripts/site_serve.py", "scripts/serve.py")
STARTUP_TIMEOUT_S = 30.0
STOP_TIMEOUT_S = 10.0


class PreviewError(Exception):
    """A preview that cannot be resolved or started; `reason` is the unmeasured code."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


@dataclass
class PreviewPlan:
    """What will be started: argv (with the port slot) and where the built site lives."""

    argv: List[str]
    cwd: Path
    site_dir: Optional[Path]
    source: str  # "declared" or the script path it was found at


def free_port() -> int:
    """A loopback port nothing is bound to right now (the OS picks it)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((LOOPBACK, 0))
        return int(s.getsockname()[1])


def _site_dir(root: Path, declared: object) -> Optional[Path]:
    if isinstance(declared, str) and declared.strip():
        path = (root / declared).resolve()
        if root.resolve() not in path.parents and path != root.resolve():
            raise PreviewError("BAD_PREVIEW", f"site_dir {declared!r} is outside the target checkout")
        return path if path.is_dir() else None
    for candidate in (root / "site", root):
        if (candidate / "index.html").is_file():
            return candidate
    return None


def resolve_preview(root: Optional[Path], review: Dict[str, object]) -> PreviewPlan:
    """The preview to start for a target checkout; raises `PreviewError` when there is none."""
    if root is None:
        raise PreviewError("NO_PREVIEW", "the target has no checkout to serve")
    root = Path(root)
    block = review.get("preview") if isinstance(review, dict) else None
    if block is not None and not isinstance(block, dict):
        raise PreviewError("BAD_PREVIEW", "[design.review.preview] must be a table")
    block = block or {}
    command = block.get("command")
    if command is not None:
        if isinstance(command, str):
            command = [command]
        if not isinstance(command, list) or not command or not all(isinstance(c, str) and c for c in command):
            raise PreviewError("BAD_PREVIEW", "[design.review.preview].command must be a non-empty list of strings")
        script = (root / command[0]).resolve()
        if Path(command[0]).is_absolute() or root.resolve() not in script.parents or not script.is_file():
            raise PreviewError("BAD_PREVIEW", f"preview command {command[0]!r} is not a file inside the target checkout")
        argv = [str(script)] + [str(c) for c in command[1:]]
        if not any("{port}" in a for a in argv):
            argv += ["--port", "{port}"]
        return PreviewPlan(argv=argv, cwd=root, site_dir=_site_dir(root, block.get("site_dir")), source="declared")
    for rel in PREVIEW_SCRIPTS:
        script = root / rel
        if script.is_file():
            return PreviewPlan(argv=[str(script), "--host", LOOPBACK, "--port", "{port}"], cwd=root,
                               site_dir=_site_dir(root, block.get("site_dir")), source=rel)
    raise PreviewError("NO_PREVIEW", f"no [design.review.preview] and none of {list(PREVIEW_SCRIPTS)} in {root}")


def _answers(url: str, timeout: float = 2.0) -> bool:
    """True once the preview answers HTTP at all (any status: a 404 is still a live server)."""
    try:
        with urllib.request.urlopen(url, timeout=timeout):  # noqa: S310 - loopback URL this module built
            return True
    except urllib.error.HTTPError:
        return True
    except (urllib.error.URLError, OSError):
        return False


class PreviewServer:
    """Start the planned preview under `python` on a free port; stop exactly that process.

    Used as a context manager. `base_url` is set once the server answers HTTP.
    The server's output goes to `<log_path>`; a preview that exits or never
    answers within `STARTUP_TIMEOUT_S` raises `PreviewError("PREVIEW_FAILED")`.
    """

    def __init__(self, python: str, plan: PreviewPlan, log_path: Path, port: Optional[int] = None) -> None:
        self.python = python
        self.plan = plan
        self.log_path = log_path
        self.port = port or free_port()
        self.base_url = f"http://{LOOPBACK}:{self.port}"
        self.proc: Optional[subprocess.Popen] = None
        self.pid: Optional[int] = None
        self._log = None

    def __enter__(self) -> "PreviewServer":
        argv = [self.python] + [a.replace("{port}", str(self.port)) for a in self.plan.argv]
        # runtime-write-exempt: server output goes to the log_path the caller passed
        log = self.log_path.open("w", encoding="utf-8")
        try:
            self.proc = subprocess.Popen(argv, cwd=str(self.plan.cwd), stdin=subprocess.DEVNULL, stdout=log,
                                         stderr=subprocess.STDOUT, creationflags=NO_WINDOW)
        except OSError as exc:
            log.close()
            raise PreviewError("PREVIEW_FAILED", f"could not start {argv[1]}: {exc}") from exc
        self._log = log
        self.pid = self.proc.pid
        deadline = time.monotonic() + STARTUP_TIMEOUT_S
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                self.stop()
                raise PreviewError("PREVIEW_FAILED", f"the preview exited with {self.proc.returncode}; see {self.log_path}")
            if _answers(self.base_url + "/"):
                return self
            time.sleep(0.25)
        self.stop()
        raise PreviewError("PREVIEW_FAILED", f"the preview did not answer on port {self.port} within {STARTUP_TIMEOUT_S:g}s")

    def stop(self) -> None:
        """Terminate the started PID (and only it), escalating to a tree kill of that PID if it lingers."""
        proc = self.proc
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=STOP_TIMEOUT_S)
            except subprocess.TimeoutExpired:
                try:
                    subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True,
                                   creationflags=NO_WINDOW, timeout=STOP_TIMEOUT_S)
                except subprocess.TimeoutExpired:
                    logging.getLogger(__name__).warning(
                        "taskkill of pid %s did not finish within %gs", proc.pid, STOP_TIMEOUT_S)
                proc.wait(timeout=STOP_TIMEOUT_S)
        if self._log is not None and not self._log.closed:
            self._log.close()

    def __exit__(self, *exc: object) -> None:
        self.stop()
