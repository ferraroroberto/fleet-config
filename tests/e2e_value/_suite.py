"""Shared preamble of the e2e_value suite: import paths, the harness, the call-recording proxy `v`, and the
synthetic progress logs.

Imported first by every part and by the entry (the script's own folder is on `sys.path`).
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "skills" / "_lib"))
import e2e_value as _e2e_value  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check

# ---- characterization pin (fleet-config#1247) ----------------------------------------------------------
# Every call this file makes through `v` is recorded with its full result (or the exception it raised),
# and the record must equal a golden captured before e2e_value.py was split into modules. The asserts
# below check chosen fields; the golden checks everything else they return. `v` is a proxy, never a
# patched module, so a call inside e2e_value is not recorded and how the code is divided can't change
# the record. Temp dirs and today's date are normalised. Regenerate only for an intended behaviour
# change: E2E_VALUE_WRITE_CALL_GOLDEN=1.
import inspect  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402
from datetime import datetime, timezone  # noqa: E402

CALL_GOLDEN = Path(__file__).resolve().parent.parent / "fixtures" / "e2e_value_calls.json"
SURFACE_GOLDEN = Path(__file__).resolve().parent.parent / "fixtures" / "e2e_value_surface.json"
_TMP_ROOT = Path(tempfile.gettempdir()).resolve()
_TMP_RE = re.compile(
    "(?:" + "|".join(re.escape(s) for s in {str(_TMP_ROOT), _TMP_ROOT.as_posix(), tempfile.gettempdir()}) + ")"
    r"[\\/]([^\\/'\"\s]*?)[a-z0-9_]{8}(?=[\\/'\"\s]|$)", re.I)
_TMP_NAME_RE = re.compile(r"\b(e2e-value-(?:[a-z]+-)*|git_fixture_)[a-z0-9_]{8}\b")
_TODAY = datetime.now(timezone.utc).date().isoformat()


def _norm(value: object) -> object:
    """`value` as JSON-able data with temp dirs and today's date masked."""
    if isinstance(value, dict):
        return {str(_norm(k)): _norm(x) for k, x in value.items()}
    if isinstance(value, (list, tuple)):
        return [_norm(x) for x in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_norm(x) for x in value), key=lambda x: json.dumps(x, sort_keys=True))
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    if inspect.ismodule(value):  # the classifier, loaded under a per-load name: its file is what's stable
        value = Path(getattr(value, "__file__", None) or value.__name__)
    text = value.as_posix() if isinstance(value, Path) else value if isinstance(value, str) else repr(value)
    text = _TMP_RE.sub(lambda m: "<TMP>/" + m.group(1), text).replace("\\", "/")
    text = _TMP_NAME_RE.sub(lambda m: m.group(1), text)  # a temp dir's name used bare (a per-repo state file)
    return text.replace(_TODAY, "<TODAY>")


class _CallPin:
    """`e2e_value` with every function call this file makes recorded."""

    def __init__(self, module) -> None:
        self._module, self.calls = module, []

    def __getattr__(self, name: str):
        attr = getattr(self._module, name)
        if not inspect.isfunction(attr):
            return attr

        def recorded(*args, **kwargs):
            try:
                result = attr(*args, **kwargs)
            except Exception as exc:
                self.calls.append([name, "raised", _norm(f"{type(exc).__name__}: {exc}")])
                raise
            self.calls.append([name, _norm(result)])
            return result
        return recorded


v = _CallPin(_e2e_value)


def _log(day: str, start: str, lines: list) -> str:
    return f"verify-before-ship run started {day} {start}\n" + "".join(l + "\n" for l in lines)


# Run 1: serial, both projections, a WebKit red that stopped at line 40 of its test.
RUN1 = _log("2026-09-20", "10:00:00", [
    "[10:00:00 +    0,0s] ==> phase: pytest (non-e2e)...",
    "[10:00:01 +    0.1s] START tests/test_api.py::test_ok",
    "[10:00:01 +    0.2s] DONE  tests/test_api.py::test_ok (0.1s)",
    "[10:01:00 +    1,0s] ==> phase: pytest e2e (tests/e2e)...",
    "[10:01:00 +    0.1s] START tests/e2e/test_board.py::test_load[chromium]",
    "[10:01:02 +    2.0s] DONE  tests/e2e/test_board.py::test_load[chromium] (2.0s)",
    "[10:01:02 +    2.0s] START tests/e2e/test_board.py::test_load[webkit]",
    "[10:01:06 +    6.0s] DONE  tests/e2e/test_board.py::test_load[webkit] (4.0s)",
    "[10:01:06 +    6.0s] START tests/e2e/test_chat.py::test_link_opens[webkit-iphone]",
    "[10:01:18 +   18.0s] FAILED (call) tests/e2e/test_chat.py::test_link_opens[webkit-iphone]",
    "    | tests/e2e/test_chat.py:40: in test_link_opens",
    "    |     expect(row).to_be_visible()",
    "    | E   AssertionError: timed out",
    "[10:01:18 +   18.0s] DONE  tests/e2e/test_chat.py::test_link_opens[webkit-iphone] (12.0s)",
    "[10:01:18 +   18.0s] START tests/e2e/test_redact.py::test_mask[{'a': 1} b]",
    "[10:01:18 +   18.5s] DONE  tests/e2e/test_redact.py::test_mask[{'a': 1} b] (0.5s)",
    "[10:01:18 +   18.5s] pytest session finished (exit status 1)",
])
# Run 2: the same test red again at a different step, on two xdist workers.
RUN2 = _log("2026-09-21", "23:59:50", [
    "[23:59:50 +    0,0s] ==> phase: pytest e2e (tests/e2e)...",
    "[23:59:50 +    0.1s] START tests/e2e/test_chat.py::test_link_opens[webkit-iphone]",
    "[00:00:05 +   15.0s] FAILED (call) tests/e2e/test_chat.py::test_link_opens[webkit-iphone] [gw1]",
    "    | tests\\e2e\\test_chat.py:52: in test_link_opens",
    "    | E   AssertionError: wrong conversation",
    "[00:00:05 +   15.0s] DONE  tests/e2e/test_chat.py::test_link_opens[webkit-iphone] (15.0s) [gw1]",
    "[00:00:05 +   15.0s] pytest session finished (exit status 1)",
])
# An unfinished run: a START with no DONE. Never "the last completed run".
RUN3 = _log("2026-09-22", "09:00:00", [
    "[09:00:00 +    0,0s] ==> phase: pytest e2e (tests/e2e)...",
    "[09:00:00 +    0.1s] START tests/e2e/test_board.py::test_load[chromium]",
])
