"""Unit tests for skills/_lib/vendored_wave/gate_ledger.py: gate wall-clock of re-vendor PRs.

Drives the real CLI as a subprocess against a throwaway hooks-state dir (CLAUDE_HOOKS_STATE_DIR),
so the ledger path, the exit-code mapping and the rendered run total are the shipped ones.
A duration that cannot be read must come out `unknown`, never 0.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/repo/test_vendored_gate_ledger.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
from no_window import NO_WINDOW  # noqa: E402
from vendored_wave import gate_ledger as gl  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check

CLI = REPO / "skills" / "_lib" / "vendored_wave" / "gate_ledger.py"
WAVE = "abc1234-20261026T200000Z"
RED_WAVE = "def5678-20261026T210000Z"


def cli(state: Path, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "CLAUDE_HOOKS_STATE_DIR": str(state), "PYTHONUTF8": "1"}
    return subprocess.run([sys.executable, str(CLI), *args], capture_output=True, text=True, env=env,
                          encoding="utf-8", creationflags=NO_WINDOW)


def ledger_rows(state: Path) -> list:
    path = state / gl.LEDGER_DIRNAME / gl.LEDGER_FILENAME
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def py(code: str) -> list:
    return [sys.executable, "-c", code]


state = Path(tempfile.mkdtemp(prefix="gate-ledger-"))
try:
    # ---- pure helpers ----
    check(gl.result_for_exit(0) == "PASS" and gl.result_for_exit(1) == "FAIL" and gl.result_for_exit(3) == "NOT_CONFIRMED"
          and gl.result_for_exit(None) == "NOT_CONFIRMED", "result_for_exit: 0 PASS, 3 and a missing code NOT CONFIRMED, else FAIL")
    check([gl._valid_seconds(v) for v in (None, -1, float("inf"), float("nan"), "x", True, 0, 2.5)]
          == [None, None, None, None, None, None, 0.0, 2.5], "_valid_seconds: unreadable readings are None, a real zero stays 0")
    check(gl.fmt_seconds(None) == "unknown" and gl.fmt_seconds(0.0) == "0.0s" and gl.fmt_seconds(125) == "2m05s",
          "fmt_seconds: None is 'unknown', never 0")
    check(gl.new_wave_id("0123456789abcdef").startswith("0123456-") and gl.new_wave_id("0123456789abcdef").endswith("Z"),
          "new_wave_id: short sha plus a UTC stamp")

    # ---- run: observed duration and exit code ----
    out = cli(state, "run", "--wave", WAVE, "--repo", "alpha", "--", *py("import time; time.sleep(0.3)"))
    check(out.returncode == 0 and "LEDGER=recorded repo=alpha result=PASS" in out.stdout, f"run: a passing gate exits 0 and reports PASS ({out.stdout!r} {out.stderr!r})")
    out = cli(state, "run", "--wave", WAVE, "--repo", "beta", "--", *py("import sys; sys.exit(1)"))
    check(out.returncode == 1 and "result=FAIL" in out.stdout, "run: a failing gate keeps its own exit code and records FAIL")
    out = cli(state, "run", "--wave", WAVE, "--repo", "gamma", "--", *py("import sys; sys.exit(3)"))
    check(out.returncode == 3 and "result=NOT_CONFIRMED" in out.stdout, "run: exit 3 is recorded NOT_CONFIRMED, not FAIL or PASS")
    out = cli(state, "run", "--wave", WAVE, "--repo", "delta", "--", str(state / "no-such-gate.exe"))
    check(out.returncode == 3 and "seconds=unknown" in out.stdout, "run: a gate that cannot be spawned is NOT CONFIRMED with an unknown duration")
    rows = {r["repo"]: r for r in ledger_rows(state)}
    check(rows["alpha"]["seconds"] is not None and rows["alpha"]["seconds"] >= 0.25, f"run: the gate's wall-clock is recorded ({rows['alpha']['seconds']})")
    check(rows["delta"]["seconds"] is None and rows["delta"]["result"] == "NOT_CONFIRMED", "ledger: an unspawnable gate has a null duration, not 0")

    # ---- a red gate keeps its cause: output streams through, a tail and the failed stage are recorded ----
    stage_gate = py("import sys\n" + "\n".join(f"print('line {i}')" for i in range(80)) +
                    "\nprint('>> lint'); print('[PASS] lint'); print('>> unit tests', flush=True); print('boom', file=sys.stderr); sys.exit(1)")
    out = cli(state, "run", "--wave", RED_WAVE, "--repo", "red", "--", *stage_gate)
    check(out.returncode == 1 and "boom" in out.stdout and "line 79" in out.stdout and out.stdout.index("boom") < out.stdout.index("LEDGER=recorded"),
          f"run: the gate's stdout and stderr stream through, before the LEDGER line ({out.stdout[-120:]!r})")
    red = {r["repo"]: r for r in ledger_rows(state)}["red"]
    check(red.get("failed_stage") == "unit tests", f"run: failed_stage names the last stage banner ({red.get('failed_stage')!r})")
    tail_file = state / gl.LEDGER_DIRNAME / str(red.get("output"))
    tail_lines = tail_file.read_text(encoding="utf-8").splitlines() if red.get("output") else []
    check(len(tail_lines) == gl.TAIL_LINES and tail_lines[-1] == "boom" and tail_lines[-2] == ">> unit tests",
          f"run: the tail file holds the last {gl.TAIL_LINES} lines of the combined output ({len(tail_lines)})")
    check(str(red.get("output")).startswith(f"{gl.OUTPUT_DIRNAME}/{RED_WAVE}/red-") and "\\" not in str(red.get("output")),
          f"run: the record names the tail file relative to the ledger dir ({red.get('output')!r})")

    out = cli(state, "run", "--wave", RED_WAVE, "--repo", "nostage", "--", *py("print('something broke'); raise SystemExit(2)"))
    nostage = {r["repo"]: r for r in ledger_rows(state)}["nostage"]
    check(out.returncode == 2 and nostage.get("failed_stage") is None and "failed_stage" in nostage and nostage.get("output"),
          f"run: output with no stage records failed_stage null, still keeps the tail ({nostage})")
    out = cli(state, "run", "--wave", RED_WAVE, "--repo", "pyt", "--", *py(
        "print('FAILED tests/test_a.py::test_x - assert 1 == 2'); print('FAILED tests/test_b.py::test_y'); raise SystemExit(1)"))
    check({r["repo"]: r for r in ledger_rows(state)}["pyt"]["failed_stage"] == "tests/test_a.py::test_x",
          "run: with no banner, failed_stage is the first pytest FAILED node")
    out = cli(state, "run", "--wave", RED_WAVE, "--repo", "pass2", "--", *py("print('>> lint'); print('ok')"))
    ok_row = {r["repo"]: r for r in ledger_rows(state)}["pass2"]
    check(out.returncode == 0 and "output" not in ok_row and "failed_stage" not in ok_row
          and not list((state / gl.LEDGER_DIRNAME / gl.OUTPUT_DIRNAME / RED_WAVE).glob("pass2-*")),
          "run: a passing gate writes no tail file and keeps the original record fields")
    check(set(rows["alpha"]) == {"wave", "repo", "result", "seconds", "utc"}, "ledger: a pass record has exactly the original fields")
    big = cli(state, "run", "--wave", RED_WAVE, "--repo", "big", "--", *py("[print('y' * 400) for _ in range(60)]; raise SystemExit(1)"))
    big_row = {r["repo"]: r for r in ledger_rows(state)}["big"]
    big_bytes = (state / gl.LEDGER_DIRNAME / str(big_row["output"])).read_bytes()
    check(0 < len(big_bytes) <= gl.TAIL_BYTES and big_bytes.endswith(b"y\n"), f"run: the tail file is capped at TAIL_BYTES ({len(big_bytes)})")
    check(big.returncode == 1, "run: a capped tail does not change the exit code")

    red_text = cli(state, "summary", "--wave", RED_WAVE).stdout
    check("red   " in red_text and "failed stage: unit tests" in red_text and f"output tail: {state / gl.LEDGER_DIRNAME / red['output']}" in red_text,
          f"summary: a red repo shows its failed stage and the tail path ({red_text!r})")
    check("nostage" in red_text and "failed stage: unknown" in red_text, "summary: a red with no stage renders 'unknown'")
    check("pass2   " in red_text and red_text.count("failed stage:") == 4, "summary: a passing repo shows no failed stage")
    red_json = {r["repo"]: r for r in json.loads(cli(state, "summary", "--wave", RED_WAVE, "--format", "json").stdout)["repos"]}
    check(red_json["red"]["failed_stage"] == "unit tests" and red_json["red"]["output"] == red["output"]
          and red_json["nostage"]["failed_stage"] is None and red_json["pass2"]["output"] is None,
          "summary json: carries failed_stage and output per repo, null when none")

    # ---- record, then summary ----
    cli(state, "record", "--wave", WAVE, "--repo", "epsilon", "--result", "PASS")
    cli(state, "record", "--wave", "other-wave", "--repo", "alpha", "--result", "PASS", "--seconds", "999")
    check({r["repo"]: r["seconds"] for r in ledger_rows(state)}["epsilon"] is None, "record: omitting --seconds stores null")
    text = cli(state, "summary", "--wave", WAVE, "--expect", "alpha,beta,gamma,delta,epsilon,zeta").stdout
    check("zeta   unknown   no result recorded" in text, f"summary: an expected repo with no record is unknown ({text!r})")
    check("delta   unknown   NOT CONFIRMED" in text and "epsilon   unknown   PASS" in text, "summary: unknown durations print as 'unknown'")
    check("lower bound: 3 repo(s) unknown" in text, "summary: a total that includes unknowns says it is a lower bound")
    check("999" not in text, "summary: another wave's records are not counted")
    data = json.loads(cli(state, "summary", "--wave", WAVE, "--expect", "alpha,zeta", "--format", "json").stdout)
    check(data["unknown"] == 3 and data["known"] == 3 and data["results"]["PASS"] == 2 and data["results"]["FAIL"] == 1,
          f"summary json: counts known/unknown repos and results ({data['known']}/{data['unknown']} {data['results']})")

    # ---- a re-run adds its time; one unknown run makes the repo unknown ----
    s = gl.summarize([{"repo": "r", "result": "FAIL", "seconds": 10}, {"repo": "r", "result": "PASS", "seconds": 5.5}])
    check(s["repos"][0]["seconds"] == 15.5 and s["repos"][0]["runs"] == 2 and s["repos"][0]["result"] == "PASS" and s["total_seconds"] == 15.5,
          "summarize: a gate re-run adds its wall-clock and the last result wins")
    s = gl.summarize([{"repo": "r", "result": "FAIL", "seconds": None}, {"repo": "r", "result": "PASS", "seconds": 5}])
    check(s["repos"][0]["seconds"] is None and s["unknown"] == 1 and s["total_seconds"] == 0, "summarize: one unreadable run makes the repo unknown, not partial")
    only_unknown = gl.render_text("w", gl.summarize([], ["a"]))
    check("total gate time: unknown" in only_unknown and "lower bound" not in only_unknown, "render_text: nothing measured gives total 'unknown', not 0s")
    check("no gate run recorded" in gl.render_text("w", gl.summarize([])), "render_text: an empty wave says nothing was recorded")

    # ---- a torn or foreign ledger line is skipped ----
    ledger = state / gl.LEDGER_DIRNAME / gl.LEDGER_FILENAME
    with open(ledger, "a", encoding="utf-8") as handle:
        handle.write('{"wave": "' + WAVE + '", "repo"\nnot json\n[1]\n')
    check(len(gl.load_wave(WAVE, ledger)) == 5, "load_wave: unparseable lines are skipped, readable records survive")
    check(gl.load_wave(WAVE, state / "missing.jsonl") == [], "load_wave: a missing ledger is an empty wave")
finally:
    shutil.rmtree(state, ignore_errors=True)


_h.report_and_exit("test_vendored_gate_ledger")
