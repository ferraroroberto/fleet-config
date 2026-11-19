"""Count every `subprocess.Popen` in a gate run, and optionally record its covered lines, per unit.

Loaded by the interpreter at start because the gate's run wrapper (`unit_ledger.py run`, or a
gate-evidence helper doing the same) puts this folder on `PYTHONPATH`. Two independent halves, each
inert unless its env var is set. Each process writes its own `<report>.<pid>` file under one lock: on
Windows `O_APPEND` is a seek then a write, so threads or processes sharing one file overwrite each
other's lines and the count comes out short.

  spawns    `FLEET_GATE_SPAWN_REPORT`: one line per `subprocess.Popen`, tagged with the running unit.
  coverage  `FLEET_GATE_COVERAGE_REPORT` plus `FLEET_GATE_COVERAGE_ROOT`: the lines executed in files
            under the root, one JSON line per unit as `{"unit", "lines": [[relpath, lineno, text]]}`.
            `sys.monitoring` reports each line once and then disables it, so the cost is one callback
            per distinct line. In the reporter, `fleet_coverage_flush(unit)` writes what the ending
            unit ran and re-arms the events, so the next unit records its own lines. A process born
            inside a unit (it inherited `FLEET_GATE_UNIT`) ignores those calls, since a suite that tests
            the unit reporter would otherwise hand its own lines to its fake units, and writes everything
            under the unit it was born into at exit.

stdlib only and never raises: a probe must not be able to fail the gate it measures.
"""

import os
import threading
from typing import Any

_LOCK = threading.Lock()


def _append(base: str, text: str) -> None:
    """Append `text` to this process's own `<base>.<pid>` file, one writer at a time."""
    with _LOCK:
        # runtime-write-exempt: the path is the gate's temp report from the env; this probe imports nothing of the repo
        with open(f"{base}.{os.getpid()}", "a", encoding="utf-8") as fh:
            fh.write(text)


_REPORT = os.environ.get("FLEET_GATE_SPAWN_REPORT")
if _REPORT:
    try:
        import json
        import subprocess

        _real_init = subprocess.Popen.__init__

        def _counting_init(self: Any, *args: Any, **kwargs: Any) -> None:
            try:
                _append(str(_REPORT), json.dumps({"unit": os.environ.get("FLEET_GATE_UNIT", ""), "pid": os.getpid()}) + "\n")
            except Exception:
                pass
            _real_init(self, *args, **kwargs)

        subprocess.Popen.__init__ = _counting_init  # type: ignore[method-assign]
    except Exception:
        pass


_COVERAGE = os.environ.get("FLEET_GATE_COVERAGE_REPORT")
_COVERAGE_ROOT = os.environ.get("FLEET_GATE_COVERAGE_ROOT")
if _COVERAGE and _COVERAGE_ROOT:
    try:
        import atexit
        import json as _json
        import linecache
        import sys
        from types import CodeType

        _mon = sys.monitoring
        _TOOL = _mon.COVERAGE_ID
        _root = os.path.normcase(os.path.abspath(_COVERAGE_ROOT)).rstrip("\\/") + os.sep
        _hits: set[tuple[str, int]] = set()
        _under_root: dict[str, bool] = {}
        _BORN_IN = os.environ.get("FLEET_GATE_UNIT", "")

        def _on_line(code: CodeType, line: int) -> Any:
            name = code.co_filename
            inside = _under_root.get(name)
            if inside is None:
                # `<frozen os>` and `<string>` are not paths; abspath would put them under the cwd.
                inside = _under_root[name] = (os.path.isabs(name)
                                              and os.path.normcase(os.path.abspath(name)).startswith(_root))
            if inside:
                _hits.add((name, line))
            return _mon.DISABLE

        def _write(unit: str) -> None:
            try:
                if unit and _hits:
                    lines = []
                    for name, lineno in sorted(_hits):
                        rel = os.path.relpath(os.path.abspath(name), str(_COVERAGE_ROOT)).replace("\\", "/")
                        lines.append([rel, lineno, linecache.getline(name, lineno).strip()])
                    _append(str(_COVERAGE), _json.dumps({"unit": unit, "lines": lines}) + "\n")
                _hits.clear()
                _mon.restart_events()
            except Exception:
                pass

        def fleet_coverage_flush(unit: str) -> None:
            if not _BORN_IN:
                _write(unit)

        _mon.use_tool_id(_TOOL, "fleet-gate-coverage")
        _mon.register_callback(_TOOL, _mon.events.LINE, _on_line)
        _mon.set_events(_TOOL, _mon.events.LINE)
        atexit.register(lambda: _write(_BORN_IN))
    except Exception:
        pass
