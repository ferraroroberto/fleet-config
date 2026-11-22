"""The Codex refusal probe reports a skipped, untrusted workspace as unknown, never pass or fail."""

from __future__ import annotations

import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import probe_codex_refusal as probe  # noqa: E402

_TRUST_STDERR = (
    "warning: Untrusted project-local configuration is disabled. Run `codex exec --trust` to allow it.\n"
)


@unittest.skipUnless(os.name == "nt", "the probe is Windows-only")
class UntrustedWorkspaceTests(unittest.TestCase):
    def _run_probe(self, fake_codex: str) -> tuple[int, str]:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "ws"
            # A stand-in for `codex` that prints the trust warning and observes nothing.
            stub = Path(tmp) / "codex.cmd"
            stub.write_text(fake_codex, encoding="utf-8")
            argv = ["probe", "--workspace", str(workspace), "--model", "m"]
            out = io.StringIO()
            with (mock.patch.object(sys, "argv", argv),
                  mock.patch.object(probe.shutil, "which", return_value=str(stub)),
                  mock.patch.object(probe, "run_git", return_value=mock.Mock(returncode=0)),
                  contextlib.redirect_stdout(out)):
                code = probe.main()
            return code, out.getvalue()

    def test_trust_warning_without_observation_is_unknown_naming_trust(self) -> None:
        code, text = self._run_probe(
            "@echo off\r\nif \"%1\"==\"--version\" (echo codex-cli 0.0.0& exit /b 0)\r\n"
            f"echo {_TRUST_STDERR.strip()} 1>&2\r\nexit /b 0\r\n")
        self.assertEqual(code, 1)
        self.assertIn("CONFORMANCE=unknown", text)
        self.assertIn("project trust missing", text)
        self.assertNotIn('"conformance"', text)

    def test_cli_failure_without_trust_warning_keeps_generic_unknown(self) -> None:
        code, text = self._run_probe(
            "@echo off\r\nif \"%1\"==\"--version\" (echo codex-cli 0.0.0& exit /b 0)\r\nexit /b 3\r\n")
        self.assertEqual(code, 1)
        self.assertIn("CONFORMANCE=unknown reason=CLI failed", text)


if __name__ == "__main__":
    unittest.main()
