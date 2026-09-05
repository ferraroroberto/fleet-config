"""Prove failed/unknown native evidence cannot be turned into passing evidence."""

from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills/_lib"))
import discovery_probe as probe


class DiscoveryProbeTests(unittest.TestCase):
    def test_catalog_checks_actual_source_and_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            source.write_text("fixture")
            rows = {scope: [{"name": name, "path": str(source), "source": str(source)}
                            for name in names] for scope, names in probe.EXPECTED.items()}
            self.assertEqual(probe._catalog_result("codex", "installed", "test", rows, [])["status"], "verified")
            rows["root"][0]["path"] = str(source.parent / "wrong-source")
            self.assertEqual(probe._catalog_result("codex", "installed", "test", rows, [])["status"], "failed")
            rows["root"][0]["path"] = str(source)
            rows["root"].append(rows["root"][0])
            self.assertEqual(probe._catalog_result("codex", "installed", "test", rows, [])["status"], "failed")

    def test_changing_grok_scope_is_unknown(self):
        reports = [{"client": "grok", "status": "verified", "scopes": {"root": ["one"]}},
                   {"client": "grok", "status": "unsupported", "scopes": {"root": []}}]
        with patch.object(probe, "_grok_inventory", side_effect=reports):
            self.assertEqual(probe._grok({})["status"], "unknown")

    def test_model_evidence_requires_successful_target_read_and_exact_answer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "package"
            package.mkdir()
            for path in (root / "CLAUDE.md", package / "CLAUDE.md"):
                path.write_text("synthetic target")
            expected = "CLAUDE_ROOT_EOF_748,CLAUDE_NESTED_EOF_748"
            command = {"type": "command_execution", "exit_code": 0,
                       "command": f"powershell.exe Get-Content -LiteralPath '{root.as_posix()}/CLAUDE.md','{package.as_posix()}/CLAUDE.md'",
                       "aggregated_output": expected.replace(",", "\n")}
            records = [{"type": "item.completed", "item": command},
                       {"type": "item.completed", "item": {"type": "agent_message", "text": expected}}]

            def run():
                result = subprocess.CompletedProcess([], 0, "\n".join(json.dumps(r) for r in records), "")
                with patch.object(probe, "_run", return_value=result), patch.object(probe, "_version", return_value="fixture"), patch.object(probe.shutil, "which", return_value="fixture"):
                    return probe._codex_model_instructions({"root": root, "package": package}, "fixture")["status"]

            self.assertEqual(run(), "verified")
            command["command"] = command["command"].replace("/", "\\\\")
            self.assertEqual(run(), "verified")
            command["exit_code"] = 1
            self.assertEqual(run(), "unknown")
            command["exit_code"] = 0
            command["aggregated_output"] = "no marker was read"
            self.assertEqual(run(), "failed")

    def test_full_proof_and_missing_required_client_are_nonpassing(self):
        result = {"skills": [{"client": "claude", "status": "verified"},
                             {"client": "codex", "status": "verified"}],
                  "instructions": [{"client": "claude", "status": "verified"},
                                   {"client": "codex", "status": "unknown"}]}
        with patch.object(probe, "probe", return_value=result), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(probe.main([]), 2)
            self.assertNotEqual(probe.main(["--run", "--instruction-proof"]), 0)
            result["skills"][0]["status"] = "missing"
            self.assertEqual(probe.main(["--run"]), 2)


if __name__ == "__main__":
    unittest.main()
