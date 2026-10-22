"""Context-filter checks on the telemetry log: row schema, rotation, secret redaction and blob GC."""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from acceptance.shared import HOOKS, PYTHON, _Checker


def row_schema_is_pinned(check: _Checker) -> None:
    # ---- telemetry row schema is pinned, and rewrite mode logs too (#541) ----
    # The app-launcher stats panel consumes these rows; a silent schema drift or
    # a rewrite flip that stops logging would blank the panel with no error.
    expected_keys = {
        "ts", "mode", "agent", "session_id", "cwd", "command", "tool",
        "raw_tokens", "compressed_tokens", "reduction_pct", "duration_ms", "exit_code",
    }
    log_command = 'Write-Output "schema probe"'
    encoded = base64.b64encode(log_command.encode("utf-8")).decode("ascii")
    for mode in ("shadow", "rewrite"):
        with tempfile.TemporaryDirectory() as tmp:
            res = subprocess.run(
                [
                    PYTHON,
                    str(HOOKS / "context_filter_cli.py"),
                    "run",
                    "--tool", "PowerShell",
                    "--mode", mode,
                    "--encoded", encoded,
                    "--session-id", "test-session-541",
                    "--agent", "codex",
                ],
                capture_output=True,
                text=True,
                env={**os.environ, "FLEET_CONTEXT_FILTER_DIR": tmp},
                timeout=30,
            )
            log_path = Path(tmp) / "shadow.jsonl"
            rows = []
            if log_path.exists():
                rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            row = rows[0] if rows else {}
            check(
                f"context_filter_cli: {mode} mode logs a row with the pinned schema (fleet-config#541)",
                res.returncode == 0
                and len(rows) == 1
                and set(row.keys()) == expected_keys
                and row["mode"] == mode
                and row["agent"] == "codex"
                and row["session_id"] == "test-session-541"
                and row["ts"].endswith("+00:00"),
                f"rc={res.returncode} rows={len(rows)} keys={sorted(row.keys())} | {res.stderr.strip()}",
            )


def shadow_log_rotates_at_cap(check: _Checker) -> None:
    log_command = 'Write-Output "schema probe"'
    encoded = base64.b64encode(log_command.encode("utf-8")).decode("ascii")

    # ---- shadow.jsonl rotates at the size cap, keeping one generation (#549) ----
    with tempfile.TemporaryDirectory() as tmp:
        sys.path.insert(0, str(HOOKS))
        import context_filter as _cf  # noqa: E402

        log = Path(tmp) / "shadow.jsonl"
        log.write_text("x" * (_cf.SHADOW_LOG_MAX_BYTES + 1), encoding="utf-8")
        prior_gen = Path(tmp) / "shadow.jsonl.1"
        prior_gen.write_text("older generation\n", encoding="utf-8")
        res = subprocess.run(
            [
                PYTHON,
                str(HOOKS / "context_filter_cli.py"),
                "run",
                "--tool", "PowerShell",
                "--mode", "shadow",
                "--encoded", encoded,
            ],
            capture_output=True,
            text=True,
            env={**os.environ, "FLEET_CONTEXT_FILTER_DIR": tmp},
            timeout=30,
        )
        rotated = prior_gen.exists() and prior_gen.stat().st_size > len("older generation\n")
        fresh_rows = [l for l in log.read_text(encoding="utf-8").splitlines() if l.strip()] if log.exists() else []
        check(
            "context_filter: oversized shadow.jsonl rotates to .1 and the row lands fresh (fleet-config#549)",
            res.returncode == 0 and rotated and len(fresh_rows) == 1 and fresh_rows[0].startswith("{"),
            f"rc={res.returncode} rotated={rotated} fresh_rows={len(fresh_rows)} | {res.stderr.strip()}",
        )


def command_field_is_redacted(check: _Checker) -> None:
    # ---- the `command` field is redacted like the output is (#681) ----
    # The row's output-derived fields already passed through SECRET_RE; the
    # command did not, so a secret-bearing command landed half-filtered in the
    # log — the redaction the writer applies asymmetric between two fields of
    # the same record.
    with tempfile.TemporaryDirectory() as tmp:
        sys.path.insert(0, str(HOOKS))
        import context_filter as _cf2  # noqa: E402

        secret_cmd = "gh auth login --with-token ghp_0123456789abcdefghijklmnopqrstuvwxyzA"
        record = {"ts": "now", "mode": "shadow", "command": secret_cmd, "tool": "Bash"}
        prior_dir = os.environ.get("FLEET_CONTEXT_FILTER_DIR")
        os.environ["FLEET_CONTEXT_FILTER_DIR"] = tmp
        try:
            _cf2.append_shadow_log(record)
            written = (Path(tmp) / "shadow.jsonl").read_text(encoding="utf-8")
        finally:
            if prior_dir is None:
                os.environ.pop("FLEET_CONTEXT_FILTER_DIR", None)
            else:
                os.environ["FLEET_CONTEXT_FILTER_DIR"] = prior_dir
        check(
            "context_filter: append_shadow_log redacts the `command` field (fleet-config#681)",
            "ghp_0123456789abcdefghijklmnopqrstuvwxyzA" not in written
            and "[REDACTED_SECRET]" in written,
            f"written={written.strip()[:200]}",
        )
        check(
            "context_filter: append_shadow_log does not mutate the caller's record",
            record["command"] == secret_cmd,
        )
        check(
            "context_filter: a secret-free command is written through unchanged",
            "git status --porcelain" in json.dumps({"command": "git status --porcelain"})
            and _cf2.redact_secret_markers("git status --porcelain") == "git status --porcelain",
        )


def blob_gc_prunes_stale_entries(check: _Checker) -> None:
    # ---- blob GC: rewrite prunes cache entries older than the TTL (#541) ----
    with tempfile.TemporaryDirectory() as tmp:
        blobs = Path(tmp) / "blobs"
        blobs.mkdir(parents=True)
        stale = blobs / "deadbeefdeadbeef.txt"
        stale.write_text("old raw output", encoding="utf-8")
        old = time.time() - (8 * 24 * 3600)
        os.utime(stale, (old, old))
        # An output with lines withheld: only that caches a blob, and caching
        # is what runs the GC (fleet-config#1268).
        withholding = "Write-Output ((0..199) -replace '^(.*)$', 'row $1 aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa')"
        res = subprocess.run(
            [
                PYTHON,
                str(HOOKS / "context_filter_cli.py"),
                "run",
                "--tool", "PowerShell",
                "--mode", "rewrite",
                "--encoded", base64.b64encode(withholding.encode("utf-8")).decode("ascii"),
            ],
            capture_output=True,
            text=True,
            env={**os.environ, "FLEET_CONTEXT_FILTER_DIR": tmp},
            timeout=30,
        )
        check(
            "context_filter_cli: blob older than 7 days is pruned on rewrite (fleet-config#541)",
            res.returncode == 0 and not stale.exists(),
            f"rc={res.returncode} stale_exists={stale.exists()} | {res.stderr.strip()}",
        )

