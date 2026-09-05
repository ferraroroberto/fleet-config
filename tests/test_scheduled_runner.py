"""Scheduled adapter conformance against sanitized events and owned fake children."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from contextlib import nullcontext
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "_lib"))
import scheduled_runner as runner
from runner_adapters import ClaudeAdapter, CodexAdapter


def fake_run(events, adapter, child_exit=0, suffix=""):
    lines = []
    formatter = runner.ProgressFormatter(adapter=adapter, emit=lines.append)
    payload = "\n".join(json.dumps(e) if isinstance(e, dict) else e for e in events)
    script = f"import sys; print({payload!r}, flush=True); " + (suffix + "; " if suffix else "") + f"sys.exit({child_exit})"
    code = runner.run_process([sys.executable, "-c", script], formatter=formatter, stall_timeout=5)
    return code, formatter, "\n".join(lines)


class ScheduledRunnerTests(unittest.TestCase):
    def setUp(self):
        self.adapters = [ClaudeAdapter(), CodexAdapter()]
        self.fixtures = {
            adapter.label: [json.loads(line) for line in
                            (ROOT / "tests" / "fixtures" / f"scheduled_{name}.jsonl").read_text(encoding="utf-8").splitlines()]
            for name, adapter in zip(("claude", "codex"), self.adapters)
        }

    def test_native_fixture_progress_completion_and_redaction(self):
        for adapter in self.adapters:
            with self.subTest(adapter=adapter.label):
                code, formatter, text = fake_run(self.fixtures[adapter.label], adapter)
                self.assertEqual(code, 0, text)
                self.assertTrue(formatter.saw_tool_use)
                self.assertIn("completed · exit 0", text)
                self.assertNotIn("raw-command", text)

    def test_missing_malformed_and_unknown_completion_are_unverified(self):
        for adapter in self.adapters:
            good = self.fixtures[adapter.label]
            for events in (good[:-1], [*good, "{truncated"], [*good, {"type": "future_side_effect"}], [{}], []):
                with self.subTest(adapter=adapter.label, events=events[-1:]):
                    code, formatter, text = fake_run(events, adapter)
                    self.assertEqual(code, runner.TRUNCATED_STREAM_EXIT_CODE, text)
                    self.assertFalse(formatter.retryable_transient_failure)
                    self.assertNotIn("✅ completed", text)

    def test_completed_prose_without_tools_is_no_work(self):
        for adapter in self.adapters:
            good = self.fixtures[adapter.label]
            events = [good[0], good[-1]]
            code, _, text = fake_run(events, adapter)
            self.assertEqual(code, runner.NO_TOOL_USE_EXIT_CODE, text)

    def test_unfinished_tools_and_children_do_not_succeed(self):
        for adapter in self.adapters:
            good = self.fixtures[adapter.label]
            if adapter.label == "Claude Code":
                pending = {"type": "system", "subtype": "task_started", "task_id": "child-1"}
            else:
                pending = {"type": "item.started", "item": {"id": "unfinished", "type": "command_execution", "status": "in_progress"}}
            code, _, text = fake_run([*good[:-1], pending, good[-1]], adapter)
            self.assertEqual(code, runner.INCOMPLETE_WORK_EXIT_CODE, text)
        start = {"type": "system", "subtype": "task_started", "task_id": "child-1"}
        stopped = {"type": "system", "subtype": "task_notification", "task_id": "child-1", "status": "stopped"}
        good = self.fixtures["Claude Code"]
        code, _, text = fake_run([good[0], start, stopped, good[-1]], ClaudeAdapter())
        self.assertEqual(code, runner.BACKGROUND_KILL_EXIT_CODE)
        self.assertIn("child failed or was interrupted", text)
        self.assertNotIn("killed after timeout", text)

    def test_codex_delegation_is_unverified_until_native_conformance(self):
        good = self.fixtures["Codex"]
        code, _, _ = fake_run([*good, {"type": "item.completed", "item": {"id": "child", "type": "collab_tool_call", "status": "completed"}}], CodexAdapter())
        self.assertEqual(code, runner.TRUNCATED_STREAM_EXIT_CODE)

    def test_auth_model_tools_and_generic_errors_are_distinct(self):
        cases = [("Authentication failed: 401", runner.AUTH_UNAVAILABLE_EXIT_CODE),
                 ("The model 'synthetic' is not supported", runner.MODEL_UNAVAILABLE_EXIT_CODE),
                 ("Required MCP server unavailable", runner.MISSING_TOOLS_EXIT_CODE),
                 ("Synthetic provider failure", 1)]
        for adapter in self.adapters:
            for message, expected in cases:
                failure = ({"type": "result", "subtype": "error", "is_error": True, "result": message}
                           if adapter.label == "Claude Code" else
                           {"type": "turn.failed", "error": {"message": message}})
                code, _, text = fake_run([failure], adapter, child_exit=1)
                self.assertEqual(code, expected, text)

    def test_failed_result_cannot_be_overridden_by_process_zero(self):
        for adapter in self.adapters:
            result = ({"type": "result", "subtype": "error_during_execution", "is_error": True, "result": "failed"}
                      if adapter.label == "Claude Code" else
                      {"type": "turn.failed", "error": {"message": "failed"}})
            self.assertEqual(fake_run([result], adapter)[0], 1)

    def test_codex_has_no_outer_retry_even_on_native_5xx_text(self):
        events = [{"type": "turn.failed", "error": {"message": "API Error: 503 synthetic"}}]
        code, formatter, _ = fake_run(events, CodexAdapter(), child_exit=1)
        self.assertEqual(code, 1)
        self.assertFalse(formatter.retryable_transient_failure)

    def test_unknown_records_close_claude_pre_effect_retry_gate(self):
        events = [{"type": "unknown_write"}, {"type": "result", "subtype": "error", "is_error": True, "result": "API Error: 503 synthetic"}]
        code, formatter, _ = fake_run(events, ClaudeAdapter(), child_exit=1)
        self.assertEqual(code, runner.TRANSIENT_API_EXIT_CODE)
        self.assertFalse(formatter.retryable_transient_failure)

    def test_unfinished_owned_descendants_close_pre_effect_retry_gate(self):
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        failure = json.dumps({"type": "result", "subtype": "error", "is_error": True,
                              "result": "API Error: 503 synthetic"})
        script = (f"import subprocess,sys;"
                  f"subprocess.Popen([sys.executable,'-c','import time;time.sleep(4)'],"
                  f"stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags={flags});"
                  f"print({failure!r},flush=True);sys.exit(1)")
        sleeps = []
        code = runner.run_with_transient_retry(
            [sys.executable, "-c", script],
            formatter=runner.ProgressFormatter(emit=lambda _: None),
            stall_timeout=0, sleep=sleeps.append)
        print(f"orphan pre-effect retry: exit={code}, retries={len(sleeps)}", flush=True)
        self.assertEqual(code, runner.TRANSIENT_API_EXIT_CODE)
        self.assertEqual(sleeps, [], "unobserved child work must forbid provider replay")

    def test_clean_parent_with_owned_orphan_is_not_reclassified_as_api_failure(self):
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        recovered = {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "API Error: 503 recovered"}]}}
        good = "\n".join(json.dumps(e) for e in [recovered, *self.fixtures["Claude Code"]])
        script = (f"import subprocess,sys;"
                  f"subprocess.Popen([sys.executable,'-c','import time;time.sleep(4)'],"
                  f"stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags={flags});"
                  f"print({good!r},flush=True)")
        formatter = runner.ProgressFormatter(emit=lambda _: None)
        code = runner.run_process([sys.executable, "-c", script], formatter=formatter, stall_timeout=0)
        print(f"clean parent with orphan: exit={code}", flush=True)
        self.assertTrue(formatter.saw_transient_api_error)
        self.assertEqual(code, runner.INCOMPLETE_WORK_EXIT_CODE)

    def test_explicit_selection_no_guessed_flags_or_fallback(self):
        command = CodexAdapter().build_command(["/smoke arg", "--model", "synthetic", "--sandbox", "read-only"], "codex-test")
        self.assertEqual(command[:2], ["codex-test", "exec"])
        self.assertIn('forced_login_method="chatgpt"', command)
        self.assertNotIn("via the Skill tool", command[-1])
        self.assertIn("Skill arguments: arg", command[-1])
        for flags in ([], ["--model", "synthetic"], ["--approve-for-me"], ["--model", "synthetic", "--approve-for-me", "--oss"], ["--model", "synthetic", "--approve-for-me", "-c", "model_provider=other"]):
            with self.assertRaises(ValueError):
                CodexAdapter().build_command(["/smoke", *flags])
        with self.assertRaises(ValueError):
            ClaudeAdapter().build_command(["/smoke", "--fallback-model", "other"])
        self.assertEqual(runner.main(["--harness", "pi", "/smoke"]), 2)
        self.assertEqual(runner.main(["--harness", "grok", "/smoke"]), 2)

    def test_delivery_runs_once_after_child_failure_and_keeps_its_code(self):
        for exit_code in (0, 7):
            with patch.object(runner, "run_with_transient_retry", return_value=exit_code), patch.object(runner, "run_delivery_check", return_value=False) as check:
                code = runner.main(["--harness", "codex", "/smoke", "--model", "synthetic", "--sandbox", "read-only", "--delivery-check", "synthetic.py"])
            check.assert_called_once()
            self.assertEqual(code, 121 if exit_code == 0 else exit_code)

    def test_unconfirmed_cancellation_never_reports_tree_stopped(self):
        cancel = threading.Event()
        cancel.set()
        lines = []
        with patch.object(runner, "_kill_process_tree", return_value=False):
            code = runner.run_process(
                [sys.executable, "-c", "import time; time.sleep(0.4)"],
                formatter=runner.ProgressFormatter(emit=lines.append),
                stall_timeout=0, cancel_event=cancel,
            )
        self.assertEqual(code, runner.CANCELLATION_UNCONFIRMED_EXIT_CODE)
        self.assertIn("cancellation not confirmed", "\n".join(lines))
        self.assertNotIn("owned process tree stopped", "\n".join(lines))


    def test_cancellation_after_parent_exit_does_not_wait_for_descendant_eof(self):
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        real_popen = subprocess.Popen
        captured = []
        launched = threading.Event()
        cancel = threading.Event()
        requested = []
        errors = []
        with tempfile.TemporaryDirectory(prefix="runner_orphan_") as folder:
            marker = Path(folder) / "descendant_completed"
            ready = Path(folder) / "descendant_started"
            descendant = f"import time; from pathlib import Path; Path({str(ready)!r}).touch(); time.sleep(4); Path({str(marker)!r}).touch()"
            child = f"import subprocess,sys; subprocess.Popen([sys.executable,'-c',{descendant!r}],stdout=sys.stdout,stderr=sys.stderr,creationflags={flags})"
            def capture(*args, **kwargs):
                process = real_popen(*args, **kwargs)
                captured.append(process)
                launched.set()
                return process
            def cancel_after_exit():
                try:
                    assert launched.wait(3), "child never launched"
                    captured[0].wait(timeout=3)
                    deadline = time.monotonic() + 2
                    while not ready.exists() and time.monotonic() < deadline:
                        time.sleep(0.01)
                    assert ready.exists(), "descendant did not start"
                    requested.append(time.monotonic())
                    cancel.set()
                except Exception as exc:
                    errors.append(str(exc))
                    cancel.set()
            observer = threading.Thread(target=cancel_after_exit)
            observer.start()
            try:
                with patch.object(runner.subprocess, "Popen", side_effect=capture):
                    code = runner.run_process([sys.executable, "-c", child],
                                              formatter=runner.ProgressFormatter(emit=lambda _: None),
                                              stall_timeout=5, cancel_event=cancel)
                returned = time.monotonic()
            finally:
                observer.join(timeout=6)
            self.assertFalse(observer.is_alive())
            self.assertEqual(errors, [])
            self.assertTrue(requested)
            delay = returned - requested[0]
            print(f"orphan cancellation: exit={code}, delay={delay:.3f}s, descendant_marker={marker.exists()}", flush=True)
            self.assertLess(delay, 2.0, "cancellation waited for an orphan's pipe EOF")
            self.assertEqual(code, runner.CANCELLED_EXIT_CODE)
            self.assertFalse(marker.exists(), "owned descendant survived cancellation")

    @unittest.skipUnless(sys.platform == "win32", "Windows job ownership")
    def test_ownership_rejection_never_launches_provider(self):
        from process_scope import _WindowsJob
        with tempfile.TemporaryDirectory(prefix="runner_ownership_") as folder:
            marker = Path(folder) / "provider_started"
            script = f"from pathlib import Path; Path({str(marker)!r}).touch()"
            with patch.object(_WindowsJob, "assign", side_effect=OSError("ownership rejected")):
                with self.assertRaisesRegex(OSError, "ownership rejected"):
                    runner.run_process([sys.executable, "-c", script], stall_timeout=0)
            self.assertFalse(marker.exists())

    @unittest.skipUnless(sys.platform == "win32", "Windows job ownership")
    def test_orphan_pipe_matrix_and_unconfirmed_cleanup_are_bounded(self):
        from process_scope import _WindowsJob
        flags = subprocess.CREATE_NO_WINDOW
        good = "\n".join(json.dumps(e) for e in self.fixtures["Codex"])
        for pipe in ("stdout", "stderr", "both", "neither"):
            for failure in ("none", "terminate", "query"):
                with self.subTest(pipe=pipe, failure=failure), tempfile.TemporaryDirectory(prefix="runner_drain_") as folder:
                    marker = Path(folder) / "descendant_completed"
                    ready = Path(folder) / "descendant_started"
                    descendant = f"import os,time;from pathlib import Path;Path({str(ready)!r}).write_text(str(os.getpid()));time.sleep(4);Path({str(marker)!r}).touch()"
                    out = "sys.stdout" if pipe in ("stdout", "both") else "subprocess.DEVNULL"
                    err = "sys.stderr" if pipe in ("stderr", "both") else "subprocess.DEVNULL"
                    script = (f"import subprocess,sys,time;from pathlib import Path;"
                              f"subprocess.Popen([sys.executable,'-c',{descendant!r}],stdout={out},stderr={err},creationflags={flags});"
                              f"print({good!r},flush=True)")
                    lines = []
                    cancel = threading.Event()
                    handles = []
                    api = ctypes.WinDLL("kernel32", use_last_error=True)
                    api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
                    api.OpenProcess.restype = wintypes.HANDLE
                    api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
                    api.WaitForSingleObject.restype = wintypes.DWORD
                    api.CloseHandle.argtypes = [wintypes.HANDLE]
                    api.CloseHandle.restype = wintypes.BOOL
                    def request():
                        deadline = time.monotonic()+3
                        while time.monotonic() < deadline:
                            try:
                                pid = int(ready.read_text())
                            except (OSError, ValueError):
                                time.sleep(.01)
                                continue
                            handle = api.OpenProcess(0x100000, False, pid)
                            if handle:
                                handles.append(handle)
                            break
                        if failure != "none":
                            cancel.set()
                    thread = threading.Thread(target=request)
                    thread.start()
                    attribute = "terminate" if failure == "terminate" else "active"
                    value = False if failure == "terminate" else None
                    context = patch.object(_WindowsJob, attribute, return_value=value)
                    if failure == "none":
                        context = nullcontext()
                    started = time.monotonic()
                    formatter = runner.ProgressFormatter(adapter=CodexAdapter(), emit=lines.append)
                    with context:
                        code = runner.run_process(
                            [sys.executable, "-c", script],
                            formatter=formatter,
                            stall_timeout=0, cancel_event=cancel if failure != "none" else None)
                    thread.join(timeout=4)
                    self.assertFalse(thread.is_alive())
                    self.assertEqual(len(handles), 1, "must retain the live descendant handle")
                    try:
                        self.assertEqual(api.WaitForSingleObject(handles[0], 2000), 0,
                                         "the owned descendant has not exited")
                    finally:
                        for handle in handles:
                            api.CloseHandle(handle)
                    self.assertLess(time.monotonic()-started, 3.5, "\n".join(lines))
                    self.assertEqual(code, runner.INCOMPLETE_WORK_EXIT_CODE if failure == "none" else runner.CANCELLATION_UNCONFIRMED_EXIT_CODE, "\n".join(lines))
                    self.assertFalse(marker.exists())
                    if failure == "none":
                        self.assertTrue(formatter.saw_tool_use)
                        self.assertTrue(formatter._saw_result)
                        self.assertFalse(formatter._malformed)
                    self.assertNotIn("owned process tree stopped", "\n".join(lines))
                    # The final close safety net must terminate owned processes,
                    # even when the explicit termination/query result is unknown.

    def test_unclosed_pipes_cannot_confirm_cancellation(self):
        event = threading.Event()
        event.set()
        lines = []
        started = time.monotonic()
        with patch.object(runner, "_kill_process_tree", return_value=True):
            code = runner.run_process(
                [sys.executable, "-c", "import time; time.sleep(5)"],
                formatter=runner.ProgressFormatter(emit=lines.append),
                stall_timeout=0, cancel_event=event)
        self.assertLess(time.monotonic()-started, 4)
        self.assertEqual(code, runner.CANCELLATION_UNCONFIRMED_EXIT_CODE)
        self.assertNotIn("owned process tree stopped", "\n".join(lines))

    def test_stall_keeps_its_code_but_names_unconfirmed_termination(self):
        lines = []
        with patch.object(runner, "_kill_process_tree", return_value=False):
            code = runner.run_process(
                [sys.executable, "-c", "import time; time.sleep(5)"],
                formatter=runner.ProgressFormatter(emit=lines.append),
                stall_timeout=.1)
        self.assertEqual(code, runner.STALL_EXIT_CODE)
        self.assertIn("owned termination unconfirmed", "\n".join(lines))

    def test_cwd_and_utf8_are_preserved(self):
        with tempfile.TemporaryDirectory(prefix="runner_cwd_") as folder:
            previous = Path.cwd()
            try:
                os.chdir(folder)
                code, _, _ = fake_run(self.fixtures["Codex"], CodexAdapter(), suffix="open('caf\\u00e9.txt','w',encoding='utf-8').write('caf\\u00e9')")
                self.assertEqual(code, 0)
                self.assertEqual(Path("café.txt").read_text(encoding="utf-8"), "café")
            finally:
                os.chdir(previous)

    def test_stall_and_owned_tree_cancellation_leave_unrelated_child_alive(self):
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], creationflags=flags)
        try:
            for cancel in (False, True):
                with tempfile.TemporaryDirectory(prefix="runner_tree_") as folder:
                    sentinel = Path(folder) / "grandchild_finished"
                    started = Path(folder) / "grandchild_started"
                    grandchild = f"import time; from pathlib import Path; Path({str(started)!r}).touch(); time.sleep(3); Path({str(sentinel)!r}).touch()"
                    script = f"import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',{grandchild!r}],creationflags={flags}); time.sleep(30)"
                    event = threading.Event()
                    stop_observer = threading.Event()
                    formatter = runner.ProgressFormatter(emit=lambda _: None)
                    def observe_readiness():
                        deadline = time.monotonic() + 5
                        while not started.exists() and time.monotonic() < deadline and not stop_observer.is_set():
                            # Fixture setup is not the idle period under test.
                            # Arm the real watchdog only after its target exists.
                            formatter._touch()
                            time.sleep(.01)
                        if started.exists():
                            formatter._touch()
                            event.set()
                    observer = threading.Thread(target=observe_readiness)
                    observer.start()
                    try:
                        code = runner.run_process([sys.executable, "-c", script], formatter=formatter, stall_timeout=0 if cancel else 1, cancel_event=event if cancel else None)
                    finally:
                        stop_observer.set()
                        observer.join(timeout=6)
                    self.assertFalse(observer.is_alive())
                    self.assertEqual(code, runner.CANCELLED_EXIT_CODE if cancel else runner.STALL_EXIT_CODE)
                    self.assertTrue(started.exists(), "the owned grandchild must start before cancellation")
                    time.sleep(3)
                    self.assertFalse(sentinel.exists(), "owned grandchild survived termination")
                    self.assertIsNone(unrelated.poll(), "unrelated process was killed")
        finally:
            unrelated.kill()
            unrelated.wait(timeout=10)


if __name__ == "__main__":
    unittest.main()
