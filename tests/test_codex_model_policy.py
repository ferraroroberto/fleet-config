"""Focused tests for the opt-in, comment-preserving Codex model-policy merger."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("codex_model_policy", ROOT / "codex_model_policy.py")
assert SPEC and SPEC.loader
policy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(policy)


class CodexModelPolicyTests(unittest.TestCase):
    def test_merge_preserves_comments_and_unrelated_config(self) -> None:
        original = (
            "# personal default\r\nmodel = \"legacy\" # replace me\r\n"
            "notify = [\"keep\"]\r\n\r\n[agents]\r\n"
            "default_subagent_model = \"legacy\"\r\nmax_concurrent_threads_per_session = 2\r\n"
        )

        updated, changed = policy.merge_policy(original, ROOT / "tmp" / "catalog.json", ROOT / "codex")

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

        updated, _ = policy.merge_policy(original, ROOT / "tmp" / "catalog.json", ROOT / "codex")

        self.assertIn('model = "gpt-5.6-sol" # real comment\n', updated)
        self.assertNotIn("legacy", updated)

    def test_merge_is_idempotent(self) -> None:
        first, _ = policy.merge_policy("[tui]\nstatus_line = [\"model\"]\n", ROOT / "tmp" / "catalog.json", ROOT / "codex")
        second, changed = policy.merge_policy(first, ROOT / "tmp" / "catalog.json", ROOT / "codex")

        self.assertEqual(second, first)
        self.assertEqual(changed, ())

    def test_invalid_toml_is_rejected(self) -> None:
        with self.assertRaises(policy.PolicyError):
            policy.merge_policy("[agents\n", ROOT / "tmp" / "catalog.json", ROOT / "codex")

    def test_assets_match_declared_tier_policy(self) -> None:
        policy.validate_assets(ROOT / "codex")
        catalog = json.loads((ROOT / "codex" / "model_catalog.json").read_text(encoding="utf-8"))
        self.assertEqual(tuple(catalog["slugs"]), policy.CATALOG_MODELS)
        self.assertEqual(tuple(policy.ROLE_SPECS), ("easy", "normal", "hard"))
        for role in policy.ROLE_SPECS:
            layer = tomllib.loads((ROOT / "codex" / "agents" / f"{role}.toml").read_text(encoding="utf-8"))
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
                    policy.configure(config, ROOT / "codex", "codex", apply=True)
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
                state = policy.configure(config, ROOT / "codex", "codex", apply=True)

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


if __name__ == "__main__":
    unittest.main()
