"""Scheduled adapter conformance against sanitized events and owned fake children."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from contextlib import nullcontext
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "_lib"))
import scheduled_runner as runner
from runner_adapters import ClaudeAdapter, CodexAdapter, describe_record
import process_scope


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
        self.background_tasks = [
            json.loads(line) for line in
            (ROOT / "tests" / "fixtures" / "scheduled_claude_background_tasks.jsonl")
            .read_text(encoding="utf-8").splitlines() if line.strip()
        ]

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

    # ---- the parser's knowledge, not the verdict policy (fleet-config#841) ----
    #
    # On 2026-09-11 every Claude-driven scheduled job on this host ended
    # `❓ not confirmed · exit 122` while demonstrably doing its work —
    # config-map committed a4758b7 and posted to Telegram, learning-log posted
    # its digest. Each ended `0 malformed and N unknown`: the stream was
    # intact, the parser had simply fallen behind it. `--output-format
    # stream-json` had begun emitting the background-task store
    # (`background_tasks_changed`) and a task status patch (`task_updated`)
    # alongside the lifecycle records, and because this runner sets
    # `CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0`, every slow shell command becomes
    # a background task — three unknown records apiece. "Not confirmed" fired on
    # every run and so stopped distinguishing a truncated run from a healthy
    # one, which is the exact failure the state exists to prevent.
    #
    # The fixture is sanitized from a real 2.1.269 capture and covers both task
    # types observed (`local_bash`, `local_agent`).

    def test_backgrounded_task_stream_is_recognised_end_to_end(self):
        code, formatter, text = fake_run(self.background_tasks, ClaudeAdapter())
        self.assertEqual(code, 0, text)
        self.assertIn("✅ completed · exit 0", text)
        self.assertEqual(formatter._unknown, 0, text)
        self.assertEqual(formatter._malformed, 0, text)
        self.assertNotIn("unknown stream record", text)
        # The lifecycle is still read from `task_notification` alone: two
        # children started, two ended, neither counted twice by the patch.
        self.assertFalse(formatter.unfinished_work, text)
        self.assertEqual(formatter._child_failures, 0, text)
        self.assertEqual(text.count("✓ task completed"), 2, text)

    def test_truncated_background_task_stream_is_still_unconfirmed(self):
        """Teaching the parser must not teach it to forgive a cut-off stream."""
        code, _, text = fake_run(self.background_tasks[:-1], ClaudeAdapter())
        self.assertEqual(code, runner.TRUNCATED_STREAM_EXIT_CODE, text)
        self.assertIn("not confirmed", text)
        self.assertNotIn("✅ completed", text)

    def test_unknown_records_name_the_shape_they_were(self):
        """A bare count is what made #841 invisible; the log must say what drifted."""
        good = self.fixtures["Claude Code"]
        drifted = [
            {"type": "system", "subtype": "some_future_subtype"},
            {"type": "system", "subtype": "some_future_subtype"},
            {"type": "future_top_level"},
            {"type": "assistant", "message": {"content": [{"type": "future_block"}]}},
        ]
        code, formatter, text = fake_run([*good[:-1], *drifted, good[-1]], ClaudeAdapter())
        self.assertEqual(code, runner.TRUNCATED_STREAM_EXIT_CODE, text)
        self.assertIn("4 unknown stream record(s)", text)
        self.assertIn("system/some_future_subtype ×2", text)
        self.assertIn("future_top_level ×1", text)
        self.assertIn("assistant/block=future_block ×1", text)
        # Shape only, and nothing else off the record: a descriptor that ever
        # carried a payload would paste prompts or paths into the job log.
        self.assertEqual(
            set(formatter.unknown_summary().split(", ")),
            {"system/some_future_subtype ×2", "future_top_level ×1",
             "assistant/block=future_block ×1"},
        )
        formatter.reset_for_retry()
        self.assertEqual(formatter.unknown_summary(), "")

    def test_unknown_descriptors_are_sanitized_and_capped(self):
        self.assertEqual(describe_record("system", "a b\nc"), "system/a?b?c")
        self.assertEqual(describe_record("system", None, ""), "system")
        self.assertEqual(describe_record(None), "unlabelled")
        self.assertEqual(len(describe_record("x" * 200)), 40)

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
        # A *finished* child closes its own slot, whatever it finished as, so a
        # stopped or failed one leaves no unfinished work behind. What it must
        # not do is decide the run's outcome — that is #808, covered below.
        start = {"type": "system", "subtype": "task_started", "task_id": "child-1"}
        stopped = {"type": "system", "subtype": "task_notification", "task_id": "child-1", "status": "stopped"}
        good = self.fixtures["Claude Code"]
        code, formatter, text = fake_run([good[0], start, stopped, good[-1]], ClaudeAdapter())
        self.assertFalse(formatter.unfinished_work, text)
        self.assertNotEqual(code, runner.INCOMPLETE_WORK_EXIT_CODE, text)

    # ---- a failed or cancelled background child is not this run's verdict ----
    #
    # fleet-config#808. `fleet-health` run 20260909T233001 delivered its full
    # three-machine digest, appended its ledger and sent its Telegram digest,
    # and was reported `❌ failed · child failed or was interrupted · exit 125`
    # with an alert_on_failure ping at 00:55. Two children had ended `failed`:
    # a disk scan the agent deliberately `TaskStop`ped on noticing it was
    # pointed at E: instead of the monitored C:, and a backgrounded PowerShell
    # that exited non-zero after `docker system df` timed out at 90s — the very
    # next line of that log is "Found the disk growth."
    #
    # Neither string in KILL_SIGNATURE_TERMS appeared anywhere in the 337-line
    # log, so the trip came through the child-failure flag `saw_kill_signature`
    # used to fold in. These replay that stream shape.

    def test_failed_background_child_does_not_red_a_delivered_run(self):
        good = self.fixtures["Claude Code"]
        start = {"type": "system", "subtype": "task_started", "task_id": "child-1"}
        failed = {"type": "system", "subtype": "task_notification", "task_id": "child-1", "status": "failed"}
        code, formatter, text = fake_run([*good[:-1], start, failed, good[-1]], ClaudeAdapter())
        self.assertEqual(code, 0, text)
        self.assertIn("✅ completed · exit 0", text)
        self.assertFalse(formatter.saw_kill_signature, text)
        # Not a verdict, but not silent either.
        self.assertIn("background task(s): 1 failed", text)

    def test_cancelled_background_child_is_a_stop_not_a_failure(self):
        good = self.fixtures["Claude Code"]
        start = {"type": "system", "subtype": "task_started", "task_id": "child-1"}
        stopped = {"type": "system", "subtype": "task_notification", "task_id": "child-1", "status": "stopped"}
        code, formatter, text = fake_run([*good[:-1], start, stopped, good[-1]], ClaudeAdapter())
        self.assertEqual(code, 0, text)
        self.assertIn("⊘ task stopped", text)
        self.assertNotIn("task failed", text)
        self.assertFalse(formatter.saw_kill_signature, text)
        self.assertIn("background task(s): 1 stopped", text)

    def test_replayed_fleet_health_run_no_longer_reports_false_failure(self):
        """The real 20260909T233001 shape: one stopped child, one failed one."""
        good = self.fixtures["Claude Code"]
        events = [
            *good[:-1],
            {"type": "system", "subtype": "task_started", "task_id": "bciid0lgb"},
            {"type": "system", "subtype": "task_notification", "task_id": "bciid0lgb", "status": "stopped"},
            {"type": "system", "subtype": "task_started", "task_id": "disk-growth"},
            {"type": "system", "subtype": "task_notification", "task_id": "disk-growth", "status": "failed"},
            # The recovered tool call that produced the answer anyway.
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": "toolu_replay", "name": "PowerShell", "input": {}}]}},
            {"type": "user", "message": {"content": [
                {"tool_use_id": "toolu_replay", "type": "tool_result"}]}},
            good[-1],
        ]
        code, _, text = fake_run(events, ClaudeAdapter(), child_exit=0)
        self.assertEqual(code, 0, text)
        self.assertNotIn("❌ failed", text)
        self.assertNotIn("child failed or was interrupted", text)
        self.assertIn("background task(s): 1 failed, 1 stopped", text)

    def test_background_kill_exit_code_needs_the_real_stderr_signature(self):
        """125 stays reachable only from KILL_SIGNATURE_TERMS on stderr."""
        good = self.fixtures["Claude Code"]
        start = {"type": "system", "subtype": "task_started", "task_id": "child-1"}
        failed = {"type": "system", "subtype": "task_notification", "task_id": "child-1", "status": "failed"}
        events = [*good[:-1], start, failed, good[-1]]
        self.assertNotEqual(fake_run(events, ClaudeAdapter())[0], runner.BACKGROUND_KILL_EXIT_CODE)
        # Same stream, plus the line the CLI actually prints when it kills
        # in-flight sub-agents: still 125, undiminished.
        kill = ("print('Background tasks still running after 600s; terminating', "
                "file=sys.stderr, flush=True)")
        code, formatter, text = fake_run(events, ClaudeAdapter(), suffix=kill)
        self.assertEqual(code, runner.BACKGROUND_KILL_EXIT_CODE, text)
        self.assertTrue(formatter.saw_kill_signature, text)
        self.assertIn("background tasks killed after timeout", text)

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
                    descendant = f"import os,time;from pathlib import Path;Path({str(ready)!r}).write_text(str(os.getpid()));time.sleep(8);Path({str(marker)!r}).touch()"
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
                        deadline = time.monotonic()+6
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
                    returned = time.monotonic()
                    thread.join(timeout=4)
                    self.assertFalse(thread.is_alive())
                    self.assertEqual(len(handles), 1, "must retain the live descendant handle")
                    try:
                        self.assertEqual(api.WaitForSingleObject(handles[0], 2000), 0,
                                         "the owned descendant has not exited")
                    finally:
                        for handle in handles:
                            api.CloseHandle(handle)
                    self.assertLess(returned-started, 5, "\n".join(lines))
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


class PosixScopeTests(unittest.TestCase):
    @patch.object(process_scope.signal, "SIGKILL", 9, create=True)
    def test_launch_and_group_termination_contract(self):
        child = Mock(pid=12345)
        with patch.object(process_scope.sys, "platform", "linux"), patch.object(process_scope.subprocess, "Popen", return_value=child) as popen:
            scope = process_scope.ProcessScope()
            self.assertIs(scope.launch(["synthetic-child"], env={"KEY": "value"}), child)
        self.assertIsNone(scope.job)
        self.assertEqual(popen.call_args.args, (["synthetic-child"],))
        self.assertTrue(popen.call_args.kwargs["start_new_session"])
        self.assertEqual(popen.call_args.kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(popen.call_args.kwargs["env"], {"KEY": "value"})
        with patch.object(process_scope.os, "killpg", create=True, side_effect=[None, ProcessLookupError]) as killpg:
            self.assertTrue(scope.terminate())
        self.assertEqual(killpg.call_args_list[0].args, (child.pid, process_scope.signal.SIGKILL))
        self.assertEqual(killpg.call_args_list[1].args, (child.pid, 0))
        with patch.object(process_scope.os, "killpg", create=True, side_effect=PermissionError):
            self.assertIsNone(scope.active())
            self.assertFalse(scope.terminate())
        scope.close()


@unittest.skipUnless(sys.platform == "win32", "Windows job ownership")
class WindowsScopeTests(unittest.TestCase):
    def setUp(self):
        # The acceptance dispatcher may itself use a base interpreter. Always
        # exercise this checkout's existing venv redirector, including the
        # scope's bootstrap; a base-only run cannot prove this regression.
        venv = ROOT / ".venv"
        self.assertTrue((venv / "pyvenv.cfg").is_file(), "ownership proof requires the existing project venv")
        executable = venv / "Scripts" / "python.exe"
        self.assertTrue(executable.is_file())
        executable_patch = patch.object(process_scope.sys, "executable", str(executable))
        executable_patch.start()
        self.addCleanup(executable_patch.stop)
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        for name, arguments, result in (
            ("OpenProcess", [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            ("IsProcessInJob", [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)], wintypes.BOOL),
            ("WaitForSingleObject", [wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
            ("CloseHandle", [wintypes.HANDLE], wintypes.BOOL),
        ):
            function = getattr(self.api, name)
            function.argtypes, function.restype = arguments, result
        self.handles = []

    def tearDown(self):
        for handle in self.handles:
            self.api.CloseHandle(handle)

    def retain(self, pid):
        handle = self.api.OpenProcess(0x100000 | 0x1000, False, pid)
        self.assertTrue(handle, f"cannot retain owned process {pid}")
        self.handles.append(handle)
        return handle

    def assert_member(self, handle, scope):
        member = wintypes.BOOL()
        self.assertTrue(self.api.IsProcessInJob(handle, scope.job.handle, ctypes.byref(member)))
        self.assertTrue(member.value, "venv base interpreter escaped the exact private job")

    def launch_compatible(self, scope, command):
        # Keep the repro executable against the pre-fix API as well.
        if hasattr(scope, "launch"):
            return scope.launch(command)
        child = subprocess.Popen(scope.command(command), stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 creationflags=subprocess.CREATE_NO_WINDOW)
        scope.start(child)
        return child

    def test_delayed_venv_assignment_contains_launcher_bootstrap_and_provider(self):
        self.assertNotEqual(Path(sys.executable), Path(sys._base_executable),
                            "run the ownership regression with the real venv redirector")
        original = process_scope._WindowsJob.assign
        with tempfile.TemporaryDirectory(prefix="scope_delay_") as folder:
            ready = Path(folder) / "pids"
            command = [sys.executable, "-I", "-c",
                       f"import os,time;from pathlib import Path;Path({str(ready)!r}).write_text(f'{{os.getpid()}} {{os.getppid()}}');time.sleep(2)"]
            scope = process_scope.ProcessScope()
            child = None
            def delayed(job, process):
                self.retain(process.pid)
                time.sleep(.5)
                original(job, process)
            try:
                with patch.object(process_scope._WindowsJob, "assign", delayed):
                    child = self.launch_compatible(scope, command)
                deadline = time.monotonic() + 3
                while not ready.exists() and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertTrue(ready.exists())
                provider, bootstrap = map(int, ready.read_text().split())
                self.retain(provider)
                self.retain(bootstrap)
                for handle in self.handles:
                    self.assert_member(handle, scope)
                self.assertEqual(child.wait(timeout=5), 0)
                for handle in self.handles:
                    self.assertEqual(self.api.WaitForSingleObject(handle, 2000), 0)
                deadline = time.monotonic() + 2
                while scope.active() != 0 and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertEqual(scope.active(), 0)
                print("500ms venv assignment: launcher/bootstrap/provider exact-job membership=True; normal exit confirmed", flush=True)
            finally:
                # Pre-fix children naturally finish; do not mistake empty job
                # accounting for proof that the escaped process has stopped.
                if child is not None:
                    child.wait(timeout=6)
                    for stream in (child.stdin, child.stdout, child.stderr):
                        if stream is not None:
                            stream.close()
                scope.close()

    def test_launch_failure_and_interrupt_collect_every_created_process(self):
        for stage in ("assign", "release", "release_written", "resume", "interrupt_assign", "interrupt_release"):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory(prefix="scope_abort_") as folder:
                marker = Path(folder) / "provider_started"
                command = [sys.executable, "-I", "-c", f"from pathlib import Path;Path({str(marker)!r}).touch()"]
                scope = process_scope.ProcessScope()
                original_assign = process_scope._WindowsJob.assign
                original_release = scope._release
                captured = []
                error = KeyboardInterrupt if stage.startswith("interrupt") else OSError
                def assign(job, process):
                    captured.append(process)
                    self.retain(process.pid)
                    time.sleep(.5)
                    if stage in ("assign", "interrupt_assign"):
                        raise error("injected assignment failure")
                    original_assign(job, process)
                    self.assert_member(self.handles[-1], scope)
                def release():
                    if stage == "release_written":
                        original_release()
                    if stage in ("release", "release_written", "interrupt_release"):
                        raise error("injected release failure")
                    original_release()
                context = (patch.object(scope.job, "resume", side_effect=OSError("injected resume failure"))
                           if stage == "resume" else nullcontext())
                with patch.object(process_scope._WindowsJob, "assign", assign), patch.object(scope, "_release", release), context:
                    with self.assertRaises(error):
                        scope.launch(command)
                self.assertEqual(len(captured), 1)
                self.assertIsNotNone(captured[0].poll())
                self.assertEqual(self.api.WaitForSingleObject(self.handles[-1], 0), 0)
                self.assertFalse(marker.exists(), "provider ran before a successful release")
                self.assertTrue(all(stream.closed for stream in (captured[0].stdin, captured[0].stdout, captured[0].stderr)))
        print("assign/release/resume failure and launch interrupts: provider absent; retained launch handles terminal", flush=True)

    def test_interrupt_after_resume_retains_and_stops_descendants(self):
        with tempfile.TemporaryDirectory(prefix="scope_resumed_") as folder:
            ready = Path(folder) / "pids"
            command = [sys.executable, "-I", "-c",
                       f"import os,time;from pathlib import Path;Path({str(ready)!r}).write_text(f'{{os.getpid()}} {{os.getppid()}}');time.sleep(5)"]
            scope = process_scope.ProcessScope()
            original_resume = scope.job.resume
            def resume(process):
                self.retain(process.pid)
                original_resume(process)
                deadline = time.monotonic() + 3
                while not ready.exists() and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertTrue(ready.exists())
                for pid in map(int, ready.read_text().split()):
                    self.assert_member(self.retain(pid), scope)
                raise KeyboardInterrupt("after native resume")
            with patch.object(scope.job, "resume", resume):
                with self.assertRaises(KeyboardInterrupt):
                    scope.launch(command)
            for handle in self.handles:
                self.assertEqual(self.api.WaitForSingleObject(handle, 2000), 0)

    def test_unqueryable_launch_cleanup_is_unknown_even_when_close_kills(self):
        scope = process_scope.ProcessScope()
        def release():
            self.retain(scope.process.pid)
            raise KeyboardInterrupt("before release")
        with patch.object(scope, "_release", release), patch.object(scope.job, "active", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "cleanup unknown"):
                scope.launch([sys.executable, "-I", "-c", "raise SystemExit(0)"])
        self.assertEqual(self.api.WaitForSingleObject(self.handles[-1], 2000), 0)

    def test_sigint_between_native_creation_and_popen_return_is_collected(self):
        scope = process_scope.ProcessScope()
        original_popen = subprocess.Popen
        original_handler = signal.getsignal(signal.SIGINT)
        with tempfile.TemporaryDirectory(prefix="scope_create_interrupt_") as folder:
            marker = Path(folder) / "provider_started"
            command = [sys.executable, "-I", "-c", f"from pathlib import Path;Path({str(marker)!r}).touch()"]
            def create_then_interrupt(*args, **kwargs):
                child = original_popen(*args, **kwargs)
                self.retain(child.pid)
                signal.raise_signal(signal.SIGINT)
                return child
            with patch.object(process_scope.subprocess, "Popen", side_effect=create_then_interrupt):
                with self.assertRaises(KeyboardInterrupt):
                    scope.launch(command)
            self.assertIs(signal.getsignal(signal.SIGINT), original_handler)
            self.assertEqual(self.api.WaitForSingleObject(self.handles[-1], 0), 0)
            self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
