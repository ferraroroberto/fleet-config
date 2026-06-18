"""Nudge when a browser-launch file is missing the anti-bot stealth kwargs.

Triggers on `PostToolUse` for `Edit`/`Write`/`MultiEdit`. **Non-blocking** —
emits a single one-line nudge on stdout (exit 0). The user decides whether the
detection risk is real.

Fires only for the files where the fleet launches Playwright — basename
`chrome_launch.py`, `browser.py`, or matching `*_session.py` — and only when the
on-disk content actually launches a browser (`launch_persistent_context` /
`.launch(`) yet is missing any stealth marker from the global "Browser
automation must not look like a bot" rule:

  * `--enable-automation` stripped via `ignore_default_args`
  * a `navigator.webdriver` init script
  * `channel="chrome"` (real Chrome, not bundled Chromium)
  * `--disable-blink-features=AutomationControlled`

Catches a launch re-inlined into a new module instead of importing the
project's single-source-of-truth launch helper. Reads the file from disk
(the PostToolUse target already exists), matching `py_syntax_check.py`.
"""

from __future__ import annotations

import fnmatch
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib  # noqa: E402


WATCHED_NAMES = {"chrome_launch.py", "browser.py"}
WATCHED_GLOB = "*_session.py"

LAUNCH_RE = re.compile(r"launch_persistent_context|\.launch\s*\(")

# (label, regex) for each required stealth marker. A launch missing any of these
# earns a nudge naming exactly what's absent.
STEALTH_MARKERS = (
    ("--enable-automation strip (ignore_default_args)", re.compile(r"enable-automation")),
    # The canonical init script is `Object.defineProperty(navigator, 'webdriver', …)`
    # (so `navigator, 'webdriver'`), but a bare `navigator.webdriver` is also valid —
    # match either by allowing separators between the two tokens.
    ("navigator.webdriver init script", re.compile(r"navigator[\s,.'\"]+webdriver")),
    ('channel="chrome"', re.compile(r"""channel\s*=\s*['"]chrome['"]""")),
    ("--disable-blink-features=AutomationControlled", re.compile(r"AutomationControlled")),
)


def main() -> None:
    payload = _lib.read_stdin_json()
    if _lib.tool_name(payload) not in {"Edit", "Write", "MultiEdit"}:
        _lib.allow()

    target = _lib.file_path(payload)
    if target is None or not target.exists():
        _lib.allow()

    name = target.name
    if name not in WATCHED_NAMES and not fnmatch.fnmatch(name, WATCHED_GLOB):
        _lib.allow()

    try:
        content = target.read_text(encoding="utf-8", errors="replace")
    except OSError:
        _lib.allow()

    if not LAUNCH_RE.search(content):
        _lib.allow()

    missing = [label for label, rx in STEALTH_MARKERS if not rx.search(content)]
    if missing:
        _lib.warn(
            f"Nudge: {name} launches a browser but is missing stealth marker(s): "
            f"{'; '.join(missing)}. The 'Browser automation must not look like a bot' "
            "rule needs all of them (past Mercadona/Casa Melier runs hit captchas the "
            "moment automation was detected). Import the project's single-source launch "
            "helper rather than re-inlining launch args."
        )

    _lib.allow()


if __name__ == "__main__":
    main()
