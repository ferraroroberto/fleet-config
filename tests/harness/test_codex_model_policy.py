"""Focused tests for the opt-in, comment-preserving user-scope config mergers: the Codex model policy, the native Codex footer status line and the session-retention settings."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent.parent


def _load_root_module(name: str):
    """Load a repo-root module by file path, the way the installer runs it."""
    spec = importlib.util.spec_from_file_location(name, ROOT / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


policy = _load_root_module("codex_model_policy")
codex_statusline = _load_root_module("codex_statusline")
session_retention = _load_root_module("session_retention")


class CodexModelPolicyTests(unittest.TestCase):
    def test_merge_preserves_comments_and_unrelated_config(self) -> None:
        original = (
            "# personal default\r\nmodel = \"legacy\" # replace me\r\n"
            "notify = [\"keep\"]\r\n\r\n[agents]\r\n"
            "default_subagent_model = \"legacy\"\r\nmax_concurrent_threads_per_session = 2\r\n"
        )

        updated, changed = policy.merge_policy(original, ROOT / "tmp" / "catalog.json", ROOT / "hooks" / "agents" / "codex")

        parsed = tomllib.loads(updated)
        self.assertEqual(changed, ("model-policy",))
        self.assertEqual(parsed["model"], "gpt-5.6-sol")
        self.assertEqual(parsed["agents"]["default_subagent_model"], "gpt-5.6-terra")
        self.assertEqual(parsed["agents"]["max_concurrent_threads_per_session"], 2)
        self.assertIn("# personal default\r\n", updated)
        self.assertIn("notify = [\"keep\"]\r\n", updated)
        self.assertNotIn("\n", updated.replace("\r\n", ""))

    def test_merge_keeps_only_the_real_comment_when_a_string_holds_a_hash(self) -> None:
        original = 'model = "legacy#1" # real comment\nnotify = ["keep"]\n'

        updated, _ = policy.merge_policy(original, ROOT / "tmp" / "catalog.json", ROOT / "hooks" / "agents" / "codex")

        self.assertIn('model = "gpt-5.6-sol" # real comment\n', updated)
        self.assertNotIn("legacy", updated)

    def test_merge_is_idempotent(self) -> None:
        first, _ = policy.merge_policy("[tui]\nstatus_line = [\"model\"]\n", ROOT / "tmp" / "catalog.json", ROOT / "hooks" / "agents" / "codex")
        second, changed = policy.merge_policy(first, ROOT / "tmp" / "catalog.json", ROOT / "hooks" / "agents" / "codex")

        self.assertEqual(second, first)
        self.assertEqual(changed, ())

    def test_table_ending_on_a_managed_key_still_gets_the_missing_keys(self) -> None:
        # The append of unseen keys used to ride on the table's last line being
        # emitted unchanged, so a table whose last line was itself a managed key
        # lost them (fleet-config#1252).
        catalog = ROOT / "tmp" / "catalog.json"
        easy_description = "Fleet easy tier; model and effort are versioned."
        cases = {
            "root": 'model = "legacy"\n',
            "agents": '[agents]\ndefault_subagent_model = "legacy"\n',
            "role": f'[agents.easy]\ndescription = "{easy_description}"\n',
        }
        for name, original in cases.items():
            with self.subTest(table=name):
                updated, changed = policy.merge_policy(original, catalog, ROOT / "hooks" / "agents" / "codex")

                parsed = tomllib.loads(updated)
                self.assertEqual(changed, ("model-policy",))
                self.assertEqual(parsed["model"], policy.MAIN_MODEL)
                self.assertEqual(parsed["model_reasoning_effort"], policy.MAIN_EFFORT)
                self.assertIn("model_catalog_json", parsed)
                self.assertEqual(parsed["agents"]["default_subagent_model"], "gpt-5.6-terra")
                self.assertEqual(parsed["agents"]["default_subagent_reasoning_effort"], "high")
                for role in policy.ROLE_SPECS:
                    self.assertIn("config_file", parsed["agents"][role])
                    self.assertIn("description", parsed["agents"][role])
                self.assertEqual(policy.merge_policy(updated, catalog, ROOT / "hooks" / "agents" / "codex")[0], updated)

    def test_invalid_toml_is_rejected(self) -> None:
        with self.assertRaises(policy.PolicyError):
            policy.merge_policy("[agents\n", ROOT / "tmp" / "catalog.json", ROOT / "hooks" / "agents" / "codex")

    def test_assets_match_declared_tier_policy(self) -> None:
        policy.validate_assets(ROOT / "hooks" / "agents" / "codex")
        catalog = json.loads((ROOT / "hooks" / "agents" / "codex" / "model_catalog.json").read_text(encoding="utf-8"))
        self.assertEqual(tuple(catalog["slugs"]), policy.CATALOG_MODELS)
        self.assertEqual(tuple(policy.ROLE_SPECS), ("easy", "normal", "hard"))
        for role in policy.ROLE_SPECS:
            layer = tomllib.loads((ROOT / "hooks" / "agents" / "codex" / "agents" / f"{role}.toml").read_text(encoding="utf-8"))
            self.assertNotIn("rollout_budget", layer.get("features", {}))

    def test_configure_does_not_write_when_native_validation_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.toml"
            original = "# leave untouched\nmodel = \"legacy\"\n"
            config.write_text(original, encoding="utf-8")
            with mock.patch.object(policy, "build_catalog", return_value="{\"models\": []}\n"), mock.patch.object(
                policy, "validate_cli", side_effect=policy.PolicyError("unsupported catalog")
            ):
                with self.assertRaises(policy.PolicyError):
                    policy.configure(config, ROOT / "hooks" / "agents" / "codex", "codex", apply=True)
            self.assertEqual(config.read_text(encoding="utf-8"), original)

    def test_configure_applies_through_native_validation(self) -> None:
        # Drives the real validate_cli: only the Codex binary is faked. No test
        # reached it, so a dropped `import tempfile` raised NameError on every
        # --check/--apply for two weeks with the gate green (fleet-config#1242).
        catalog = json.dumps({"models": [{"slug": slug} for slug in policy.CATALOG_MODELS]})
        homes = []

        def fake_codex(command, **kwargs):
            if "env" in kwargs:
                homes.append(Path(kwargs["env"]["CODEX_HOME"]))
                self.assertTrue((homes[-1] / "config.toml").is_file())
            return subprocess.CompletedProcess(command, 0, stdout=catalog, stderr="")

        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.toml"
            config.write_text("# keep me\nmodel = \"legacy\"\nnotify = [\"keep\"]\n", encoding="utf-8")
            with mock.patch.object(policy.subprocess, "run", side_effect=fake_codex):
                state = policy.configure(config, ROOT / "hooks" / "agents" / "codex", "codex", apply=True)

            self.assertEqual(state, "updated")
            self.assertEqual(tomllib.loads(config.read_text(encoding="utf-8"))["model"], policy.MAIN_MODEL)
            self.assertTrue((Path(directory) / "fleet-config" / "model_catalog.json").is_file())
        self.assertEqual(len(homes), 1)
        self.assertFalse(homes[0].exists(), "the disposable CODEX_HOME must be cleaned up")

    def test_installer_forwards_the_opt_in_switch(self) -> None:
        installer = (ROOT / "install.ps1").read_text(encoding="utf-8")

        self.assertIn("[switch]$ConfigureCodexModelPolicy", installer)
        self.assertIn("$psArgs += '-ConfigureCodexModelPolicy'", installer)
        self.assertIn("(Join-Path $RepoRoot 'codex_model_policy.py') --apply", installer)

    def test_hung_codex_is_a_policy_error_not_a_hang(self) -> None:
        def hang(argv, **kwargs):
            self.assertEqual(kwargs.get("timeout"), policy.CODEX_TIMEOUT_S)
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

        with mock.patch.object(policy.subprocess, "run", side_effect=hang):
            with self.assertRaisesRegex(policy.PolicyError, "bundled model catalog within"):
                policy.build_catalog("codex")
            with self.assertRaisesRegex(policy.PolicyError, "did not load the policy within"):
                policy.validate_cli('model_catalog_json = "x"\n', "{}", "codex")


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
            command = [sys.executable, str(ROOT / "codex_statusline.py")]

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
        installer = (ROOT / "install.ps1").read_text(encoding="utf-8")
        scoped_guard = next(
            line for line in installer.splitlines()
            if line.strip().startswith("if ($ConfigureCodexStatusline")
        )

        self.assertIn("[switch]$ConfigureCodexStatusline", installer)
        self.assertIn("$ConfigureCodexStatusline", scoped_guard)
        self.assertIn("$psArgs += '-ConfigureCodexStatusline'", installer)
        self.assertIn("(Join-Path $RepoRoot 'codex_statusline.py') --apply", installer)


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
        scoped_guard = next(
            line for line in installer.splitlines()
            if line.strip().startswith("if ($ConfigureCodexStatusline")
        )
        self.assertIn("[switch]$ConfigureSessionRetention", installer)
        self.assertIn("$psArgs += '-ConfigureSessionRetention'", installer)
        self.assertIn("(Join-Path $RepoRoot 'session_retention.py') --apply", installer)
        self.assertIn("$ConfigureSessionRetention", scoped_guard)


if __name__ == "__main__":
    unittest.main()
