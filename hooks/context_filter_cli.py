"""CLI entrypoint for the fleet context filter.

Usage:
    py hooks/context_filter_cli.py eval --fixtures tests/fixtures/context_filter
    py hooks/context_filter_cli.py run --tool PowerShell --mode rewrite --encoded <b64>
    py hooks/context_filter_cli.py retrieve <key>
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


def _decode_command(encoded: str) -> str:
    return base64.b64decode(encoded.encode("ascii")).decode("utf-8", "replace")


def _powershell_exe() -> str:
    win_ps = "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
    if Path(win_ps).exists():
        return win_ps
    return shutil.which("powershell") or shutil.which("pwsh") or "powershell"


def _run_command(tool: str, command: str, cwd: str | None) -> subprocess.CompletedProcess[str]:
    if tool.lower() == "powershell":
        args = [_powershell_exe(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", command]
    elif tool.lower() == "bash":
        bash = shutil.which("bash")
        args = [bash, "-lc", command] if bash else [command]
    else:
        args = [command]
    return subprocess.run(
        args,
        cwd=cwd or None,
        capture_output=True,
        text=True,
        shell=(len(args) == 1),
        timeout=int(os.environ.get("FLEET_CONTEXT_FILTER_TIMEOUT", "600")),
    )


def run_wrapped(args: argparse.Namespace) -> int:
    command = _decode_command(args.encoded)
    try:
        result = _run_command(args.tool, command, args.cwd)
    except subprocess.TimeoutExpired as exc:
        raw = (exc.stdout or "") + (exc.stderr or "")
        if raw:
            sys.stdout.write(raw)
        print(f"fleet-context-filter: command timed out after wrapper timeout: {command}", file=sys.stderr)
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
