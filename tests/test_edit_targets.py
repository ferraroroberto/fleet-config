"""Shared edit target contract and actual syntax-hook regression (#744)."""
from __future__ import annotations

import io
import json
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks"))
import _lib
import py_syntax_check


class EditTargetsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.payload = json.loads((Path(__file__).parent / "fixtures" / "codex_patch_post.json").read_text(encoding="utf-8"))
        self.payload["cwd"] = str(self.root)

    def run_hook(self, payload: dict, run=None) -> tuple[int, str, str, list[str]]:
        out, err = io.StringIO(), io.StringIO()
        compiled = []
        original = subprocess.run

        def observe(argv, **kwargs):
            if "py_compile" in argv:
                compiled.append(Path(argv[-1]).name)
                if run is not None:
                    return run(argv, **kwargs)
            return original(argv, **kwargs)

        with patch.object(sys, "stdin", io.StringIO(json.dumps(payload))), patch.object(
            subprocess, "run", side_effect=observe
        ), redirect_stdout(out), redirect_stderr(err):
            with self.assertRaises(SystemExit) as result:
                py_syntax_check.main()
        return result.exception.code, out.getvalue(), err.getvalue(), compiled

    def test_captured_multi_file_targets(self) -> None:
        event = _lib.edit_event(self.payload)
        self.assertEqual((event.status, event.outcome), ("known", "success"))
        self.assertEqual([(t.path.name, t.operation) for t in event.targets], [
            ("broken_a.py", "add"), ("broken_b.py", "add"), ("good.py", "add"),
            ("notes.txt", "add"), ("renamed.py", "rename"), ("deleted.py", "delete")])
        self.assertEqual(event.targets[4].source_path, self.root / "old.py")
        self.assertTrue(all(t.path.parent == self.root for t in event.targets))

    def test_all_python_targets_checked_even_after_error(self) -> None:
        for name in ("broken_a.py", "broken_b.py", "good.py", "renamed.py", "notes.txt"):
            text = "def broken(:\n" if name.startswith("broken") else "VALUE = 1\n"
            (self.root / name).write_text(text, encoding="utf-8")
        code, out, err, compiled = self.run_hook(self.payload)
        self.assertEqual(code, 2)
        self.assertEqual(compiled, ["broken_a.py", "broken_b.py", "good.py", "renamed.py"])
        self.assertIn("broken_a.py", err)
        self.assertIn("broken_b.py", err)
        self.assertEqual(out, "")

    def test_deduplicate_and_remove_final_deleted_paths(self) -> None:
        self.payload["tool_input"]["command"] = """*** Begin Patch
*** Update File: good.py
@@
-VALUE = 0
+VALUE = 1
*** Update File: ./good.py
@@
-VALUE = 1
+VALUE = 2
*** Update File: deleted.py
@@
-old
+new
*** Delete File: deleted.py
*** End Patch"""
        (self.root / "good.py").write_text("VALUE = 2\n", encoding="utf-8")
        self.assertEqual(self.run_hook(self.payload)[3], ["good.py"])

    def test_codex_syntax_feedback_is_structured_and_complete(self) -> None:
        for name in ("broken_a.py", "broken_b.py", "good.py", "renamed.py"):
            text = "def broken(:" if name.startswith("broken") else "X = 1"
            (self.root / name).write_text(text, encoding="utf-8")
        with patch.object(sys, "argv", [str(self.root / ".codex" / "hooks" / "syntax.py")]):
            code, out, err, compiled = self.run_hook(self.payload)
        self.assertEqual((code, err), (0, ""))
        context = json.loads(out)["hookSpecificOutput"]
        self.assertEqual(context["hookEventName"], "PostToolUse")
        self.assertEqual(context["additionalContext"].count("SyntaxError"), 2)
        self.assertEqual(len(compiled), 4)

    def test_rename_extension_decides_final_python_target(self) -> None:
        for old, new, expected in (("old.py", "new.txt", []), ("old.txt", "new.py", ["new.py"])):
            with self.subTest(old=old, new=new):
                self.payload["tool_input"]["command"] = chr(10).join([
                    "*** Begin Patch", f"*** Update File: {old}", f"*** Move to: {new}",
                    "@@", "-X = 0", "+X = 1", "*** End Patch"])
                (self.root / new).write_text("X = 1", encoding="utf-8")
                self.assertEqual(self.run_hook(self.payload)[3], expected)

    def test_unknown_and_failed_outcomes_never_compile(self) -> None:
        for response, outcome in ((None, "unknown"), ({"exit_code": 0}, "unknown"),
                                  ("future output", "unknown"), ("Exit code: 1\nOutput:\nfailed", "failed"),
                                  ("apply_patch verification failed: missing", "failed")):
            with self.subTest(response=response):
                self.payload["tool_response"] = response
                self.assertEqual(_lib.edit_event(self.payload).outcome, outcome)
                code, out, err, compiled = self.run_hook(self.payload)
                self.assertEqual((code, compiled), (0, []))
                self.assertIn("unverified", out)
                self.assertIn(outcome, out)
        self.payload["hook_event_name"] = "PostToolUseFailure"
        self.assertEqual(_lib.edit_event(self.payload).outcome, "failed")
        self.assertIn("unverified", self.run_hook(self.payload)[1])
        self.payload["hook_event_name"] = "PreToolUse"
        self.assertEqual(_lib.edit_event(self.payload).outcome, "pending")
        self.assertEqual(self.run_hook(self.payload)[:3], (0, "", ""))

    def test_malformed_patch_is_entirely_unverified(self) -> None:
        for command in (None, {}, "echo shell", "*** Begin Patch\n*** End Patch",
                        "*** Begin Patch\n*** Add File: ok.py\n+x = 1\n*** Copy File: x.py\n*** End Patch",
                        "*** Begin Patch\n*** Update File: x.py\n*** End Patch",
                        "*** Begin Patch\n*** Update File: x.py\n@@\n*** End Patch",
                        "*** Begin Patch\n*** Add File: x.py\nnot a patch line\n*** End Patch",
                        "*** Begin Patch\n*** Add File: \n+x\n*** End Patch"):
            with self.subTest(command=command):
                self.payload["tool_input"] = {"command": command}
                event = _lib.edit_event(self.payload)
                self.assertEqual((event.status, event.targets), ("unverified", ()))
                self.assertIn("unverified", self.run_hook(self.payload)[1])
        self.payload["tool_input"] = "not an object"
        self.assertEqual(_lib.edit_event(self.payload).status, "unverified")

    def test_relative_absolute_and_missing_cwd(self) -> None:
        self.payload.pop("cwd")
        self.assertEqual(_lib.edit_event(self.payload).status, "unverified")
        absolute = self.root / "folder with spaces" / "target.py"
        self.payload["tool_input"]["command"] = f"*** Begin Patch\n*** Add File: {absolute}\n+X = 1\n*** End Patch"
        self.assertEqual(_lib.edit_event(self.payload).targets[0].path, absolute)

    def test_native_and_grok_behavior_and_identity(self) -> None:
        (self.root / "bad.py").write_text("def broken(:\n", encoding="utf-8")
        for name in ("Edit", "Write", "MultiEdit"):
            with self.subTest(name=name):
                payload = {"tool_name": name, "hook_event_name": "PostToolUse", "cwd": str(self.root),
                           "tool_input": {"file_path": "bad.py"}}
                self.assertIs(_lib.normalize_payload(payload), payload)
                self.assertEqual(self.run_hook(payload)[0], 2)
        grok = {"toolName": "search_replace", "hookEventName": "post_tool_use", "cwd": str(self.root),
                "toolInput": {"file_path": "bad.py"}}
        normalized = _lib.normalize_payload(grok)
        self.assertEqual(_lib.payload_agent(normalized), "grok")
        self.assertEqual(_lib.edit_event(normalized).targets[0].path, self.root / "bad.py")
        self.assertEqual(self.run_hook(grok)[0], 2)
        self.assertEqual(_lib.edit_event({"tool_name": "Read"}).status, "not_edit")

    def test_checker_inability_is_unverified(self) -> None:
        payload = {"tool_name": "Edit", "hook_event_name": "PostToolUse", "cwd": str(self.root),
                   "tool_input": {"file_path": "x.py"}}
        self.assertIn("target missing", self.run_hook(payload)[1])
        (self.root / "x.py").write_text("X = 1\n", encoding="utf-8")
        with patch.object(_lib, "find_venv_python", return_value=None), patch.object(_lib, "find_python_executable", return_value=None):
            self.assertIn("no working Python", self.run_hook(payload)[1])
        for error, message in ((subprocess.TimeoutExpired("compiler", 10), "timed out"),
                               (OSError("unavailable"), "could not start")):
            def fail(argv, **kwargs):
                raise error
            self.assertIn(message, self.run_hook(payload, fail)[1])

    def test_patch_syntax_regression(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "broken.py").write_text("def broken(:\n", encoding="utf-8")
            payload = {"hook_event_name": "PostToolUse", "cwd": str(root), "tool_name": "apply_patch",
                       "tool_input": {"command": "*** Begin Patch\n*** Add File: broken.py\n+def broken(:\n*** End Patch"},
                       "tool_response": "Success. Updated the following files:\nA broken.py\n"}
            proc = subprocess.run([sys.executable, str(Path(py_syntax_check.__file__))], input=json.dumps(payload),
                                  text=True, capture_output=True, encoding="utf-8", timeout=20, creationflags=_lib.NO_WINDOW)
            self.assertIn("SyntaxError", proc.stderr + proc.stdout)


if __name__ == "__main__":
    unittest.main()
