"""A run whose only post-exit leftovers are read-only search tools is reaped with a warning, not failed with exit 118.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/claude_progress/test_reap_read_only_search.py`  (also invoked by tests/run_acceptance.py)
"""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "skills" / "_lib"))
import scheduled_runner as runner
from scheduled import process_scope


class ReapReadOnlySearchTests(unittest.TestCase):
    def setUp(self):
        fixture = ROOT / "tests" / "fixtures" / "scheduled_claude.jsonl"
        self.events = [json.loads(line) for line in fixture.read_text(encoding="utf-8").splitlines()]

    def test_read_only_search_names_classification(self):
        names = runner._read_only_search_names
        self.assertEqual(names([(1, "grep.exe", "grep -r x"), (2, "HEAD.EXE", None),
                                (3, "rg", None), (4, "grep.exe", None)]), ["grep", "head", "rg"])
        self.assertIsNone(names(None), "unnameable members never qualify")
        self.assertIsNone(names([]))
        self.assertIsNone(names([(1, "grep.exe", None), (2, None, None)]), "unreadable image")
        self.assertIsNone(names([(1, "grep.exe", None), (2, "git.exe", "git commit")]))
        self.assertIsNone(names([(1, "python.exe", None)]))
        self.assertEqual(names([(1, "grep.exe", None), (2, "conhost.exe", None)]), ["grep"])
        self.assertIsNone(names([(1, "conhost.exe", None)]), "a console host alone proves nothing")

    def _run_with_orphan(self, reap_names, terminate=None):
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        good = "\n".join(json.dumps(e) for e in self.events)
        script = (f"import subprocess,sys;"
                  f"subprocess.Popen([sys.executable,'-c','import time;time.sleep(4)'],"
                  f"stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags={flags});"
                  f"print({good!r},flush=True)")
        lines = []
        formatter = runner.ProgressFormatter(emit=lines.append)
        with patch.object(runner, "_read_only_search_names", return_value=reap_names):
            if terminate is None:
                code = runner.run_process([sys.executable, "-c", script],
                                          formatter=formatter, stall_timeout=0)
            else:
                with patch.object(process_scope.ProcessScope, "terminate", return_value=terminate):
                    code = runner.run_process([sys.executable, "-c", script],
                                              formatter=formatter, stall_timeout=0)
        return code, formatter, lines

    def test_read_only_search_leftovers_exit_by_the_delivery_verdict(self):
        code, formatter, lines = self._run_with_orphan(["grep", "head"])
        print(f"reaped read-only orphan: exit={code} · {lines[-3:]}", flush=True)
        self.assertEqual(code, 0, "\n".join(lines))
        self.assertEqual(formatter.owned_orphans, 0)
        warning = [line for line in lines if "reaped" in line]
        self.assertEqual(len(warning), 1, lines)
        self.assertIn("stray read-only search descendant", warning[0])
        self.assertIn("grep, head", warning[0])
        self.assertIn("✅ completed", lines[-1])
        self.assertNotIn("not confirmed", "\n".join(lines))

    def test_other_leftover_keeps_exit_118(self):
        code, formatter, lines = self._run_with_orphan(None)
        self.assertEqual(code, runner.INCOMPLETE_WORK_EXIT_CODE, "\n".join(lines))
        self.assertGreater(formatter.owned_orphans, 0)
        self.assertFalse(any("reaped" in line for line in lines), lines)
        self.assertIn("❓ not confirmed", lines[-1])

    def test_unconfirmed_reap_keeps_the_not_confirmed_verdict(self):
        code, _formatter, lines = self._run_with_orphan(["grep"], terminate=False)
        text = "\n".join(lines)
        self.assertNotEqual(code, 0, text)
        self.assertFalse(any("reaped" in line for line in lines), text)
        self.assertIn("reap-unconfirmed", text)


if __name__ == "__main__":
    unittest.main()
