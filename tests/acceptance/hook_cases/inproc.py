"""Run a hook's `main()` inside the test process instead of a fresh interpreter.

A hook case costs about a second as a spawn, almost all of it interpreter start
and imports, and the hook matrix, the dispatch differential and the two sink
suites replay the same few hooks hundreds of times. `run_in_process()` answers a
case exactly as `shared.run()` does, `(exit code, stdout, stderr)`, with the
process-level state a spawn gave for free made explicit: the hook's own modules
are imported fresh for every case, the environment is the suite's `hook_env`,
stdin is the payload, and stdout/stderr/argv/logging are restored afterwards.

Each hook keeps one real spawn (`wiring_proof`): the interpreter, the `__main__`
guard, real pipes and `_lib.block()`'s per-harness wire format, compared with the
in-process answer for the same payload so the two can never drift apart.
"""
from __future__ import annotations

import contextlib
import importlib
import io
import json
import logging
import os
import sys
import traceback
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

from acceptance.shared import HOOKS, hook_env

Result = Tuple[int, str, str]

_HOOKS_ROOT = str(HOOKS.resolve()).lower()


def _is_hook_module(module: Any) -> bool:
    """True for a module loaded from `hooks/` (the hook, `_lib`, `hook_wire`, ...)."""
    path = getattr(module, "__file__", None)
    return bool(path) and os.path.abspath(path).lower().startswith(_HOOKS_ROOT)


@contextlib.contextmanager
def _isolated_process_state(env: Dict[str, str]) -> Iterator[None]:
    """Everything a one-shot hook process owns, restored on exit."""
    saved_env = dict(os.environ)
    saved_argv, saved_stdin = sys.argv, sys.stdin
    saved_out, saved_err = sys.stdout, sys.stderr
    saved_modules = {name: mod for name, mod in sys.modules.items() if _is_hook_module(mod)}
    root = logging.getLogger()
    saved_handlers, saved_level = list(root.handlers), root.level
    for name in saved_modules:
        del sys.modules[name]
    os.environ.clear()
    os.environ.update(env)
    try:
        yield
    finally:
        for name, mod in list(sys.modules.items()):
            if _is_hook_module(mod):
                del sys.modules[name]
        sys.modules.update(saved_modules)
        os.environ.clear()
        os.environ.update(saved_env)
        sys.argv, sys.stdin, sys.stdout, sys.stderr = saved_argv, saved_stdin, saved_out, saved_err
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)


def run_in_process(hook: str, payload: Dict[str, Any],
                   extra_env: Optional[Dict[str, str]] = None,
                   args: Sequence[str] = ()) -> Result:
    """`shared.run()`'s answer for `hook`, without spawning an interpreter.

    `args` is the command line after the script, for `hook_dispatch`'s hook names."""
    raw = json.dumps(payload).encode("utf-8")
    out, err = io.StringIO(), io.StringIO()
    code = 0
    with _isolated_process_state(hook_env(extra_env)):
        sys.argv = [str(HOOKS / f"{hook}.py"), *args]
        sys.stdin = io.TextIOWrapper(io.BytesIO(raw), encoding="utf-8")
        sys.stdout, sys.stderr = out, err
        try:
            module = importlib.import_module(hook)
            result = module.main()
            code = result if isinstance(result, int) else 0
        except SystemExit as exc:
            if exc.code is None:
                code = 0
            elif isinstance(exc.code, int):
                code = exc.code
            else:
                err.write(f"{exc.code}\n")
                code = 1
        except Exception:
            traceback.print_exc(file=err)
            code = 1
    return code, out.getvalue(), err.getvalue()


def wiring_cases(
    cases: Iterable[Tuple[str, str, Dict[str, Any], int]],
) -> List[Tuple[str, str, Dict[str, Any], int]]:
    """One case per hook for its real spawn: the first that refuses, else the first.

    A refusal exercises the per-harness wire format `_lib.block()` writes."""
    chosen: Dict[str, Tuple[str, str, Dict[str, Any], int]] = {}
    for case in cases:
        hook, expected = case[1], case[3]
        if hook not in chosen or (chosen[hook][3] == 0 and expected != 0):
            chosen[hook] = case
    return list(chosen.values())
