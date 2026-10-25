"""Focused tests for Grok Build's status line and its opt-in config merge."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("grok_statusline", REPO / "grok_statusline.py")
assert SPEC and SPEC.loader
grok_statusline = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(grok_statusline)

PAYLOAD = {
    "workspace": {"current_dir": "E:/work/demo/src", "repo_root": "E:/work/demo", "branch": "main"},
    "model": {"display_name": "Grok 4.5"},
    "effort": {"level": "high"},
    "context_window": {"used_percentage": 12, "auto_compact_threshold_percent": 85},
    "cost": {"total_cost_usd": 0.4213},
}


def _snapshot(state: str = "available", five: object = 5, week: object = 71) -> dict:
    def window(wid: str, pct: object) -> dict:
        return {"id": wid, "state": state, "used_percentage": pct}

    return {"sources": [{
        "producer": "claude-statusline", "state": state,
        "observations": [{"state": state, "windows": [window("five_hour", five), window("seven_day", week)]}],
    }]}


def _plain(text: str) -> str:
    return text.replace("\x1b[0m", "").replace("\x1b[31m", "").replace("\x1b[32m", "").replace("\x1b[33m", "")


class RenderTests(unittest.TestCase):
    def test_full_row_labels_the_limits_as_claudes(self) -> None:
        row = _plain(grok_statusline.render(PAYLOAD, _snapshot()))
        self.assertEqual(row, "12%c $0.42 | Claude 5%s 71%w | Grok 4.5 high | demo (main)")

    def test_stale_or_absent_claude_reading_shows_unknown_never_a_number(self) -> None:
        for snapshot in (_snapshot(state="stale"), {"sources": []}, _snapshot(five=None)):
            self.assertIn("Claude ?%s", _plain(grok_statusline.render(PAYLOAD, snapshot)))

    def test_no_grok_limit_is_ever_rendered(self) -> None:
        # Even a payload carrying limit-shaped fields shows only Claude's.
        payload = dict(PAYLOAD, rate_limits={"five_hour": {"used_percentage": 3}})
        row = _plain(grok_statusline.render(payload, _snapshot()))
        self.assertEqual(row.count("%s"), 1)
        self.assertNotIn("3%s", row)

    def test_absent_fields_are_omitted_not_zeroed(self) -> None:
        row = _plain(grok_statusline.render({"model": {"display_name": "Grok 4.5"}}, _snapshot()))
        self.assertEqual(row, "Claude 5%s 71%w | Grok 4.5")

    def test_worktree_name_wins_over_the_repo_folder(self) -> None:
        payload = dict(PAYLOAD, worktree={"name": "demo-wt-3", "path": "E:/work/demo-wt-3"})
        self.assertTrue(_plain(grok_statusline.render(payload, _snapshot())).endswith("demo-wt-3 (main)"))

    def test_context_color_follows_the_compaction_threshold(self) -> None:
        def ctx(pct: int) -> str:
            payload = {"context_window": {"used_percentage": pct, "auto_compact_threshold_percent": 85}}
            return grok_statusline.render(payload, {"sources": []})

        self.assertIn("\x1b[32m60%c", ctx(60))
        self.assertIn("\x1b[33m65%c", ctx(65))
        self.assertIn("\x1b[31m70%c", ctx(70))


class MergeTests(unittest.TestCase):
    COMMAND = "C:/py/python.exe E:/fleet/grok_statusline.py"

    def test_appends_one_table_and_keeps_every_other_byte(self) -> None:
        original = "# mine\n[ui]\nyolo = true\n\n[cli]\nauto_update = false\n"
        updated, state = grok_statusline.merge_status_line(original, self.COMMAND)
        self.assertEqual(state, "added")
        self.assertTrue(updated.startswith(original))
        data = tomllib.loads(updated)
        self.assertEqual(data["ui"]["status_line"], {"type": "command", "command": self.COMMAND})
        self.assertTrue(data["ui"]["yolo"])

    def test_rerun_is_unchanged_and_crlf_survives(self) -> None:
        once, _ = grok_statusline.merge_status_line("[ui]\r\nyolo = true\r\n", self.COMMAND)
        twice, state = grok_statusline.merge_status_line(once, self.COMMAND)
        self.assertEqual((twice, state), (once, "unchanged"))
        self.assertNotIn("\n", once.replace("\r\n", ""))

    def test_a_users_own_row_is_left_alone(self) -> None:
        for original in ('[ui.status_line]\ntype = "builtin"\n', '[ui.status_line]\ntype = "disabled"\n'):
            self.assertEqual(grok_statusline.merge_status_line(original, self.COMMAND), (original, "kept-user-row"))

    def test_invalid_toml_is_refused(self) -> None:
        with self.assertRaises(grok_statusline.ConfigError):
            grok_statusline.merge_status_line("[ui\n", self.COMMAND)

    def test_cli_check_then_apply_then_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.toml"
            config.write_text("[ui]\nyolo = true\n", encoding="utf-8")

            def run(flag: str) -> str:
                return subprocess.run(
                    [sys.executable, str(REPO / "grok_statusline.py"), flag, "--config", str(config)],
                    capture_output=True, text=True, check=True,
                ).stdout

            self.assertIn("status=update-needed", run("--check"))
            self.assertNotIn("status_line", config.read_text(encoding="utf-8"))
            self.assertIn("status=added", run("--apply"))
            self.assertIn("status=unchanged", run("--check"))

    def test_render_cli_reads_stdin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, CLAUDE_HOOKS_STATE_DIR=tmp, PYTHONUTF8="1")
            out = subprocess.run(
                [sys.executable, str(REPO / "grok_statusline.py")], input=json.dumps(PAYLOAD).encode(),
                capture_output=True, env=env, check=True,
            ).stdout.decode("utf-8")
        self.assertTrue(out.endswith("demo (main)\n"))
        self.assertIn("Claude ?%s ?%w", _plain(out))

    def test_installer_switch_is_scoped_and_forwarded(self) -> None:
        installer = (REPO / "install.ps1").read_text(encoding="utf-8")
        self.assertIn("[switch]$ConfigureGrokStatusline", installer)
        self.assertIn("$ConfigureCodexStatusline -or $ConfigureGrokStatusline -or", installer)
        self.assertIn("$psArgs += '-ConfigureGrokStatusline'", installer)
        self.assertIn("(Join-Path $RepoRoot 'grok_statusline.py') --apply", installer)


if __name__ == "__main__":
    unittest.main()
