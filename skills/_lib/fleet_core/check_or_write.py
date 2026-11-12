"""The check-or-regenerate tail shared by the generated-file renderers.

A renderer builds the file's text, then either verifies the committed copy
(`--check`: STALE to stderr, exit 1) or rewrites it. One implementation keeps
the wording, the stream and the exit code identical across renderers.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional


def check_or_write(path: Path, rendered: str, label: str, script: Path, check: bool,
                   current: Optional[str] = None, newline: Optional[str] = None) -> int:
    """Compare `rendered` with `path`; 0 when current (or written), 1 when `--check` finds it stale.

    `script` is the renderer, named in the STALE message as the command that
    regenerates the file. `current` is the file's text when the caller already
    read it; otherwise it is read here, a missing file counting as stale.
    `newline` is passed to `write_text` for renderers that pin LF.
    """
    if current is None and path.exists():
        current = path.read_text(encoding="utf-8")
    if check:
        if rendered != current:
            regen = f"{Path(sys.executable).as_posix()} {script.resolve().as_posix()}"
            print(f"{label}: STALE — run `{regen}` and commit.", file=sys.stderr)  # logging-not-print-exempt: CLI output
            return 1
        print(f"{label}: up to date.")  # logging-not-print-exempt: CLI output
        return 0
    if rendered != current:
        path.write_text(rendered, encoding="utf-8", newline=newline)  # runtime-write-exempt: the caller names the generated file it owns
        print(f"{label}: regenerated {path.name}.")  # logging-not-print-exempt: CLI output
    else:
        print(f"{label}: {path.name} already up to date.")  # logging-not-print-exempt: CLI output
    return 0
