from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("session_retention", ROOT / "session_retention.py")
assert SPEC and SPEC.loader
session_retention = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(session_retention)


class SessionRetentionTests(unittest.TestCase):
    def test_claude_merge_preserves_unrelated_values_and_removes_opt_out(self) -> None:
        original = {
            "permissions": {"allow": ["Read"]},
            "env": {"SECRET": "kept", "CLAUDE_CODE_SKIP_PROMPT_HISTORY": "1"},
            "cleanupPeriodDays": 30,
        }
        updated, changed = session_retention.merge_claude_settings(original)
        self.assertEqual(updated["cleanupPeriodDays"], 730)
        self.assertEqual(updated["env"], {"SECRET": "kept"})
        self.assertEqual(original["cleanupPeriodDays"], 30)
        self.assertEqual(set(changed), {
            "cleanupPeriodDays", "env.CLAUDE_CODE_SKIP_PROMPT_HISTORY",
        })

    def test_codex_merge_preserves_comments_and_removes_cap(self) -> None:
        original = (
            "# keep me\r\n"
            "model = \"gpt-example\"\r\n"
            "\r\n[history] # local sessions\r\n"
            "persistence = \"none\" # retain comment\r\n"
            "max_bytes = 1024\r\n"
            "\r\n[tui]\r\n"
            "terminal_title = true\r\n"
        )
        updated, changed = session_retention.merge_codex_history(original)
        parsed = tomllib.loads(updated)
        self.assertEqual(parsed["history"], {"persistence": "save-all"})
        self.assertIn("# keep me\r\n", updated)
        self.assertIn("# retain comment\r\n", updated)
        self.assertIn('terminal_title = true\r\n', updated)
        self.assertEqual(set(changed), {"history.persistence", "history.max_bytes"})

    def test_codex_merge_supports_dotted_key_and_is_idempotent(self) -> None:
        first, _ = session_retention.merge_codex_history(
            'history.persistence = "none"\nother = 42\n'
        )
        second, changed = session_retention.merge_codex_history(first)
        self.assertEqual(tomllib.loads(first)["history"]["persistence"], "save-all")
        self.assertEqual(second, first)
        self.assertEqual(changed, ())

    def test_codex_merge_adds_history_table(self) -> None:
        updated, _ = session_retention.merge_codex_history('model = "example"\n')
        self.assertEqual(tomllib.loads(updated)["history"]["persistence"], "save-all")

    def test_apply_is_atomic_and_cli_check_reports_clean(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            claude = root / "settings.json"
            codex = root / "config.toml"
            claude.write_text(json.dumps({"permissions": {"allow": ["Read"]}}), encoding="utf-8")
            codex.write_text('[tui]\nterminal_title = true\n', encoding="utf-8")
            states = session_retention.configure(claude, codex, apply=True)
            self.assertEqual(states, ("updated", "updated"))
            self.assertEqual(json.loads(claude.read_text(encoding="utf-8"))["cleanupPeriodDays"], 730)
            self.assertEqual(tomllib.loads(codex.read_text(encoding="utf-8"))["history"]["persistence"], "save-all")
            proc = subprocess.run(
                [sys.executable, str(ROOT / "session_retention.py"), "--check",
                 "--claude-settings", str(claude), "--codex-config", str(codex)],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("claude=unchanged codex=unchanged days=730", proc.stdout)

    def test_installer_switch_is_scoped_forwarded_and_invokes_helper(self) -> None:
        installer = (ROOT / "install.ps1").read_text(encoding="utf-8")
        self.assertIn("[switch]$ConfigureSessionRetention", installer)
        self.assertIn("$psArgs += '-ConfigureSessionRetention'", installer)
        self.assertIn("(Join-Path $RepoRoot 'session_retention.py') --apply", installer)
        self.assertIn("$ConfigureCodexStatusline -or $ConfigureSessionRetention", installer)


if __name__ == "__main__":
    unittest.main()
