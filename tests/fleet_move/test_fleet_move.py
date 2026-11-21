"""Tests for the fleet-root move helpers under scripts/fleet_move/ (fleet-config#2166).

Real temp trees, a real Windows junction and real hardlinks: the helpers exist
to get exactly those three things right, so mocks would prove nothing.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/fleet_move/test_fleet_move.py`
(also invoked by tests/run_acceptance.py)
"""
from __future__ import annotations

import os
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "fleet_move"))
import backup_paths
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


if __name__ == "__main__":
    unittest.main()
