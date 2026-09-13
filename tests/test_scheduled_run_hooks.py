"""Hooks detach no background indexer inside a scheduled run (fleet-config#911).

`weekly-recap-draft` delivered its work and still exited 118 two Sundays running:
the `Stop` hook's delayed `conversation_index` child was alive when the provider
exited, inside the scheduled runner's owned Windows job, which counted it and then
killed it before it indexed anything. The runner is right to call that unfinished,
so the fix is at the source: a hook never starts fire-and-forget work under the
runner's `FLEET_SCHEDULED_RUN=1` marker, and interactive sessions keep spawning.
"""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hooks"))
sys.path.insert(0, str(ROOT / "skills" / "_lib"))
import _lib  # noqa: E402
import conversation_capture as cc  # noqa: E402
import rate_gate  # noqa: E402
import scheduled_runner as runner  # noqa: E402
import session_index  # noqa: E402

SCHEDULED = {_lib.SCHEDULED_RUN_ENV_VAR: "1"}


def _interactive_env() -> dict:
    env = dict(os.environ)
    env.pop(_lib.SCHEDULED_RUN_ENV_VAR, None)
    return env


class ScheduledRunHookSpawnTests(unittest.TestCase):
    def test_marker_spelling_matches_the_runner(self):
        # The tree boundary forbids hooks importing rate_gate, so the two
        # spellings are held together here instead.
        self.assertEqual(_lib.SCHEDULED_RUN_ENV_VAR, rate_gate.UNATTENDED_ENV)

    def test_stop_trigger_spawns_interactively(self):
        with patch.dict(os.environ, _interactive_env(), clear=True), \
                patch.object(cc.subprocess, "Popen") as popen:
            cc._trigger_delayed_index("probe")
        popen.assert_called_once()
        argv = popen.call_args.args[0]
        self.assertIn("--delay-seconds", argv)
        self.assertEqual(argv[argv.index("--project") + 1], "probe")

    def test_stop_trigger_does_not_spawn_in_a_scheduled_run(self):
        with patch.dict(os.environ, SCHEDULED), patch.object(cc.subprocess, "Popen") as popen:
            cc._trigger_delayed_index("probe")
        popen.assert_not_called()

    def _session_start(self, env: dict):
        with tempfile.TemporaryDirectory(prefix="session_index_911_") as folder:
            project = Path(folder) / "project"
            project.mkdir()
            registry = Path(folder) / "projects.toml"
            registry.write_text("[probe]\ncwd_prefix = " + json.dumps(project.as_posix())
                                + "\ncapture = true\n", encoding="utf-8")
            payload = {"hook_event_name": "SessionStart", "cwd": str(project)}
            env = {**env, "CLAUDE_HOOKS_PROJECTS_TOML": str(registry)}
            with patch.dict(os.environ, env, clear=True), \
                    patch.object(sys, "stdin", io.StringIO(json.dumps(payload))), \
                    patch.object(session_index.subprocess, "Popen") as popen:
                self.assertEqual(session_index.main(), 0)
            return popen

    def test_session_start_spawns_interactively(self):
        popen = self._session_start(_interactive_env())
        popen.assert_called_once()
        self.assertEqual(popen.call_args.args[0][-2:], ["--project", "probe"])

    def test_session_start_does_not_spawn_in_a_scheduled_run(self):
        popen = self._session_start({**_interactive_env(), **SCHEDULED})
        popen.assert_not_called()

    def test_stop_trigger_under_the_real_runner_leaves_no_owned_descendant(self):
        """The #911 shape end to end: a clean child whose Stop trigger fires.

        Before the fix the trigger's real 60s-delayed indexer outlives the child
        inside the owned job and the runner reports 118; after it, exit 0.
        """
        fixture = ROOT / "tests" / "fixtures" / "scheduled_claude.jsonl"
        good = fixture.read_text(encoding="utf-8").strip()
        script = (
            "import sys; sys.path.insert(0, " + repr(str(ROOT / "hooks")) + "); "
            "import conversation_capture as cc; "
            "cc._trigger_delayed_index('fleet-config-911-probe-no-such-project'); "
            f"print({good!r}, flush=True)"
        )
        lines: list = []
        formatter = runner.ProgressFormatter(emit=lines.append)
        code = runner.run_process([sys.executable, "-c", script],
                                  formatter=formatter, stall_timeout=0)
        text = "\n".join(lines)
        print(f"stop trigger under runner: exit={code} · {lines[-1] if lines else ''}", flush=True)
        self.assertEqual(code, 0, text)
        self.assertEqual(formatter.owned_orphans, 0, text)


if __name__ == "__main__":
    unittest.main()
