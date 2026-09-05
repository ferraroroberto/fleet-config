"""Sanitized quota contract, native protocol and real atomic-I/O regressions."""
from __future__ import annotations

import copy
import datetime as dt
import io
import json
import os
import queue
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "skills" / "_lib"))
import quota_snapshot as contract
import quota_sources as sources

NOW = dt.datetime(2026, 9, 5, 12, tzinfo=dt.timezone.utc)
RESET = int((NOW + dt.timedelta(days=1)).timestamp())


def codex_fixture() -> dict:
    return {"accountId": "synthetic-account", "rateLimits": {
        "limitId": "codex", "primary": {"usedPercent": 42, "windowDurationMins": 10080,
                                      "resetsAt": RESET}, "secondary": None,
        "credits": {"balance": "DO-NOT-PUBLISH"}, "planType": "DO-NOT-PUBLISH"},
        "private": "DO-NOT-PUBLISH"}


def codex() -> dict:
    return sources.codex_source(codex_fixture(), "0.153.3", now=NOW)


def claude() -> dict:
    with patch.object(sources, "utc_now", return_value=NOW):
        return sources.claude_source({"captured_at": "2026-09-05T14:00:00+02:00",
                                     "five_hour": {"used_percentage": 31, "resets_at": RESET},
                                     "seven_day": None, "private": "DO-NOT-PUBLISH"})


class ContractTests(unittest.TestCase):
    def test_native_windows_identity_utc_and_allowlisted_output(self) -> None:
        c, o = claude(), codex()
        self.assertEqual((c["provider"], o["provider"]), ("anthropic", "openai"))
        cw, ow = c["observations"][0]["windows"][0], o["observations"][0]["windows"][0]
        self.assertEqual((cw["duration_minutes"], ow["duration_minutes"]), (300, 10080))
        self.assertEqual(ow["id"], "primary")
        self.assertEqual(len(o["observations"][0]["windows"]), 1)
        self.assertTrue(cw["resets_at"].endswith("Z"))
        self.assertEqual(c["observations"][0]["observed_at"], contract.iso_utc(NOW))
        self.assertIsNone(c["observations"][0]["pool_id"])
        self.assertEqual(c["observations"][0]["account"]["state"], "unknown")
        self.assertIsNotNone(o["observations"][0]["pool_id"])
        serialized = json.dumps([contract.validate_source(c), contract.validate_source(o)])
        for private in ("synthetic-account", "DO-NOT-PUBLISH", "balance", "planType"):
            self.assertNotIn(private, serialized)

    def test_multi_bucket_map_retains_native_windows_without_fallback_duplicate(self) -> None:
        raw = codex_fixture()
        raw["rateLimitsByLimitId"] = {"codex": copy.deepcopy(raw["rateLimits"]),
                                     "another": {"limitId": "another", "primary": {
                                         "usedPercent": 0, "windowDurationMins": 15, "resetsAt": None}}}
        result = sources.codex_source(raw, "0.153.3", now=NOW)
        self.assertEqual(len(result["observations"]), 2)
        second = result["observations"][1]["windows"][0]
        self.assertEqual(second["duration_minutes"], 15)
        self.assertEqual(second["used_percentage"], 0)
        self.assertIsNone(second["resets_at"])
        self.assertEqual(second["state"], "available")

    def test_missing_malformed_and_version_drift_never_invent_capacity(self) -> None:
        bad_shapes = [None, [], {}, {"rateLimits": []},
                      {"rateLimits": {"limitId": ["bad"]}}, {"rateLimits": {"limitId": []}}, {"rateLimits": {}, "rateLimitsByLimitId": []},
                      {"rateLimits": {"limitId": "codex", "primary": []}},
                      {"rateLimits": {"limitId": "codex", "primary": {"usedPercent": None}}},
                      {"rateLimits": {"primary": {"usedPercent": 0, "windowDurationMins": 300}}}]
        for raw in bad_shapes:
            with self.subTest(raw=raw):
                result = sources.codex_source(raw, "0.153.3", now=NOW)
                self.assertNotEqual(result["state"], "available")
                contract.validate_source(result)
        self.assertEqual(sources.codex_source(codex_fixture(), "0.154.0", now=NOW)["state"], "unsupported")
        raw = codex_fixture()
        raw["rateLimits"]["primary"]["usedPercent"] = None
        result = sources.codex_source(raw, "0.153.3", now=NOW)
        self.assertIsNone(result["observations"][0]["windows"][0]["used_percentage"])
        with patch.object(sources, "utc_now", return_value=NOW):
            for raw in [None, {}, {"captured_at": NOW.isoformat(), "five_hour": []},
                        {"captured_at": "2026-09-05T12:00:00", "five_hour": {"used_percentage": 0}}]:
                self.assertNotEqual(sources.claude_source(raw)["state"], "available")

    def test_bad_numbers_duration_and_reset_are_not_available(self) -> None:
        for field, value in [("usedPercent", True), ("usedPercent", -1), ("usedPercent", 101),
                             ("usedPercent", float("nan")), ("usedPercent", float("inf")),
                             ("usedPercent", "0"), ("windowDurationMins", 0),
                             ("windowDurationMins", True), ("resetsAt", "naive"),
                             ("resetsAt", "2026-09-05T12:00:00"), ("resetsAt", True)]:
            with self.subTest(field=field, value=value):
                raw = codex_fixture()
                raw["rateLimits"]["primary"][field] = value
                result = sources.codex_source(raw, "0.153.3", now=NOW)
                self.assertNotEqual(result["state"], "available")
                contract.validate_source(result)

    def test_absent_account_is_unbound_not_a_default_pool(self) -> None:
        raw = codex_fixture()
        del raw["accountId"]
        result = sources.codex_source(raw, "0.153.3", now=NOW)
        self.assertEqual(result["state"], "available")
        self.assertIsNone(result["observations"][0]["pool_id"])
        other = codex_fixture()
        other["accountId"] = "synthetic-other-account"
        self.assertNotEqual(codex()["observations"][0]["pool_id"],
                            sources.codex_source(other, "0.153.3", now=NOW)["observations"][0]["pool_id"])

    def test_staleness_at_read_time_and_elapsed_reset_never_refresh_to_zero(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            contract.publish(codex(), root)
            fresh = contract.read_snapshot(root, now=NOW + dt.timedelta(seconds=1))
            late = contract.read_snapshot(root, now=NOW + dt.timedelta(seconds=601))
            self.assertEqual(fresh["pools"][0]["state"], "available")
            self.assertEqual(late["pools"][0]["state"], "stale")
            self.assertEqual(late["pools"][0]["windows"][0]["used_percentage"], 42)
            badclock = contract.read_snapshot(root, now=NOW - dt.timedelta(seconds=1))
            self.assertEqual(badclock["pools"], [])
            self.assertIn("future_timestamp", [s["reason"] for s in badclock["sources"]])
        source = codex()
        source["observations"][0]["windows"][0]["resets_at"] = contract.iso_utc(NOW)
        self.assertEqual(contract.refresh_states(source, NOW)["state"], "stale")

    def test_unsupported_pi_grok_and_same_account_dedup_use_existing_schema(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = codex()
            contract.publish(original, root)
            pi = copy.deepcopy(original)
            pi.update(producer="pi-native", harness="pi")
            pi["source"]["kind"] = "pi-native"
            contract.publish(pi, root)
            contract.publish(claude(), root)
            contract.publish(contract.empty_source("grok-native", "grok", "xai",
                                                    "unsupported", "native_source_absent", now=NOW), root)
            result = contract.read_snapshot(root, now=NOW)
            self.assertEqual(len(result["pools"]), 1)
            self.assertEqual(result["pools"][0]["harnesses"], ["codex", "pi"])
            self.assertEqual(result["pools"][0]["windows"][0]["used_percentage"], 42)
            self.assertEqual(len(result["sources"]), 4)
            self.assertEqual(result["sources"][-1]["provider"], "openai")  # pi route is provider-owned
            self.assertEqual(next(s for s in result["sources"] if s["harness"] == "grok")["observations"], [])

    def test_disk_missing_corrupt_future_contract_and_false_available_are_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertTrue(all(s["state"] == "unknown" for s in contract.read_snapshot(root, now=NOW)["sources"]))
            folder = root / "quota-v1"
            folder.mkdir()
            path = folder / "codex-app-server.json"
            corrupt = [[], {"schema_version": 99}, "torn json"]
            for raw in corrupt:
                path.write_text(raw if isinstance(raw, str) else json.dumps(raw), encoding="utf-8")
                result = contract.read_snapshot(root, now=NOW)
                self.assertEqual(next(s for s in result["sources"] if s["harness"] == "codex")["state"], "error")
                self.assertEqual(result["pools"], [])
            contract.publish(codex(), root)
            with patch.object(Path, "read_text", side_effect=PermissionError("synthetic")):
                denied = contract.read_snapshot(root, now=NOW)
                self.assertEqual(denied["pools"], [])
                self.assertTrue(all(s["state"] in {"error", "unknown"} for s in denied["sources"]))
            for mutate in [
                lambda x: x.update(provider="anthropic"),
                lambda x: x["source"].update(adapter_version=99),
                lambda x: x.update(observations=[]),
                lambda x: x.update(state="unknown"),
                lambda x: x["observations"][0].update(pool_id="wrong"),
                lambda x: x["observations"][0]["windows"][0].update(used_percentage=None),
                lambda x: x.update(checked_at="not-a-time"),
            ]:
                raw = codex()
                mutate(raw)
                path.write_text(json.dumps(raw), encoding="utf-8")
                self.assertEqual(contract.read_snapshot(root, now=NOW)["pools"], [])

    def test_atomic_failed_replace_keeps_complete_old_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = contract.publish(codex(), root)
            before = target.read_bytes()
            with patch.object(contract.os, "replace", side_effect=PermissionError("synthetic")):
                with self.assertRaises(PermissionError):
                    contract.publish(codex(), root)
            self.assertEqual(target.read_bytes(), before)
            self.assertFalse(list(target.parent.glob("*.tmp.*")))
            self.assertFalse(before.startswith(b"\xef\xbb\xbf"))

    def test_concurrent_process_producers_and_continuous_reader_never_lose_a_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = (
                "import sys; from pathlib import Path; "
                f"sys.path.insert(0, {str(REPO / 'skills' / '_lib')!r}); "
                "from quota_snapshot import empty_source,publish; "
                "p=sys.argv[2]; "
                "[publish(empty_source(p, 'pi', 'openai', 'unknown', 'no_measurement'),Path(sys.argv[1])) for _ in range(20)]"
            )
            workers = [subprocess.Popen([sys.executable, "-c", script, str(root), f"synthetic-{i}"],
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
                       for i in range(4)]
            errors = []
            while any(p.poll() is None for p in workers):
                for path in (root / "quota-v1").glob("*.json"):
                    try:
                        contract.validate_source(json.loads(path.read_text(encoding="utf-8")))
                    except OSError:
                        # Native Windows replacement can deny an open briefly.
                        # Unreadable reads are explicit errors, tested separately.
                        pass
                    except ValueError as exc:
                        errors.append(type(exc).__name__ + ": " + str(exc))
            outcomes = [(worker, worker.communicate(timeout=5)) for worker in workers]
            for worker, (out, err) in outcomes:
                self.assertEqual(worker.returncode, 0, err.decode(errors="replace"))
            self.assertEqual(errors, [])
            self.assertEqual(len(list((root / "quota-v1").glob("*.json"))), 4)


class NativeTransportTests(unittest.TestCase):
    def test_protocol_timeout_eof_unsupported_and_error_are_distinct_redacted(self) -> None:
        process = type("FakeProcess", (), {"stdin": io.StringIO()})()
        cases = [(None, "native_exited"),
                 ({"id": 1, "error": {"code": -32601, "message": "PRIVATE"}}, "method_unsupported"),
                 ({"id": 1, "error": {"code": 500, "message": "PRIVATE"}}, "native_request_failed")]
        for message, expected in cases:
            messages = queue.Queue()
            messages.put(message)
            with self.assertRaisesRegex(sources.NativeReadError, expected):
                sources._request(process, messages, 1, "account/rateLimits/read", {})
        with patch.object(sources, "RPC_TIMEOUT_SECONDS", 0):
            with self.assertRaisesRegex(sources.NativeReadError, "native_timeout"):
                sources._request(process, queue.Queue(), 1, "account/rateLimits/read", {})

    def test_no_client_and_unverified_client_do_not_start_app_server(self) -> None:
        with patch.object(sources.shutil, "which", return_value=None):
            self.assertEqual(sources.collect_codex()["state"], "unknown")
        with patch.object(sources.shutil, "which", return_value="synthetic"), \
             patch.object(sources.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "codex-cli 0.999.0\n")), \
             patch.object(sources.subprocess, "Popen") as spawn:
            self.assertEqual(sources.collect_codex()["state"], "unsupported")
            spawn.assert_not_called()

    def test_statusline_real_powershell_preserves_legacy_and_publishes_same_values(self) -> None:
        if sys.platform != "win32":
            self.skipTest("Native PowerShell integration requires Windows")
        with tempfile.TemporaryDirectory() as directory:
            env = dict(os.environ, CLAUDE_HOOKS_STATE_DIR=directory)
            raw = {"rate_limits": {"five_hour": {"used_percentage": 12, "resets_at": RESET},
                                   "seven_day": {"used_percentage": 34, "resets_at": RESET}}}
            result = subprocess.run(["C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
                                     "-NoProfile", "-File", str(REPO / "statusline-command.ps1")],
                                    input=json.dumps(raw), capture_output=True, text=True,
                                    env=env, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("12%s", result.stdout)
            self.assertIn("34%w", result.stdout)
            legacy = json.loads((Path(directory) / "rate-limits.json").read_text(encoding="utf-8"))
            self.assertEqual(set(legacy), {"five_hour", "seven_day", "captured_at"})
            self.assertEqual(legacy["five_hour"], raw["rate_limits"]["five_hour"])
            self.assertEqual(legacy["seven_day"], raw["rate_limits"]["seven_day"])
            shared = json.loads((Path(directory) / "quota-v1" / "claude-statusline.json").read_text(encoding="utf-8"))
            for entry in shared["observations"][0]["windows"]:
                self.assertEqual(entry["used_percentage"], legacy[entry["id"]]["used_percentage"])
                self.assertEqual(contract.parse_time(entry["resets_at"]).timestamp(),
                                 legacy[entry["id"]]["resets_at"])
            # The existing reader needs no migration for this producer change.
            self.assertEqual(shared["provider"], "anthropic")


if __name__ == "__main__":
    unittest.main()
