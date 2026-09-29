"""Unit tests for skills/_lib/fleet_toml.py, the one `.fleet.toml` reader (fleet-config#1062).

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/test_fleet_toml.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "_lib"))
import fleet_toml  # noqa: E402
import worktree_config  # noqa: E402


def _repo(text: str | None) -> tuple[tempfile.TemporaryDirectory, Path]:
    holder = tempfile.TemporaryDirectory()
    repo = Path(holder.name)
    if text is not None:
        (repo / ".fleet.toml").write_text(text, encoding="utf-8")
    return holder, repo


class FleetTomlTests(unittest.TestCase):
    def test_absent_invalid_and_ok_are_distinct_states(self) -> None:
        for text, state in ((None, "absent"), ("[e2e\n", "invalid"), ('layer = "x"\n', "ok"), ("", "ok")):
            holder, repo = _repo(text)
            with holder:
                data, got = fleet_toml.load_state(repo)
                self.assertEqual(got, state)
                self.assertEqual(data is not None, state == "ok")
                self.assertEqual(fleet_toml.load(repo), data)

    def test_a_directory_named_fleet_toml_reads_as_absent(self) -> None:
        holder, repo = _repo(None)
        with holder:
            (repo / ".fleet.toml").mkdir()
            self.assertEqual(fleet_toml.load_state(repo), (None, "absent"))

    def test_table_returns_only_real_tables(self) -> None:
        data = fleet_toml.parse('[e2e]\ntest_budget = 3\n[cert]\n')
        self.assertEqual(fleet_toml.table(data, "e2e"), {"test_budget": 3})
        self.assertEqual(fleet_toml.table(fleet_toml.parse('e2e = "x"\n'), "e2e"), None)
        self.assertEqual(fleet_toml.table(None, "e2e"), None)

    def test_parse_of_empty_or_broken_text_is_none(self) -> None:
        self.assertIsNone(fleet_toml.parse(None))
        self.assertIsNone(fleet_toml.parse(""))
        self.assertIsNone(fleet_toml.parse("[e2e\n"))

    def test_positive_int_rejects_bool_string_zero_and_negative(self) -> None:
        self.assertEqual(fleet_toml.positive_int(7), 7)
        for bad in (True, False, "7", 0, -1, 1.5, None):
            self.assertIsNone(fleet_toml.positive_int(bad), repr(bad))

    def test_worktree_readers_share_the_degrade_contract(self) -> None:
        holder, repo = _repo("[worktree]\nprimary_instance_ok = true\n")
        with holder:
            self.assertTrue(worktree_config.worktree_primary_instance_ok(repo))
        for text in (None, "[worktree\n", "[worktree]\nprimary_instance_ok = \"true\"\n"):
            holder, repo = _repo(text)
            with holder:
                self.assertFalse(worktree_config.worktree_primary_instance_ok(repo), repr(text))


if __name__ == "__main__":
    unittest.main()
