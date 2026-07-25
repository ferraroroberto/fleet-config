"""CLI entrypoint for the fleet context filter.

Usage (invoke the resolved Python path directly — a bare ``py``/``python`` is
not reliably on ``PATH`` on this machine; see ``_lib.find_python_executable``):
    E:/automation/fleet-config/.venv/Scripts/python.exe hooks/context_filter_cli.py eval --fixtures tests/fixtures/context_filter
    E:/automation/fleet-config/.venv/Scripts/python.exe hooks/context_filter_cli.py run --tool PowerShell --mode rewrite --encoded <b64>
    E:/automation/fleet-config/.venv/Scripts/python.exe hooks/context_filter_cli.py retrieve <key>
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from statistics import median
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import context_filter  # noqa: E402

DEFAULT_TIMEOUT_SECONDS = 600
# How long to wait for the output pipes to close once the tree has been killed.
KILL_GRACE_SECONDS = 10
NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def _decode_command(encoded: str) -> str:
    return base64.b64decode(encoded.encode("ascii")).decode("utf-8", "replace")


def _powershell_exe() -> str:
    win_ps = "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
    if Path(win_ps).exists():
        return win_ps
    return shutil.which("powershell") or "powershell"


class WrapperTimeout(Exception):
    """The wrapped command exceeded the wrapper timeout and was killed.

    Carries whatever output was recovered plus whether the post-kill collection
    itself had to be abandoned, so the caller can report the two conditions
    distinctly instead of collapsing them into one message.
    """

    def __init__(self, timeout: int, output: str, pipes_abandoned: bool) -> None:
        super().__init__(f"timed out after {timeout}s")
        self.timeout = timeout
        self.output = output
        self.pipes_abandoned = pipes_abandoned

    def reason(self, command: str) -> str:
        if self.pipes_abandoned:
            return (
                f"command timed out after {self.timeout}s; process tree killed but a "
                f"descendant still held the output pipes — capture abandoned: {command}"
            )
        return f"command timed out after {self.timeout}s; process tree killed: {command}"


def _timeout_seconds() -> int:
    """Wrapper timeout, tolerating a malformed env override.

    This wrapper sits in front of *every* Bash/PowerShell tool call fleet-wide,
    so a bad env value must degrade to the default rather than crash the hook.
    """
    raw = os.environ.get("FLEET_CONTEXT_FILTER_TIMEOUT", "")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT_SECONDS
    return value if value > 0 else DEFAULT_TIMEOUT_SECONDS


def _kill_tree(process: subprocess.Popen[str]) -> None:
    """Kill the wrapped command *and every descendant*.

    A bare ``Popen.kill()`` only reaps the direct child. Any grandchild that
    inherited the stdout/stderr pipes keeps their write handles open, and the
    post-kill collection then blocks on a pipe that never reaches EOF — which is
    exactly how a scheduled run wedged for eight hours (fleet-config#411).
    """
    if sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(process.pid)],
                capture_output=True,
                timeout=KILL_GRACE_SECONDS,
                creationflags=NO_WINDOW,
            )
            return
        except (OSError, subprocess.SubprocessError):
            pass  # fall through to the direct-child kill below
    try:
        process.kill()
    except OSError:
        pass


def _run_command(tool: str, command: str, cwd: str | None) -> subprocess.CompletedProcess[str]:
    if tool.lower() == "powershell":
        args = [_powershell_exe(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", command]
    elif tool.lower() == "bash":
        bash = shutil.which("bash")
        args = [bash, "-lc", command] if bash else [command]
    else:
        args = [command]
    timeout = _timeout_seconds()
    # Popen + a bounded two-phase collection rather than subprocess.run(timeout=):
    # run() reacts to a timeout by killing only the direct child and then calling
    # communicate() with NO timeout, which deadlocks whenever a surviving
    # grandchild still holds the pipes (fleet-config#411).
    process = subprocess.Popen(
        args,
        cwd=cwd or None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        # Explicit UTF-8: bare text=True decodes with the locale codec (cp1252
        # here), so any command emitting emoji or box-drawing characters killed
        # the reader thread with UnicodeDecodeError and lost its whole output.
        encoding="utf-8",
        errors="replace",
        shell=(len(args) == 1),
        creationflags=NO_WINDOW,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(process)
        try:
            stdout, stderr = process.communicate(timeout=KILL_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            # Reader threads are daemons, so abandoning the pipes lets this
            # process exit instead of hanging forever on a handle it can't close.
            process.poll()
            raise WrapperTimeout(timeout, "", pipes_abandoned=True) from None
        raise WrapperTimeout(timeout, (stdout or "") + (stderr or ""), pipes_abandoned=False) from None
    return subprocess.CompletedProcess(args, process.returncode, stdout, stderr)


def run_wrapped(args: argparse.Namespace) -> int:
    command = _decode_command(args.encoded)
    try:
        result = _run_command(args.tool, command, args.cwd)
    except WrapperTimeout as exc:
        if exc.output:
            sys.stdout.write(exc.output)
            if not exc.output.endswith("\n"):
                sys.stdout.write("\n")
        # In-band on stdout, not just stderr: a consumer parsing stdout as the
        # command's output (e.g. JSON from a fleet sweep helper) must not be
        # able to mistake truncated content for a complete, well-formed
        # result (fleet-config#424).
        sys.stdout.write(
            f"[fleet-context-filter: OUTPUT TRUNCATED - killed after {exc.timeout}s; "
            "content above may be incomplete]\n"
        )
        print(f"fleet-context-filter: {exc.reason(command)}", file=sys.stderr)
        return 124

    raw = (result.stdout or "") + (result.stderr or "")
    compressed = context_filter.compress_output(command, raw, cache_raw=args.mode == "rewrite")

    if args.mode == "shadow":
        context_filter.append_shadow_log(
            {
                "command": command,
                "tool": args.tool,
                "raw_tokens": compressed.raw_tokens,
                "compressed_tokens": compressed.compressed_tokens,
                "reduction_pct": round(compressed.reduction_pct, 2),
                "duration_ms": round(compressed.duration_ms, 3),
                "exit_code": result.returncode,
            }
        )
        sys.stdout.write(raw)
        return result.returncode

    header = (
        f"[fleet-context-filter: raw_tokens={compressed.raw_tokens} "
        f"compressed_tokens={compressed.compressed_tokens} "
        f"reduction={compressed.reduction_pct:.1f}%"
    )
    if compressed.raw_key:
        header += f" raw_key={compressed.raw_key}"
    if compressed.secret_like:
        header += " secret_like=true raw_not_cached=true"
    header += "]"
    sys.stdout.write(header + "\n" + compressed.compressed + "\n")
    return result.returncode


def _load_manifest(fixtures: Path) -> list[dict[str, Any]]:
    manifest = fixtures / "manifest.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    cases = data.get("cases", [])
    if not isinstance(cases, list):
        raise ValueError("manifest.json must contain a cases array")
    return cases


def run_eval(args: argparse.Namespace) -> int:
    fixtures = Path(args.fixtures)
    cases = _load_manifest(fixtures)
    rows: list[dict[str, Any]] = []
    failures: list[str] = []

    for case in cases:
        name = str(case["name"])
        command = str(case["command"])
        raw = (fixtures / str(case["fixture"])).read_text(encoding="utf-8")
        result = context_filter.compress_output(command, raw)
        missing = [needle for needle in case.get("must_contain", []) if needle not in result.compressed]
        if missing:
            failures.append(f"{name}: missing required text: {missing}")
        min_reduction = float(case.get("min_reduction_pct", 0))
        if result.reduction_pct < min_reduction:
            failures.append(
                f"{name}: reduction {result.reduction_pct:.1f}% below required {min_reduction:.1f}%"
            )
        rows.append(
            {
                "name": name,
                "raw_tokens": result.raw_tokens,
                "compressed_tokens": result.compressed_tokens,
                "reduction_pct": round(result.reduction_pct, 1),
                "raw_lines": result.line_count,
                "compressed_lines": result.compressed_line_count,
                "duration_ms": round(result.duration_ms, 3),
                "missing": missing,
            }
        )

    reductions = [float(row["reduction_pct"]) for row in rows]
    summary = {
        "cases": len(rows),
        "median_reduction_pct": round(median(reductions), 1) if reductions else 0.0,
        "total_raw_tokens": sum(int(row["raw_tokens"]) for row in rows),
        "total_compressed_tokens": sum(int(row["compressed_tokens"]) for row in rows),
        "failures": failures,
    }
    summary["total_reduction_pct"] = round(
        0.0
        if summary["total_raw_tokens"] == 0
        else (summary["total_raw_tokens"] - summary["total_compressed_tokens"])
        / summary["total_raw_tokens"]
        * 100,
        1,
    )

    if args.json:
        print(json.dumps({"summary": summary, "cases": rows}, indent=2, sort_keys=True))
    else:
        print("| case | raw tk | compressed tk | reduction | lines | ms |")
        print("|---|---:|---:|---:|---:|---:|")
        for row in rows:
            print(
                f"| {row['name']} | {row['raw_tokens']} | {row['compressed_tokens']} | "
                f"{row['reduction_pct']}% | {row['raw_lines']} -> {row['compressed_lines']} | "
                f"{row['duration_ms']} |"
            )
        print()
        print(
            f"median reduction: {summary['median_reduction_pct']}% | "
            f"total reduction: {summary['total_reduction_pct']}% | "
            f"tokens: {summary['total_raw_tokens']} -> {summary['total_compressed_tokens']}"
        )
        if failures:
            print()
            for failure in failures:
                print(f"FAIL {failure}")

    min_median = float(args.min_median_reduction)
    if summary["median_reduction_pct"] < min_median:
        failures.append(
            f"median reduction {summary['median_reduction_pct']:.1f}% below required {min_median:.1f}%"
        )
    return 1 if failures else 0


def retrieve(args: argparse.Namespace) -> int:
    path = context_filter.data_dir() / "blobs" / f"{args.key}.txt"
    if not path.exists():
        print(f"raw output not found for key: {args.key}", file=sys.stderr)
        return 1
    sys.stdout.write(path.read_text(encoding="utf-8", errors="replace"))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Fleet command-output context filter")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run_p = sub.add_parser("run", help="execute and optionally compress a command")
    run_p.add_argument("--tool", default="PowerShell", choices=["PowerShell", "Bash"])
    run_p.add_argument("--mode", default="rewrite", choices=["rewrite", "shadow"])
    run_p.add_argument("--encoded", required=True)
    run_p.add_argument("--cwd")
    run_p.set_defaults(func=run_wrapped)

    eval_p = sub.add_parser("eval", help="run the reproducible fixture benchmark")
    eval_p.add_argument("--fixtures", default="tests/fixtures/context_filter")
    eval_p.add_argument("--min-median-reduction", default="35")
    eval_p.add_argument("--json", action="store_true")
    eval_p.set_defaults(func=run_eval)

    retrieve_p = sub.add_parser("retrieve", help="print a cached raw output blob")
    retrieve_p.add_argument("key")
    retrieve_p.set_defaults(func=retrieve)

    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
