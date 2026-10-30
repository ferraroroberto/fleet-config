"""`ui_proof capture`: drive a running app at named viewports and themes into the `.proof/` staging folder.

Everything that is not browser code lives here: the viewport/theme arguments, the target repo's
interpreter (the child runs under its `.venv`, which has Playwright; this repo's venv is
stdlib-only), the probes that tell "no Playwright" from "app not answering", and the hand-off to
`store.capture`. The browser work is `shoot.py`. Screenshots are written inside this checkout's
`.proof/` and the scratch folder is removed afterwards, so nothing lands outside the tree.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional, Tuple

import browser_verify
from no_window import NO_WINDOW
from ui_proof import store

VIEWPORTS = {"phone": "390x844", "desktop": "1280x900"}
THEMES = ("light", "dark")
SHOOT = Path(__file__).resolve().parent / "shoot.py"
PROBE_TIMEOUT_S = 60
RUN_TIMEOUT_S = 300
_SIZE = re.compile(r"\d{2,5}x\d{2,5}")
_LABEL = re.compile(r"[A-Za-z0-9_.]+")


def parse_viewport(text: str) -> Tuple[str, str]:
    """`phone`, `desktop`, `390x844` or `<label>=<W>x<H>` -> `(label, "<W>x<H>")`; ValueError otherwise.

    A label never holds a `-`: the leg key is `<viewport>-<theme>`.
    """
    label, sep, dims = text.partition("=")
    if not sep:
        label, dims = text, VIEWPORTS.get(text, text)
    if not _SIZE.fullmatch(dims) or not _LABEL.fullmatch(label):
        raise ValueError(f"--viewport needs phone, desktop, <W>x<H> or <label>=<W>x<H>, got {text!r}")
    return label, dims


def _interpreter(repo: Path) -> Optional[Path]:
    """The repo's `.venv` python: this checkout's, else the primary's (a worktree without its junction)."""
    for root in (store.proof_root(repo), store.primary_root(repo)):
        if root is not None and (found := browser_verify.discover_venv_python(str(root.parent))):
            return found
    return None


def _has_playwright(python: Path) -> bool:
    try:
        res = subprocess.run(browser_verify.playwright_probe_cmd(str(python)), capture_output=True, text=True,
                             timeout=PROBE_TIMEOUT_S, creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return res.returncode == 0


def run(repo: Path, phase: str, url: str, state: str, viewports: List[str], themes: List[str],
        clicks: List[str], wait_ms: Optional[int] = None) -> Tuple[bool, str]:
    """Capture `state` at `viewports` x `themes`; `(ok, CAPTURED=... line)`. Raises ValueError on a bad viewport."""
    specs = [parse_viewport(v) for v in viewports]
    root = store.proof_root(repo)
    if root is None:
        return False, "CAPTURED=none reason=not a git checkout"
    python = _interpreter(repo)
    if python is None:
        return False, "CAPTURED=none reason=PLAYWRIGHT_MISSING no .venv python in this repo; use --image instead"
    if not _has_playwright(python):
        return False, f"CAPTURED=none reason=PLAYWRIGHT_MISSING playwright is not importable under {python}"
    if not browser_verify.app_reachable(url):
        return False, f"CAPTURED=none reason=APP_UNREACHABLE nothing answers at {url}"
    root.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix=".shots-", dir=root))
    try:
        cmd = [str(python), str(SHOOT), "--url", url, "--out", str(scratch)]
        for label, dims in specs:
            cmd += ["--viewport", f"{label}={dims}"]
        for theme in themes:
            cmd += ["--theme", theme]
        for selector in clicks:
            cmd += ["--click", selector]
        if wait_ms is not None:
            cmd += ["--wait-ms", str(wait_ms)]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=RUN_TIMEOUT_S, creationflags=NO_WINDOW)
        except subprocess.TimeoutExpired:
            return False, f"CAPTURED=none reason=TIMEOUT the browser did not finish in {RUN_TIMEOUT_S}s"
        try:
            shots = json.loads((scratch / "shots.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            tail = (res.stderr or "").strip().splitlines()[-1:] or ["no output"]
            return False, f"CAPTURED=none reason=BROWSER_FAILED {tail[0][:200]}"
        images = [{"state": state, "viewport": s["viewport"], "theme": s["theme"], "screenshot": s["screenshot"]}
                  for s in shots if "error" not in s]
        failed = [f"{s['viewport']}-{s['theme']}" for s in shots if "error" in s]
        if not images:
            return False, "CAPTURED=none reason=BROWSER_FAILED every leg failed: " + ",".join(failed)
        ok, line = store.capture(repo, phase, None, images)
        if failed:
            line += " failed_legs=" + ",".join(failed)
        return ok and not failed, line
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
