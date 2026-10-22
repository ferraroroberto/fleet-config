"""Context-filter checks that drive `context_filter_cli.py run` as a real subprocess: timeouts, fail-open, banner, encoding, shell choice."""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from acceptance.shared import HOOKS, PYTHON, REPO, _Checker


def fixture_eval(check: _Checker) -> None:
    res = subprocess.run(
        [
            PYTHON,
            str(HOOKS / "context_filter_cli.py"),
            "eval",
            "--fixtures",
            str(REPO / "tests" / "fixtures" / "context_filter"),
            "--min-median-reduction",
            "35",
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    check(
        "context_filter_eval: fixture benchmark passes",
        res.returncode == 0 and "median reduction:" in res.stdout,
        res.stdout + res.stderr,
    )


def timeout_kills_process_tree(check: _Checker) -> None:
    # ---- wrapper timeout must not outlive a pipe-holding grandchild (#411) ----
    # subprocess.run(capture_output, timeout=) reacts to a timeout by killing only
    # the direct child, then collecting output with NO timeout — so a surviving
    # grandchild that inherited the pipes blocks it forever. That wedged a
    # scheduled run for eight hours. The wrapper must kill the tree and return
    # 124 well before the grandchild would have exited on its own.
    with tempfile.TemporaryDirectory() as tmp:
        probe = Path(tmp) / "hold_pipe.py"
        probe.write_text(
            "import subprocess, sys, time\n"
            "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
            "time.sleep(120)\n",
            encoding="utf-8",
        )
        command = f'& "{PYTHON.replace(chr(92), "/")}" "{str(probe).replace(chr(92), "/")}"'
        encoded = base64.b64encode(command.encode("utf-8")).decode("ascii")
        started = time.monotonic()
        timed_out = False
        try:
            res = subprocess.run(
                [
                    PYTHON,
                    str(HOOKS / "context_filter_cli.py"),
                    "run",
                    "--tool",
                    "PowerShell",
                    "--mode",
                    "shadow",
                    "--encoded",
                    encoded,
                ],
                capture_output=True,
                text=True,
                env={**os.environ, "FLEET_CONTEXT_FILTER_TIMEOUT": "3"},
                timeout=60,
            )
        except subprocess.TimeoutExpired:
            timed_out = True
        elapsed = time.monotonic() - started
        check(
            "context_filter_cli: pipe-holding grandchild does not outlive the wrapper timeout (fleet-config#411)",
            not timed_out and res.returncode == 124 and elapsed < 45,
            f"timed_out={timed_out} elapsed={elapsed:.1f}s "
            + ("" if timed_out else f"rc={res.returncode} stderr={res.stderr.strip()}"),
        )
        check(
            "context_filter_cli: timeout message names the tree kill",
            not timed_out and "process tree killed" in res.stderr,
            "" if timed_out else res.stderr.strip(),
        )


def timeout_carries_truncation_marker(check: _Checker) -> None:
    # ---- timed-out stdout must carry an in-band truncation marker (#424) ----
    # A consumer reading stdout as the command's output (e.g. JSON from a fleet
    # sweep helper) must not be able to mistake a wrapper-timeout truncation for
    # complete output. The marker has to land on stdout itself, not just stderr.
    with tempfile.TemporaryDirectory() as tmp:
        probe = Path(tmp) / "slow_stdout.py"
        probe.write_text(
            "import sys, time\n"
            "for i in range(30):\n"
            "    sys.stdout.write(str(i) + chr(10))\n"
            "    sys.stdout.flush()\n"
            "    time.sleep(1)\n",
            encoding="utf-8",
        )
        command = f'& "{PYTHON.replace(chr(92), "/")}" "{str(probe).replace(chr(92), "/")}"'
        encoded = base64.b64encode(command.encode("utf-8")).decode("ascii")
        res = subprocess.run(
            [
                PYTHON,
                str(HOOKS / "context_filter_cli.py"),
                "run",
                "--tool",
                "PowerShell",
                "--mode",
                "shadow",
                "--encoded",
                encoded,
            ],
            capture_output=True,
            text=True,
            env={**os.environ, "FLEET_CONTEXT_FILTER_TIMEOUT": "3"},
            timeout=60,
        )
        check(
            "context_filter_cli: timed-out stdout carries an in-band truncation marker (fleet-config#424)",
            res.returncode == 124 and "OUTPUT TRUNCATED" in res.stdout and "0" in res.stdout,
            res.stdout.strip() + " | " + res.stderr.strip(),
        )


def filter_fault_fails_open(check: _Checker) -> None:
    # (e) fail open, loudly: the command has already run, so a fault in the
    # filter itself must cost neither the output nor the exit code. Driven by
    # pointing FLEET_CONTEXT_FILTER_DIR at a *file*, which makes the blob-cache
    # mkdir and the telemetry append both raise mid-compression.
    with tempfile.TemporaryDirectory() as tmp:
        not_a_dir = Path(tmp) / "occupied"
        not_a_dir.write_text("this is a file, not a directory", encoding="utf-8")
        failing_cmd = 'Write-Output "payload the agent must still see"; exit 3'
        res = subprocess.run(
            [
                PYTHON,
                str(HOOKS / "context_filter_cli.py"),
                "run",
                "--tool", "PowerShell",
                "--mode", "rewrite",
                "--encoded", base64.b64encode(failing_cmd.encode("utf-8")).decode("ascii"),
            ],
            capture_output=True,
            text=True,
            env={**os.environ, "FLEET_CONTEXT_FILTER_DIR": str(not_a_dir)},
            timeout=30,
        )
        check(
            "context_filter_cli: a filter fault fails open with raw output and the "
            "command's own exit code (fleet-config#837)",
            res.returncode == 3
            and "payload the agent must still see" in res.stdout
            and "FILTER FAILED" in res.stdout,
            f"rc={res.returncode} stdout={res.stdout.strip()[:200]} | {res.stderr.strip()[:200]}",
        )


def banner_reports_withheld_lines(check: _Checker) -> None:
    # (f) heavy filtering is legible as such: `lines=N->M` in the banner is what
    # separates "mostly withheld" from "the command printed nothing".
    with tempfile.TemporaryDirectory() as tmp:
        # One simple command: a `;`-joined list is compound output, returned
        # whole (fleet-config#1268), and this case is about withheld lines.
        long_cmd = "Write-Output ((0..199) -replace '^(.*)$', 'row $1 aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa')"
        res = subprocess.run(
            [
                PYTHON,
                str(HOOKS / "context_filter_cli.py"),
                "run",
                "--tool", "PowerShell",
                "--mode", "rewrite",
                "--encoded", base64.b64encode(long_cmd.encode("utf-8")).decode("ascii"),
            ],
            capture_output=True,
            text=True,
            env={**os.environ, "FLEET_CONTEXT_FILTER_DIR": tmp},
            timeout=60,
        )
        banner = res.stdout.splitlines()[0] if res.stdout.strip() else ""
        check(
            "context_filter_cli: banner reports lines=N->M and points at the full output (fleet-config#837)",
            res.returncode == 0
            and "lines=200->" in banner
            and "raw_key=" in banner
            and "retrieve " in res.stdout,
            f"rc={res.returncode} banner={banner}",
        )
        # ...and so does a single line truncated at the char budget, whose
        # line count cannot drop (fleet-config#1269).
        one_line = "Write-Output ('[' + ((1..4000) -join ',') + ']')"
        one_line_chars = len("[" + ",".join(str(i) for i in range(1, 4001)) + "]")
        res = subprocess.run(
            [
                PYTHON,
                str(HOOKS / "context_filter_cli.py"),
                "run",
                "--tool", "PowerShell",
                "--mode", "rewrite",
                "--encoded", base64.b64encode(one_line.encode("utf-8")).decode("ascii"),
            ],
            capture_output=True,
            text=True,
            env={**os.environ, "FLEET_CONTEXT_FILTER_DIR": tmp},
            timeout=60,
        )
        check(
            "context_filter_cli: a truncated single line still points at the full output (fleet-config#1269)",
            res.returncode == 0 and f"of {one_line_chars} chars" in res.stdout and "retrieve " in res.stdout,
            f"rc={res.returncode} tail={res.stdout.strip()[-300:]}",
        )


def whole_output_carries_no_banner(check: _Checker) -> None:
    # (g) output that comes back whole carries no banner: ~30 tokens on every
    # call outweighed the filter's whole saving once most commands are wrapped
    # (fleet-config#1268). Nothing withheld means nothing to retrieve, either.
    with tempfile.TemporaryDirectory() as tmp:
        whole_cmd = 'Write-Output "first line"; Write-Output "second line"'
        res = subprocess.run(
            [
                PYTHON, str(HOOKS / "context_filter_cli.py"), "run",
                "--tool", "PowerShell", "--mode", "rewrite",
                "--encoded", base64.b64encode(whole_cmd.encode("utf-8")).decode("ascii"),
            ],
            capture_output=True, text=True, env={**os.environ, "FLEET_CONTEXT_FILTER_DIR": tmp}, timeout=60,
        )
        check(
            "context_filter_cli: output returned whole comes back raw, no banner, no blob (fleet-config#1268)",
            res.returncode == 0
            and res.stdout.replace("\r\n", "\n") == "first line\nsecond line\n"
            and not (Path(tmp) / "blobs").exists()
            and (Path(tmp) / "shadow.jsonl").exists(),
            f"rc={res.returncode} stdout={res.stdout!r}",
        )
        (Path(tmp) / "mode.json").write_text(json.dumps({"mode": "rewrite"}), encoding="utf-8")
        res = subprocess.run(
            [PYTHON, str(HOOKS / "context_filter_cli.py"), "compress", "--agent", "pi"],
            input=json.dumps({"command": "git status --short", "output": " M a.py\n"}),
            capture_output=True, text=True,
            env={**os.environ, "FLEET_CONTEXT_FILTER_MODE": "", "FLEET_CONTEXT_FILTER_DIR": tmp}, timeout=30,
        )
        reply = json.loads(res.stdout) if res.returncode == 0 and res.stdout.strip() else {}
        check(
            "context_filter_cli: compress leaves a whole output unwrapped (fleet-config#1268)",
            reply.get("wrap") is False and reply.get("mode") == "rewrite",
            f"rc={res.returncode} reply={reply}",
        )


def survives_non_cp1252_output(check: _Checker) -> None:
    # ---- wrapper stdout re-emission survives non-cp1252 output (#426) ----
    # The wrapped child's own output is decoded as explicit UTF-8 in
    # _run_command, but context_filter_cli.py's own sys.stdout.write of that
    # output falls back to the locale codec (cp1252 here) unless reconfigured
    # to UTF-8 up front. An emoji/astral codepoint used to crash the wrapper
    # with UnicodeEncodeError instead of passing the output through.
    emoji_command = "Write-Output ([System.Char]::ConvertFromUtf32(0x1F4CA))"
    encoded = base64.b64encode(emoji_command.encode("utf-8")).decode("ascii")
    with tempfile.TemporaryDirectory() as tmp:
        for mode in ("shadow", "rewrite"):
            res = subprocess.run(
                [
                    PYTHON,
                    str(HOOKS / "context_filter_cli.py"),
                    "run",
                    "--tool",
                    "PowerShell",
                    "--mode",
                    mode,
                    "--encoded",
                    encoded,
                ],
                capture_output=True,
                text=True,
                # The wrapper re-emits the emoji as UTF-8; the locale codec would
                # misdecode (or raise on) its bytes.
                encoding="utf-8",
                errors="replace",
                # FLEET_CONTEXT_FILTER_DIR: keep test rows out of the machine's
                # real telemetry — the launcher stats panel reads it now (#541).
                env={**os.environ, "PYTHONUTF8": "0", "FLEET_CONTEXT_FILTER_DIR": tmp},
                timeout=30,
            )
            check(
                f"context_filter_cli: {mode} mode survives non-cp1252 output (fleet-config#426)",
                res.returncode == 0 and "UnicodeEncodeError" not in res.stderr,
                res.stdout.strip() + " | " + res.stderr.strip(),
            )


def bash_body_runs_non_login(check: _Checker) -> None:
    import _lib  # noqa: E402

    git_bash = _lib.git_bash_exe()

    # ---- Bash bodies run in a non-login shell, in the caller's environment (#1272) ----
    # `bash -lc` re-sourced /etc/profile on every wrapped call (~280 ms) and
    # replaced the harness's environment with the profile's: LANG came back as
    # en_US.UTF-8 where the harness has none, which changed `sort` and `wc -m`
    # output against the same command run unwrapped.
    if not git_bash:
        check.advisory(
            "context_filter_cli: Bash body runs non-login in the caller's environment (fleet-config#1272)",
            False,
            "no Git Bash found (CLAUDE_CODE_GIT_BASH_PATH, git --exec-path, PATH): not established",
        )
    else:
        body = 'shopt -q login_shell && echo login || echo plain; echo "LANG=${LANG-unset}"'
        encoded = base64.b64encode(body.encode("utf-8")).decode("ascii")
        with tempfile.TemporaryDirectory() as tmp:
            env = {k: v for k, v in os.environ.items() if k not in {"LANG", "LC_ALL", "LC_CTYPE"}}
            res = subprocess.run(
                [PYTHON, str(HOOKS / "context_filter_cli.py"), "run", "--tool", "Bash",
                 "--mode", "rewrite", "--encoded", encoded, "--cwd", tmp],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                env={**env, "FLEET_CONTEXT_FILTER_DIR": tmp}, timeout=60,
            )
        check(
            "context_filter_cli: Bash body runs non-login in the caller's environment (fleet-config#1272)",
            res.returncode == 0 and res.stdout.split() == ["plain", "LANG=unset"],
            res.stdout.strip() + " | " + res.stderr.strip(),
        )

        # fleet-config#1399: a launcher-spawned session's PATH lacks Git's usr\bin
        # and puts the WSL launcher first. The body must still run in Git Bash.
        if sys.platform == "win32":
            kept = [d for d in os.environ.get("PATH", "").split(os.pathsep) if d and "git/usr/" not in d.lower().replace("\\", "/")]
            wsl_first = os.pathsep.join([os.path.join(os.environ.get("SystemRoot", "C:/Windows"), "System32"), *kept])
            uname = base64.b64encode(b"echo $OSTYPE").decode("ascii")
            with tempfile.TemporaryDirectory() as tmp:
                res = subprocess.run(
                    [PYTHON, str(HOOKS / "context_filter_cli.py"), "run", "--tool", "Bash",
                     "--mode", "rewrite", "--encoded", uname, "--cwd", tmp],
                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                    env={**os.environ, "PATH": wsl_first, "FLEET_CONTEXT_FILTER_DIR": tmp}, timeout=60,
                )
            check(
                "context_filter_cli: Bash body runs in Git Bash when PATH puts the WSL launcher first (fleet-config#1399)",
                res.returncode == 0 and "msys" in res.stdout.lower(),
                res.stdout.strip() + " | " + res.stderr.strip(),
            )


def import_paths_defer_heavy_modules(check: _Checker) -> None:
    # The hook imports context_filter on every shell call and the wrapper adds
    # its own interpreter start, so modules only `eval` or a cached blob need
    # stay out of both import paths.
    probe = (
        "import sys; sys.path.insert(0, sys.argv[1]); import context_filter_cli, context_filter_hook; "
        "print(sorted(m for m in ('hashlib', 'statistics') if m in sys.modules))"
    )
    res = subprocess.run([PYTHON, "-c", probe, str(HOOKS)], capture_output=True, text=True, timeout=30)
    check(
        "context_filter: hook and wrapper import paths defer hashlib and statistics (fleet-config#1272)",
        res.returncode == 0 and res.stdout.strip() == "[]",
        res.stdout.strip() + " | " + res.stderr.strip(),
    )

