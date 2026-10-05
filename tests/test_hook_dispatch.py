"""In-process tests for hooks/hook_dispatch.py's failure paths (fleet-config#1274).

Standalone, against throwaway hook modules in a temp dir, because these shapes
(a crash, a raw `sys.exit(2)`, stray stdout) exist in no real hook, and the
dispatcher must still treat each the way the hook's own process was treated.
The real-hook differential lives in tests/acceptance/checks_dispatch.py.

Run: E:/automation/fleet-config/.venv/Scripts/python.exe tests/test_hook_dispatch.py
Exit 0 = all pass.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "hooks"))
import harness_wire  # noqa: E402
import hook_dispatch  # noqa: E402

FAKES = {
    "fake_crash": "def main():\n    raise RuntimeError('boom')\n",
    "fake_raw_exit": "import sys\ndef main():\n    print('raw refusal', file=sys.stderr)\n    sys.exit(2)\n",
    "fake_noisy_allow": "import _lib\ndef main():\n    print('stray stdout')\n    _lib.read_stdin_json()\n    _lib.allow()\n",
    "fake_warn": "import _lib\ndef main():\n    _lib.read_stdin_json()\n    _lib.warn('nudge one')\n",
    "fake_block": "import _lib\ndef main():\n    _lib.read_stdin_json()\n    _lib.block('refused')\n",
    "fake_swallow": ("import _lib\ndef main():\n    try:\n        _lib.block('still refused')\n"
                     "    except Exception:\n        pass\n    raise AssertionError('ran past its answer')\n"),
}
RAW = json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Bash",
                  "tool_input": {"command": "git status"}}).encode()

failures = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global failures
    print(("OK    " if ok else "FAIL  ") + label + ("" if ok else f"  ({detail})"))
    failures += 0 if ok else 1


def run(name: str):
    verdicts, err = [], io.StringIO()
    with contextlib.redirect_stderr(err):
        answered = hook_dispatch.run_hook(name, RAW, verdicts)
    return answered, verdicts, err.getvalue()


def emit(verdicts):
    out, err = io.StringIO(), io.StringIO()
    sys.stdin = hook_dispatch._stdin_from(RAW)
    harness_wire.read_stdin_json()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            harness_wire.emit_collected(verdicts)
        except SystemExit as exc:
            return exc.code, out.getvalue(), err.getvalue()
    return None, out.getvalue(), err.getvalue()


with tempfile.TemporaryDirectory() as tmp:
    for name, body in FAKES.items():
        (Path(tmp) / f"{name}.py").write_text(body, encoding="utf-8")
    sys.path.insert(0, tmp)
    hook_dispatch.HOOKS_DIR = Path(tmp)

    answered, verdicts, err = run("fake_crash")
    check("a crashing hook fails open for itself, traceback on stderr",
          not answered and verdicts == [] and "RuntimeError: boom" in err, err)

    answered, verdicts, err = run("fake_raw_exit")
    check("a raw sys.exit(2) still refuses, its stderr the reason",
          answered and verdicts == [harness_wire.Verdict("block", "raw refusal")] and err == "", repr(verdicts))

    answered, verdicts, err = run("fake_noisy_allow")
    check("stray stdout goes to stderr, never the JSON channel",
          answered and verdicts == [] and err == "stray stdout\n", repr(err))

    answered, verdicts, _ = run("fake_swallow")
    check("an `except Exception` can't swallow a recorded verdict",
          answered and verdicts == [harness_wire.Verdict("block", "still refused")], repr(verdicts))

    check("the collector is off again after each hook", harness_wire._COLLECTOR is None)

    collected = []
    for name in ("fake_warn", "fake_block", "fake_crash"):
        collected += run(name)[1]
    code, out, err = emit(collected)
    check("a refusal outranks a nudge: exit 2, reason on stderr, stdout clean",
          code == 2 and out == "" and err.strip() == "refused", f"{code} {out!r} {err!r}")

    code, out, _ = emit(run("fake_warn")[1] + run("fake_warn")[1])
    check("nudges merge into one systemMessage",
          code == 0 and json.loads(out) == {"systemMessage": "nudge one\n\nnudge one"}, f"{code} {out!r}")

    code, out, err = emit([])
    check("no verdict is a silent allow", (code, out, err) == (0, "", ""), f"{code} {out!r} {err!r}")

sys.exit(1 if failures else 0)
