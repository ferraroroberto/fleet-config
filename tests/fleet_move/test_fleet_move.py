"""Tests for the fleet-root move helpers under scripts/fleet_move/ (fleet-config#2166).

Real temp trees, a real Windows junction and real hardlinks: the helpers exist
to get exactly those three things right, so mocks would prove nothing.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/fleet_move/test_fleet_move.py`
(also invoked by tests/run_acceptance.py)
"""
from __future__ import annotations

import contextlib
import io
import os
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "fleet_move"))
import backup_paths
import copy_claude_history
import copy_linked
import measure_tree

ON_WINDOWS = sys.platform == "win32"


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _junction(target: Path, link: Path) -> None:
    import _winapi

    _winapi.CreateJunction(str(target), str(link))


class MeasureTreeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "fleet"
        _write(self.root / "repo" / "a.txt", "aaaa")
        _write(self.root / "repo" / ".venv" / "lib" / "big.bin", "x" * 100)
        _write(self.root / "repo" / ".git" / "index.lock", "")
        _write(self.root / "top.txt", "t")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_counts_files_bytes_and_folders(self) -> None:
        report = measure_tree.measure(self.root)
        self.assertEqual(report.files, 4)
        self.assertEqual(report.bytes, 105)
        self.assertEqual(report.folder_bytes["repo"], 104)
        self.assertEqual(report.folder_bytes["repo/.venv"], 100)
        self.assertEqual(report.index_locks, ["repo/.git/index.lock"])

    @unittest.skipUnless(ON_WINDOWS, "junctions are Windows-only")
    def test_records_a_junction_without_entering_it(self) -> None:
        (self.root / "repo-wt-1").mkdir()
        _junction(self.root / "repo" / ".venv", self.root / "repo-wt-1" / ".venv")
        report = measure_tree.measure(self.root)
        self.assertEqual(report.files, 4, "the venv behind the junction is not counted twice")
        self.assertIn("repo-wt-1/.venv", report.reparse_points)
        self.assertTrue(report.reparse_points["repo-wt-1/.venv"].endswith(str(self.root / "repo" / ".venv")))

    def test_compare_flags_a_missing_file_and_matches_a_faithful_copy(self) -> None:
        other = Path(self._tmp.name) / "copy"
        for path in self.root.rglob("*"):
            if path.is_file():
                _write(other / path.relative_to(self.root), path.read_text(encoding="utf-8"))
        self.assertTrue(measure_tree.compare(measure_tree.measure(self.root), measure_tree.measure(other))["match"])
        (other / "top.txt").unlink()
        result = measure_tree.compare(measure_tree.measure(self.root), measure_tree.measure(other))
        self.assertFalse(result["match"])
        self.assertIn("files: 4 vs 3", result["differences"])


class CopyLinkedTests(unittest.TestCase):
    def test_keeps_hardlinks_between_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "store"
            first = _write(src / "2026-11-19" / "repo" / "notes.md", "same")
            (src / "2026-11-20" / "repo").mkdir(parents=True)
            os.link(first, src / "2026-11-20" / "repo" / "notes.md")
            _write(src / "2026-11-20" / "repo" / "new.md", "new")
            dest = Path(tmp) / "moved"
            dest.mkdir()
            result = copy_linked.copy_linked(src, dest, dry_run=False)
            self.assertEqual((result["copied"], result["linked"], result["errors"]), (2, 1, []))
            a = os.stat(dest / "2026-11-19" / "repo" / "notes.md")
            b = os.stat(dest / "2026-11-20" / "repo" / "notes.md")
            self.assertEqual(a.st_ino, b.st_ino, "the copy shares one inode, like the source")
            self.assertEqual((dest / "2026-11-20" / "repo" / "new.md").read_text(encoding="utf-8"), "new")

    def test_dry_run_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "store"
            _write(src / "d" / "f.md", "x")
            dest = Path(tmp) / "moved"
            result = copy_linked.copy_linked(src, dest, dry_run=True)
            self.assertEqual(result["copied"], 1)
            self.assertFalse(dest.exists())


MOVED_BACKUP = {"source_root": "C:/automation", "dest": "E:/backup/fleet-private"}


class BackupPathsTests(unittest.TestCase):

    def test_replaces_existing_keys_and_keeps_everything_else(self) -> None:
        text = (
            "[repo]\ncwd_prefix = \"E:/automation/repo\"\ndest = \"not-this-one\"\n\n"
            "[backup]\nsource_root = \"E:/automation\"\nkeep_daily = 14\ndest = \"C:/old\"\n\n"
            "[global]\nx = 1\n"
        )
        out = backup_paths.set_backup_paths(text, MOVED_BACKUP)
        data = tomllib.loads(out)
        self.assertEqual(data["backup"], {"source_root": "C:/automation", "keep_daily": 14,
                                          "dest": "E:/backup/fleet-private"})
        self.assertEqual(data["repo"]["dest"], "not-this-one", "a same-named key in another table is untouched")
        self.assertEqual(data["global"], {"x": 1})

    def test_adds_missing_keys_and_a_missing_table(self) -> None:
        out = backup_paths.set_backup_paths("[backup]\nkeep_daily = 7\n", MOVED_BACKUP)
        self.assertEqual(tomllib.loads(out)["backup"]["dest"], "E:/backup/fleet-private")
        out = backup_paths.set_backup_paths("[global]\nx = 1", MOVED_BACKUP)
        self.assertEqual(tomllib.loads(out)["backup"], MOVED_BACKUP)

    def test_keeps_crlf_and_writes_forward_slashes(self) -> None:
        out = backup_paths.set_backup_paths("[backup]\r\ndest = \"C:/old\"\r\n",
                                            {"source_root": "C:\\automation", "dest": "E:\\b"})
        self.assertEqual(out.count("\n"), out.count("\r\n"), "every added line ends CRLF like the file")
        self.assertEqual(tomllib.loads(out)["backup"], {"source_root": "C:/automation", "dest": "E:/b"})

    def test_cli_keeps_the_original_and_refuses_a_second_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(Path(tmp) / "projects.toml", "[backup]\ndest = \"C:/old\"\n")
            args = [str(path), "--source-root", "C:/automation", "--dest", "E:/backup/fleet-private"]
            self.assertEqual(backup_paths.main(args), 0)
            self.assertEqual(tomllib.loads((Path(tmp) / "projects.toml.pre-move").read_text(encoding="utf-8")),
                             {"backup": {"dest": "C:/old"}})
            self.assertEqual(backup_paths.main(args), 2, "never overwrites the only record of the old values")


class CopyClaudeHistoryTests(unittest.TestCase):
    """fleet-config#2181: E-- project folders copied into their C-- twins, never moved or overwritten."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.projects = Path(self._tmp.name) / "projects"
        self.sessions = Path(self._tmp.name) / "sessions"
        self.projects.mkdir()
        self.sessions.mkdir()

    def _run(self, *extra: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        argv = ["--projects-dir", str(self.projects), "--sessions-dir", str(self.sessions), *extra]
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = copy_claude_history.main(argv)
        return code, out.getvalue(), err.getvalue()

    def _snapshot(self) -> dict[str, bytes]:
        return {str(p.relative_to(self.projects)): p.read_bytes() for p in self.projects.rglob("*") if p.is_file()}

    def test_twin_name_maps_only_the_automation_folders(self) -> None:
        self.assertEqual(copy_claude_history.twin_name("E--automation"), "C--automation")
        self.assertEqual(copy_claude_history.twin_name("E--automation-fleet-config-wt-9"),
                         "C--automation-fleet-config-wt-9")
        self.assertIsNone(copy_claude_history.twin_name("E--automationx"))
        self.assertIsNone(copy_claude_history.twin_name("C--Users-x-E--automation-y"))

    def test_dry_run_lists_and_touches_nothing(self) -> None:
        _write(self.projects / "E--automation-app" / "a.jsonl", "one")
        _write(self.projects / "E--automation-app" / "memory" / "m.md", "mem")
        before = self._snapshot()
        code, out, _ = self._run()
        self.assertEqual(code, 0)
        self.assertIn("E--automation-app -> C--automation-app (new folder): would copy 2", out)
        self.assertIn("DRY RUN (nothing written)", out)
        self.assertEqual(self._snapshot(), before)
        self.assertFalse((self.projects / "C--automation-app").exists())

    def test_apply_copies_and_skips_existing_without_overwriting(self) -> None:
        _write(self.projects / "E--automation-app" / "new.jsonl", "from-E-new")
        _write(self.projects / "E--automation-app" / "same.jsonl", "from-E")
        _write(self.projects / "E--automation-app" / "dir-1" / "sub" / "t.txt", "nested")
        _write(self.projects / "C--automation-app" / "same.jsonl", "from-C")
        code, out, _ = self._run("--apply")
        self.assertEqual(code, 0)
        self.assertIn("copied 2, skipped 1", out)
        c = self.projects / "C--automation-app"
        self.assertEqual((c / "new.jsonl").read_text(encoding="utf-8"), "from-E-new")
        self.assertEqual((c / "dir-1" / "sub" / "t.txt").read_text(encoding="utf-8"), "nested")
        self.assertEqual((c / "same.jsonl").read_text(encoding="utf-8"), "from-C", "an existing file is never replaced")
        e = self.projects / "E--automation-app"
        self.assertEqual((e / "same.jsonl").read_text(encoding="utf-8"), "from-E", "the source stays untouched")
        self.assertTrue((e / "new.jsonl").exists(), "copied, not moved")
        self.assertFalse(list(self.projects.rglob("*.copy-tmp")))
        after_first = self._snapshot()
        code, out, _ = self._run("--apply")
        self.assertEqual(code, 0)
        self.assertIn("copied 0, skipped 3", out)
        self.assertEqual(self._snapshot(), after_first)

    def test_memory_clash_keeps_the_newer_file_and_lists_it(self) -> None:
        e_old = _write(self.projects / "E--automation-a" / "memory" / "old.md", "E-older")
        e_new = _write(self.projects / "E--automation-a" / "memory" / "new.md", "E-newer")
        c_old = _write(self.projects / "C--automation-a" / "memory" / "old.md", "C-newer")
        c_new = _write(self.projects / "C--automation-a" / "memory" / "new.md", "C-older")
        _write(self.projects / "E--automation-a" / "memory" / "equal.md", "same")
        _write(self.projects / "C--automation-a" / "memory" / "equal.md", "same")
        os.utime(e_old, (1_000_000, 1_000_000))
        os.utime(c_old, (2_000_000, 2_000_000))
        os.utime(e_new, (2_000_000, 2_000_000))
        os.utime(c_new, (1_000_000, 1_000_000))
        code, out, _ = self._run("--apply")
        self.assertEqual(code, 0)
        self.assertEqual(c_old.read_text(encoding="utf-8"), "C-newer", "newer target kept")
        self.assertEqual(c_new.read_text(encoding="utf-8"), "E-newer", "newer source replaces the older target")
        self.assertIn("clash: memory/old.md (kept target, the newer)", out)
        self.assertIn("clash: memory/new.md (kept source, the newer)", out)
        self.assertNotIn("equal.md", out, "identical bytes are a skip, not a clash")
        self.assertIn("clashes 2", out)

    def test_dry_run_reports_a_clash_without_replacing_it(self) -> None:
        e = _write(self.projects / "E--automation-a" / "memory" / "m.md", "E")
        c = _write(self.projects / "C--automation-a" / "memory" / "m.md", "C")
        os.utime(e, (2_000_000, 2_000_000))
        os.utime(c, (1_000_000, 1_000_000))
        code, out, _ = self._run()
        self.assertEqual(code, 0)
        self.assertIn("clash: memory/m.md (kept source, the newer)", out)
        self.assertEqual(c.read_text(encoding="utf-8"), "C")

    def test_memory_index_merges_lines_instead_of_overwriting(self) -> None:
        _write(self.projects / "E--automation-a" / "memory" / "MEMORY.md", "- [One](one.md) - x\n- [Two](two.md) - y\n")
        target = _write(self.projects / "C--automation-a" / "memory" / "MEMORY.md",
                        "- [Two](two.md) - y\n- [Three](three.md) - z")
        code, out, _ = self._run("--apply")
        self.assertEqual(code, 0)
        text = target.read_text(encoding="utf-8")
        self.assertEqual(text, "- [Two](two.md) - y\n- [Three](three.md) - z\n- [One](one.md) - x\n")
        self.assertIn("MEMORY.md lines 1", out)
        self.assertEqual(self._run("--apply")[0], 0)
        self.assertEqual(target.read_text(encoding="utf-8"), text, "a second run adds nothing")

    def test_merge_index_helper(self) -> None:
        self.assertEqual(copy_claude_history.merge_index("a\nb\n", "b\n"), ("b\na\n", 1))
        self.assertEqual(copy_claude_history.merge_index("a\n", "a\n"), ("a\n", 0))
        self.assertEqual(copy_claude_history.merge_index("", ""), ("", 0))

    def test_apply_refuses_while_a_session_is_open_and_dry_run_only_notes_it(self) -> None:
        _write(self.projects / "E--automation-a" / "a.jsonl", "x")
        _write(self.sessions / f"{os.getpid()}.json", f'{{"pid": {os.getpid()}}}')
        code, _, err = self._run("--apply")
        self.assertEqual(code, 3)
        self.assertIn("1 Claude Code session(s) open", err)
        self.assertFalse((self.projects / "C--automation-a").exists())
        code, out, _ = self._run()
        self.assertEqual(code, 0)
        self.assertIn("--apply would refuse", out)

    def test_a_stale_session_file_does_not_block_apply(self) -> None:
        dead_pid = 999_999_999  # no real pid is this large or odd on Windows
        _write(self.projects / "E--automation-a" / "a.jsonl", "x")
        _write(self.sessions / f"{dead_pid}.json", f'{{"pid": {dead_pid}}}')
        self.assertEqual(self._run("--apply")[0], 0)
        self.assertTrue((self.projects / "C--automation-a" / "a.jsonl").exists())

    def test_apply_refuses_when_the_session_registry_is_missing(self) -> None:
        _write(self.projects / "E--automation-a" / "a.jsonl", "x")
        self.sessions.rmdir()
        code, _, err = self._run("--apply")
        self.assertEqual(code, 3)
        self.assertIn("could not be checked", err)
        self.assertEqual(self._run()[0], 0)

    @unittest.skipUnless(ON_WINDOWS, "junctions are Windows-only")
    def test_a_junction_inside_a_folder_is_reported_and_not_followed(self) -> None:
        outside = _write(Path(self._tmp.name) / "outside" / "secret.txt", "x").parent
        _write(self.projects / "E--automation-a" / "a.jsonl", "x")
        _junction(outside, self.projects / "E--automation-a" / "linked")
        code, out, _ = self._run("--apply")
        self.assertEqual(code, 0)
        self.assertIn("link not followed: linked", out)
        self.assertFalse((self.projects / "C--automation-a" / "linked").exists())
        self.assertTrue((outside / "secret.txt").exists())


if __name__ == "__main__":
    unittest.main()
