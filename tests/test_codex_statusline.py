"""Focused tests for the opt-in native Codex footer config merger."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("codex_statusline", REPO / "codex_statusline.py")
assert SPEC and SPEC.loader
codex_statusline = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(codex_statusline)


class MergeStatusLineTests(unittest.TestCase):
    def test_fresh_config_gets_the_six_supported_items(self) -> None:
        updated, added = codex_statusline.merge_status_line("")

        self.assertEqual(added, codex_statusline.REQUIRED_STATUS_ITEMS)
        self.assertEqual(tomllib.loads(updated)["tui"]["status_line"], list(added))

    def test_existing_tui_table_receives_setting_without_duplicate_table(self) -> None:
        original = "[tui]\nnotifications = false\n\n[history]\npersistence = 'save-all'\n"

        updated, _ = codex_statusline.merge_status_line(original)

        self.assertEqual(updated.count("[tui]"), 1)
        self.assertIn("notifications = false\n\nstatus_line = [", updated)
        self.assertIn("[history]\npersistence = 'save-all'", updated)
        tomllib.loads(updated)

    def test_custom_order_comments_and_unrelated_settings_are_preserved(self) -> None:
        original = (
            "model = 'custom-model'\n\n[tui]\n"
            "# Hand-picked order stays first.\n"
            "status_line = ['custom-item', \"model\"] # keep this note\n"
            "terminal_title = ['project', 'git-branch']\n"
        )

        updated, added = codex_statusline.merge_status_line(original)

        self.assertEqual(added[0], "context-used")
        self.assertIn("['custom-item', \"model\", \"context-used\"", updated)
        self.assertIn("] # keep this note", updated)
        self.assertIn("terminal_title = ['project', 'git-branch']", updated)
        self.assertEqual(tomllib.loads(updated)["tui"]["status_line"][:2], ["custom-item", "model"])

    def test_commented_multiline_array_is_extended_in_place(self) -> None:
        original = (
            "[tui]\nstatus_line = [\n"
            "    'custom#item', # custom field\n"
            "    \"model\" # final item had no trailing comma\n"
            "]\n"
        )

        updated, _ = codex_statusline.merge_status_line(original)

        self.assertIn("\"model\", # final item had no trailing comma", updated)
        self.assertIn("    \"context-used\", \"current-dir\"", updated)
        self.assertIn("'custom#item', # custom field", updated)
        self.assertEqual(tomllib.loads(updated)["tui"]["status_line"][0], "custom#item")

    def test_multiline_comment_only_and_shared_closing_bracket_stay_valid(self) -> None:
        comment_only = "[tui]\nstatus_line = [\n    # reserved for local choices\n]\n"
        shared_close = "[tui]\nstatus_line = [\n    'custom-item'] # compact close\n"

        updated_comment, _ = codex_statusline.merge_status_line(comment_only)
        updated_shared, _ = codex_statusline.merge_status_line(shared_close)

        self.assertIn("# reserved for local choices", updated_comment)
        self.assertEqual(tomllib.loads(updated_comment)["tui"]["status_line"], list(codex_statusline.REQUIRED_STATUS_ITEMS))
        self.assertIn("'custom-item', \"context-used\"", updated_shared)
        self.assertIn("] # compact close", updated_shared)
        self.assertEqual(tomllib.loads(updated_shared)["tui"]["status_line"][0], "custom-item")

    def test_status_line_text_inside_multiline_string_is_not_an_assignment(self) -> None:
        original = (
            '[tui]\nhelp = """example config:\n'
            "status_line = ['fake']\n"
            '[history]\n"""\nnotifications = false\n'
        )

        updated, added = codex_statusline.merge_status_line(original)

        self.assertEqual(added, codex_statusline.REQUIRED_STATUS_ITEMS)
        self.assertIn("status_line = ['fake']", updated)
        self.assertEqual(updated.count("status_line ="), 2)
        self.assertEqual(tomllib.loads(updated)["tui"]["help"], "example config:\nstatus_line = ['fake']\n[history]\n")
        self.assertEqual(tomllib.loads(updated)["tui"]["status_line"], list(added))

    def test_top_level_dotted_status_line_is_extended_without_new_table(self) -> None:
        original = "tui.status_line = ['custom-item']\ntui.terminal_title = ['project']\n"

        updated, added = codex_statusline.merge_status_line(original)

        self.assertNotIn("[tui]", updated)
        self.assertIn("['custom-item', \"context-used\"", updated)
        self.assertEqual(tomllib.loads(updated)["tui"]["status_line"], ["custom-item", *added])
        self.assertEqual(tomllib.loads(updated)["tui"]["terminal_title"], ["project"])

    def test_merge_is_idempotent_and_preserves_crlf(self) -> None:
        original = "model = 'fixture'\r\n"
        first, _ = codex_statusline.merge_status_line(original)
        second, added = codex_statusline.merge_status_line(first)

        self.assertEqual(second, first)
        self.assertEqual(added, ())
        self.assertNotIn("\n", first.replace("\r\n", ""))

    def test_invalid_toml_is_rejected_before_write(self) -> None:
        with self.assertRaises(codex_statusline.ConfigError):
            codex_statusline.merge_status_line("[tui\nstatus_line = []\n")

    def test_cli_apply_then_check_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.toml"
            config.write_bytes(b"[tui]\r\nterminal_title = ['project']\r\n")
            command = [sys.executable, str(REPO / "codex_statusline.py")]

            applied = subprocess.run(
                [*command, "--apply", "--config", str(config)],
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
            )
            checked = subprocess.run(
                [*command, "--check", "--config", str(config)],
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
            )

            self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
            self.assertIn("status=updated added=6", applied.stdout)
            self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
            self.assertIn("status=unchanged added=0", checked.stdout)
            config_bytes = config.read_bytes()
            self.assertNotIn(b"\n", config_bytes.replace(b"\r\n", b""))
            self.assertEqual(tomllib.loads(config_bytes.decode("utf-8"))["tui"]["terminal_title"], ["project"])

    def test_installer_switch_is_scoped_and_forwarded(self) -> None:
        installer = (REPO / "install.ps1").read_text(encoding="utf-8")

        self.assertIn("[switch]$ConfigureCodexStatusline", installer)
        self.assertIn("$ConfigureCodexStatusline -or $VerifyCodexSandbox", installer)
        self.assertIn("$psArgs += '-ConfigureCodexStatusline'", installer)
        self.assertIn("(Join-Path $RepoRoot 'codex_statusline.py') --apply", installer)


if __name__ == "__main__":
    unittest.main()
