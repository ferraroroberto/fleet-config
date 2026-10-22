"""Context-filter checks on the `compress` subcommand, the Pi port's entry point."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

from acceptance.shared import HOOKS, PYTHON, REPO, _Checker


def compress_modes_and_skips(check: _Checker) -> None:
    # ---- compress subcommand: the Pi port's entry point (#545) ----
    # Pi's tool_result middleware already holds the output, so compress reads
    # stdin JSON and never executes anything. Mode comes from the same
    # resolve_mode(); rows log agent "pi"; wrap only in rewrite.
    big_output = "\n".join(f"PASS test_case_{i:03d} ok" for i in range(400))
    compress_payload = json.dumps(
        {"command": "git status --short", "output": big_output, "session_id": "pi-sess-1", "cwd": str(REPO), "exit_code": 0}
    )
    for mode, expect_wrap in (("shadow", False), ("rewrite", True)):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "mode.json").write_text(json.dumps({"mode": mode}), encoding="utf-8")
            res = subprocess.run(
                [PYTHON, str(HOOKS / "context_filter_cli.py"), "compress", "--agent", "pi"],
                input=compress_payload,
                capture_output=True,
                text=True,
                env={**os.environ, "FLEET_CONTEXT_FILTER_MODE": "", "FLEET_CONTEXT_FILTER_DIR": tmp},
                timeout=30,
            )
            reply = json.loads(res.stdout) if res.returncode == 0 and res.stdout.strip() else {}
            log_path = Path(tmp) / "shadow.jsonl"
            rows = [json.loads(l) for l in log_path.read_text(encoding="utf-8").splitlines() if l.strip()] if log_path.exists() else []
            row = rows[0] if rows else {}
            check(
                f"context_filter_cli: compress {mode} -> wrap={expect_wrap}, row agent=pi (fleet-config#545)",
                res.returncode == 0
                and reply.get("wrap") is expect_wrap
                and (not expect_wrap or "[fleet-context-filter:" in reply.get("text", ""))
                and len(rows) == 1
                and row.get("agent") == "pi"
                and row.get("mode") == mode
                and row.get("session_id") == "pi-sess-1",
                f"rc={res.returncode} reply={reply} rows={len(rows)} | {res.stderr.strip()}",
            )

    # skip rule still applies post-hoc: a streaming command's output is left alone
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "mode.json").write_text(json.dumps({"mode": "rewrite"}), encoding="utf-8")
        res = subprocess.run(
            [PYTHON, str(HOOKS / "context_filter_cli.py"), "compress"],
            input=json.dumps({"command": "npm run dev -- --watch", "output": big_output}),
            capture_output=True,
            text=True,
            env={**os.environ, "FLEET_CONTEXT_FILTER_MODE": "", "FLEET_CONTEXT_FILTER_DIR": tmp},
            timeout=30,
        )
        reply = json.loads(res.stdout) if res.returncode == 0 and res.stdout.strip() else {}
        check(
            "context_filter_cli: compress honors rewrite_decision skips (fleet-config#545)",
            res.returncode == 0 and reply.get("wrap") is False,
            f"rc={res.returncode} reply={reply} | {res.stderr.strip()}",
        )


def compress_malformed_stdin_fails_open(check: _Checker) -> None:
    # malformed stdin fails open with a well-formed no-wrap reply
    res = subprocess.run(
        [PYTHON, str(HOOKS / "context_filter_cli.py"), "compress"],
        input="{not json",
        capture_output=True,
        text=True,
        timeout=30,
    )
    reply = json.loads(res.stdout) if res.returncode == 0 and res.stdout.strip() else {}
    check(
        "context_filter_cli: compress malformed stdin fails open (fleet-config#545)",
        res.returncode == 0 and reply.get("wrap") is False,
        f"rc={res.returncode} reply={reply} | {res.stderr.strip()}",
    )


def compress_keeps_non_ascii_output(check: _Checker) -> None:
    # Non-ASCII output survives the pipe (fleet-config#1440): Pi writes UTF-8 JSON
    # and spawns this entry point without PYTHONUTF8, so a text-layer stdin read
    # on Windows decoded it as cp1252 and handed back `cafÃ© â€”`. Raw UTF-8 bytes
    # in, the interpreter's UTF-8 mode off, and `ensure_ascii=False` so the
    # payload really carries the multi-byte characters.
    utf8_output = "\n".join(f"PASS café — test_case_{i:03d} ok" for i in range(400))
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "mode.json").write_text(json.dumps({"mode": "rewrite"}), encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if k not in {"PYTHONUTF8", "PYTHONIOENCODING"}}
        env.update({"FLEET_CONTEXT_FILTER_MODE": "", "FLEET_CONTEXT_FILTER_DIR": tmp})
        res = subprocess.run(
            [PYTHON, str(HOOKS / "context_filter_cli.py"), "compress", "--agent", "pi"],
            input=json.dumps(
                {"command": "git status --short", "output": utf8_output}, ensure_ascii=False
            ).encode("utf-8"),
            capture_output=True,
            env=env,
            timeout=30,
        )
        reply = json.loads(res.stdout.decode("utf-8")) if res.returncode == 0 and res.stdout.strip() else {}
        text = reply.get("text", "")
        check(
            "context_filter_cli: compress keeps non-ASCII output intact (fleet-config#1440)",
            reply.get("wrap") is True and "café —" in text and "cafÃ©" not in text,
            f"rc={res.returncode} wrap={reply.get('wrap')} text[:120]={text[:120]!r} | {res.stderr.decode('utf-8', 'replace').strip()}",
        )

