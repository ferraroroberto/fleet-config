"""Run several hook modules in one process and answer once (fleet-config#1274).

Claude Code starts every matching hook as its own process, in parallel. Through
`run-hook.ps1` that is a PowerShell 5.1 start (~160 ms, ~250 ms with the shim's
work) plus a Python start per hook, and a Bash call matched eight of them: a
measured ~500 ms of wall-clock before every shell command, almost all of it
launcher. This module is one Python process that runs the named hooks'
`main()` in turn, in-process, and folds their answers into one:

    <venv python> C:/Users/rober/.claude/hooks/hook_dispatch.py <hook> [<hook> ...]

The settings.json command points the venv interpreter straight at this file,
the way Codex already calls its hook modules (`codex-hooks.json`). No shim
sits in between, so stdin reaches `read_stdin_json()` as the harness's own
UTF-8 bytes (fleet-config#912).

What each hook sees and says is unchanged:

- every hook reads its own fresh copy of the original payload, so no guard
  ever sees a rewritten command, and the order of names on the command line
  can't change any verdict;
- `block()` / `warn()` / `rewrite_command()` / `allow()` record a
  `harness_wire.Verdict` instead of writing it, and `harness_wire.
  emit_collected()` answers once, in the calling harness's dialect: any block
  refuses, else a rewrite applies, else the nudges, else allow;
- each hook's other stdout/stderr output is passed through to stderr, so the
  JSON answer on stdout stays parseable and the breadcrumbs stay visible;
- a hook that crashes or is missing fails open for itself alone, as its own
  process did. Its traceback goes to stderr, and with no other verdict the
  dispatcher exits 1, Claude's non-blocking hook error, as before. A hook that
  calls `sys.exit(2)` itself still refuses, with its stderr as the reason.
"""
from __future__ import annotations

import contextlib
import importlib
import io
import logging
import re
import sys
import traceback
from pathlib import Path
from typing import List, NoReturn, Optional

HOOKS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(HOOKS_DIR))
import harness_wire  # noqa: E402

_HOOK_NAME_RE = re.compile(r"^[a-z0-9_]+$")


def _stdin_from(raw: bytes) -> io.TextIOWrapper:
    return io.TextIOWrapper(io.BytesIO(raw), encoding="utf-8")


def run_hook(name: str, raw: bytes, verdicts: List[harness_wire.Verdict]) -> bool:
    """Run hook module `name`'s `main()` on `raw`, recording its verdict in
    `verdicts`. Returns False when the hook couldn't answer (missing module,
    no `main`, crash, or an exit code other than 0 and 2)."""
    if not _HOOK_NAME_RE.match(name) or not (HOOKS_DIR / f"{name}.py").is_file():
        print(f"hook_dispatch: hook module not found: {name}", file=sys.stderr)
        return False
    captured_out, captured_err = io.StringIO(), io.StringIO()
    saved_stdin, saved_argv = sys.stdin, sys.argv
    saved_handlers = list(logging.root.handlers)
    answered = True
    try:
        with contextlib.redirect_stdout(captured_out), contextlib.redirect_stderr(captured_err):
            try:
                module = importlib.import_module(name)
                main = getattr(module, "main")
                sys.stdin = _stdin_from(raw)
                sys.argv = [str(HOOKS_DIR / f"{name}.py")]
                harness_wire._COLLECTOR = verdicts
                main()
            except harness_wire.Collected:
                pass
            except SystemExit as exc:
                if exc.code == 2:
                    reason = captured_err.getvalue().strip() or f"{name} refused the call"
                    captured_err = io.StringIO()
                    verdicts.append(harness_wire.Verdict("block", reason))
                elif exc.code not in (0, None):
                    print(f"hook_dispatch: {name} exited {exc.code}", file=sys.stderr)
                    answered = False
            except Exception:
                traceback.print_exc()
                answered = False
    finally:
        harness_wire._COLLECTOR = None
        sys.stdin, sys.argv = saved_stdin, saved_argv
        logging.root.handlers[:] = saved_handlers
    noise = captured_out.getvalue() + captured_err.getvalue()
    if noise:
        sys.stderr.write(noise)
        sys.stderr.flush()
    return answered


def main(names: Optional[List[str]] = None) -> NoReturn:
    names = sys.argv[1:] if names is None else names
    raw = sys.stdin.buffer.read()
    verdicts: List[harness_wire.Verdict] = []
    all_answered = True
    for name in names:
        all_answered = run_hook(name, raw, verdicts) and all_answered
    # Re-read the payload so the answer's dialect follows *this* payload's
    # harness and event, whatever the last hook happened to read.
    sys.stdin = _stdin_from(raw)
    harness_wire.read_stdin_json()
    if not verdicts and not all_answered:
        sys.exit(1)
    harness_wire.emit_collected(verdicts)


if __name__ == "__main__":
    main()
