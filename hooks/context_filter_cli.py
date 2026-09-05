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
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib  # noqa: E402
import context_filter  # noqa: E402

DEFAULT_TIMEOUT_SECONDS = 600
# How long to wait for the output pipes to close once the tree has been killed.
KILL_GRACE_SECONDS = 10


def _decode_command(encoded: str) -> str:
    return base64.b64decode(encoded.encode("ascii")).decode("utf-8", "replace")


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
                creationflags=_lib.NO_WINDOW,
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
        args = [_lib.powershell_exe(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", command]
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
        creationflags=_lib.NO_WINDOW,
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


def _log_row(
    *,
    mode: str,
    agent: str,
    session_id: str | None,
    cwd: str | None,
    command: str,
    tool: str,
    compressed: Any,
    exit_code: int | None,
) -> None:
    """Append one telemetry row for a compressed command, in either agent path.

    Written in BOTH shadow and rewrite (fleet-config#541): rewrite rows are what
    the app-launcher stats panel reads after the #392 flip, so a shadow-only
    writer would go dark the moment the filter starts earning its keep.

    One writer for `run_wrapped` (Claude/agy/Copilot) and `run_compress` (Pi), because
    the panel reads both agents' rows out of the same log and therefore needs
    one payload shape — two hand-kept copies could only ever drift into a
    half-readable log (fleet-config#677).
    """
    context_filter.append_shadow_log(
        {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "mode": mode,
            "agent": agent,
            "session_id": session_id or None,
            "cwd": cwd or None,
            "command": command,
            "tool": tool,
            "raw_tokens": compressed.raw_tokens,
            "compressed_tokens": compressed.compressed_tokens,
            "reduction_pct": round(compressed.reduction_pct, 2),
            "duration_ms": round(compressed.duration_ms, 3),
            "exit_code": exit_code,
        }
    )


def _header(compressed: Any) -> str:
    """The one-line `[fleet-context-filter: ...]` banner prefixed to rewritten
    output. Shared by both agent paths for the same reason as `_log_row`: the
    banner is a parsed contract (the app-launcher stats panel and the fixture
    eval both read it), not decoration."""
    header = (
        f"[fleet-context-filter: raw_tokens={compressed.raw_tokens} "
        f"compressed_tokens={compressed.compressed_tokens} "
        f"reduction={compressed.reduction_pct:.1f}%"
    )
    if compressed.raw_key:
        header += f" raw_key={compressed.raw_key}"
    if compressed.secret_like:
        header += " secret_like=true raw_not_cached=true"
    return header + "]"


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

    _log_row(
        mode=args.mode,
        agent=args.agent or "claude",
        session_id=args.session_id,
        cwd=args.cwd,
        command=command,
        tool=args.tool,
        compressed=compressed,
        exit_code=result.returncode,
    )

    if args.mode == "shadow":
        sys.stdout.write(raw)
        return result.returncode

    sys.stdout.write(_header(compressed) + "\n" + compressed.compressed + "\n")
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


def run_compress(args: argparse.Namespace) -> int:
    """Compress an already-captured tool output (fleet-config#545).

    The Pi port's entry: Pi's ``tool_result`` extension middleware already has
    the real output, so unlike ``run`` there is nothing to execute — no wrapper
    timeout, no shell-dialect concerns. Reads one JSON object on stdin
    (``command``, ``output``, optional ``session_id`` / ``cwd`` / ``exit_code``),
    resolves the mode itself (env → mode.json → off), and prints one JSON
    object: ``{"mode", "wrap"}`` plus ``"text"`` when wrap is true. Telemetry
    rows are appended in both shadow and rewrite, same as ``run``.
    """
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeDecodeError):
        print(json.dumps({"mode": "off", "wrap": False}))
        return 0
    if not isinstance(data, dict):
        print(json.dumps({"mode": "off", "wrap": False}))
        return 0

    command = str(data.get("command") or "")
    output = str(data.get("output") or "")
    mode = context_filter.resolve_mode()
    if mode not in {"shadow", "rewrite"} or not command:
        print(json.dumps({"mode": mode, "wrap": False}))
        return 0
    decision = context_filter.rewrite_decision(command)
    if not decision.should_wrap:
        print(json.dumps({"mode": mode, "wrap": False}))
        return 0

    compressed = context_filter.compress_output(command, output, cache_raw=mode == "rewrite")
    exit_code = data.get("exit_code")
    _log_row(
        mode=mode,
        agent=args.agent or "pi",
        session_id=str(data.get("session_id") or ""),
        cwd=str(data.get("cwd") or ""),
        command=command,
        tool=args.tool,
        compressed=compressed,
        exit_code=exit_code if isinstance(exit_code, int) else None,
    )

    if mode == "shadow":
        print(json.dumps({"mode": mode, "wrap": False}))
        return 0

    text = _header(compressed) + "\n" + compressed.compressed
    print(json.dumps({"mode": mode, "wrap": True, "text": text}))
    return 0


def retrieve(args: argparse.Namespace) -> int:
    path = context_filter.data_dir() / "blobs" / f"{args.key}.txt"
    if not path.exists():
        print(f"raw output not found for key: {args.key}", file=sys.stderr)
        return 1
    sys.stdout.write(path.read_text(encoding="utf-8", errors="replace"))
    return 0


def main() -> int:
    # The wrapped child's own output is decoded as explicit UTF-8 (see
    # _run_command), but re-emitting it via sys.stdout.write still falls back
    # to the locale codec (cp1252 on this machine) unless reconfigured here —
    # any emoji or box-drawing character in that output otherwise crashes the
    # wrapper instead of passing through (fleet-config#426).
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Fleet command-output context filter")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run_p = sub.add_parser("run", help="execute and optionally compress a command")
    run_p.add_argument("--tool", default="PowerShell", choices=["PowerShell", "Bash"])
    run_p.add_argument("--mode", default="rewrite", choices=["rewrite", "shadow"])
    run_p.add_argument("--encoded", required=True)
    run_p.add_argument("--cwd")
    # Telemetry attribution (fleet-config#541); optional so an older hook (or a
    # hand-run wrapper) stays valid.
    run_p.add_argument("--session-id", dest="session_id", default="")
    run_p.add_argument("--agent", default="")
    run_p.set_defaults(func=run_wrapped)

    compress_p = sub.add_parser("compress", help="compress an already-captured output (stdin JSON)")
    compress_p.add_argument("--tool", default="Bash")
    compress_p.add_argument("--agent", default="pi")
    compress_p.set_defaults(func=run_compress)

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
