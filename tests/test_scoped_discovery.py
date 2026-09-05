"""Disposable real-link coverage for scoped discovery and installer entry points."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "skills/_lib"))
import scoped_discovery as sd


class ScopedDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="fleet-discovery-test-")
        self.base = Path(self.temp.name)
        self.repo = self.base / "repo"
        self.repo.mkdir()
        sd.run_git(["-C", str(self.repo), "init", "-q"], check=True)
        self.home = self.base / "home"
        self.home.mkdir()

    def tearDown(self):
        # Test failures must never let rmtree walk a junction into a source.
        for folder, dirs, _ in os.walk(self.base, topdown=True, followlinks=False):
            for name in list(dirs):
                path = Path(folder) / name
                if sd.is_link(path):
                    sd._unlink(path)
                    dirs.remove(name)
        self.temp.cleanup()

    def skill(self, path, name="root-check"):
        path.mkdir(parents=True, exist_ok=True)
        (path / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: Harmless synthetic fixture.\n---\nReturn SENTINEL.\n",
            encoding="utf-8")
        return path

    def run_action(self, action="install", repo=None):
        return sd.reconcile(repo or self.repo, action, self.home)

    def test_scope_idempotence_and_real_parent_preservation(self):
        root = self.skill(self.repo / ".claude/skills/root-check")
        nested = self.skill(self.repo / "packages/api/.claude/skills/api-check", "api-check")
        sibling = self.repo / "packages/other"
        sibling.mkdir()
        keep = self.repo / ".agents/keep.txt"
        keep.parent.mkdir()
        keep.write_text("private unchanged")
        self.assertEqual(self.run_action("diagnose")["state"], "needs-install")
        self.assertFalse(sd.manifest_path(self.repo).exists())
        self.assertEqual(self.run_action()["state"], "ok")
        manifest = sd.manifest_path(self.repo).read_bytes()
        self.assertEqual(self.run_action()["state"], "ok")
        self.assertEqual(manifest, sd.manifest_path(self.repo).read_bytes())
        self.assertEqual((self.repo / ".agents/skills/root-check").resolve(), root.resolve())
        self.assertEqual((nested.parents[2] / ".agents/skills/api-check").resolve(), nested.resolve())
        self.assertFalse((self.repo / ".agents/skills/api-check").exists())
        self.assertFalse((sibling / ".agents").exists())
        self.assertEqual(self.run_action("uninstall")["state"], "ok")
        self.assertEqual(keep.read_text(), "private unchanged")
        self.assertTrue((root / "SKILL.md").exists())
        self.assertTrue((nested / "SKILL.md").exists())
        self.assertTrue(keep.parent.is_dir())

    def test_inverse_source_helpers_and_user_owned_links(self):
        source = self.skill(self.repo / ".agents/skills/inverse", "inverse")
        self.skill(self.repo / ".agents/skills/_private", "hidden")
        self.skill(self.repo / ".agents/skills/_lib", "helper")
        existing = self.repo / ".claude/skills/inverse"
        sd._link(source, existing)
        self.assertEqual(self.run_action()["state"], "ok")
        self.assertFalse(sd.manifest_path(self.repo).exists())
        self.run_action("uninstall")
        self.assertTrue(sd.is_link(existing))
        self.assertFalse((self.repo / ".claude/skills/_private").exists())

    def test_duplicate_name_different_folder_or_home_is_collision(self):
        for name in ("first", "second"):
            self.skill(self.repo / f".claude/skills/{name}")
        self.assertEqual(self.run_action()["state"], "blocked")
        self.assertFalse((self.repo / ".agents").exists())
        shutil.rmtree(self.repo / ".claude/skills/second")
        self.skill(self.home / ".agents/skills/global")
        self.assertEqual(self.run_action()["state"], "blocked")

    def test_ancestor_collision_but_sibling_names_do_not_overlap(self):
        self.skill(self.repo / "a/.claude/skills/check", "check")
        self.skill(self.repo / "b/.agents/skills/check", "check")
        self.assertEqual(self.run_action()["state"], "ok")
        self.skill(self.repo / ".claude/skills/root", "check")
        result = self.run_action()
        self.assertEqual(result["state"], "blocked")
        self.assertEqual(len([r for r in result["rows"] if r["state"] == "collision"]), 2)

    def test_occupied_real_destination_preserves_both(self):
        source = self.skill(self.repo / ".claude/skills/check")
        real = self.repo / ".agents/skills/check"
        real.mkdir(parents=True)
        (real / "private.txt").write_text("keep")
        self.assertEqual(self.run_action()["state"], "blocked")
        self.assertEqual((real / "private.txt").read_text(), "keep")
        self.assertTrue((source / "SKILL.md").is_file())

    def test_missing_source_and_changed_ownership_are_not_success(self):
        source = self.skill(self.repo / ".claude/skills/check")
        self.run_action()
        link = self.repo / ".agents/skills/check"
        sd._unlink(link)
        replacement = self.skill(self.repo / ".claude/skills/other", "other")
        sd._link(replacement, link)
        self.assertEqual(self.run_action("uninstall")["state"], "blocked")
        self.assertEqual(link.resolve(), replacement.resolve())
        sd._unlink(link)
        sd._link(source, link)
        shutil.rmtree(source)
        self.assertEqual(self.run_action("diagnose")["state"], "blocked")
        self.assertEqual(self.run_action()["state"], "ok")
        self.assertFalse(sd.present(link))
        self.assertNotIn(".agents/skills/check", sd.load_manifest(self.repo)["links"])

    def test_replaced_parent_and_foreign_manifest_are_preserved(self):
        self.skill(self.repo / ".claude/skills/check")
        self.run_action()
        owned = self.repo / ".agents/skills/check"
        sd._unlink(owned)
        owned.parent.rmdir()
        elsewhere = self.base / "elsewhere"
        elsewhere.mkdir()
        sd._link(elsewhere, owned.parent)
        self.assertEqual(self.run_action("uninstall")["state"], "blocked")
        self.assertTrue(sd.is_link(owned.parent))
        sd.manifest_path(self.repo).write_text('{"owner":"someone else","links":{}}')
        with self.assertRaisesRegex(ValueError, "owner"):
            self.run_action()

    def test_worktree_uses_own_source_and_refuses_primary_link(self):
        source = self.skill(self.repo / ".claude/skills/check")
        wt = self.base / "repo-wt-1"
        wt.mkdir()
        sd.run_git(["-C", str(wt), "init", "-q"], check=True)
        local = self.skill(wt / ".claude/skills/check")
        self.assertEqual(self.run_action(repo=wt)["state"], "ok")
        (local / "SKILL.md").write_text((local / "SKILL.md").read_text() + "WORKTREE")
        self.assertIn("WORKTREE", (wt / ".agents/skills/check/SKILL.md").read_text())
        self.assertNotIn("WORKTREE", (source / "SKILL.md").read_text())
        self.run_action("uninstall", wt)
        shutil.rmtree(local)
        sd._link(source, local)
        self.assertEqual(self.run_action(repo=wt)["state"], "blocked")

    def test_unknown_broken_link_and_instruction_only_package(self):
        nested = self.repo / "package"
        nested.mkdir()
        (nested / "CLAUDE.md").write_text("EOF")
        source = self.skill(self.repo / ".claude/skills/check")
        link = self.repo / ".agents/skills/broken"
        sd._link(source, link)
        shutil.rmtree(source)
        self.assertEqual(self.run_action("diagnose")["state"], "blocked")
        info = sd.instruction_report(self.repo, self.home)
        self.assertIn("unknown", info["state"])
        self.assertTrue(any(f["path"] == str(nested / "CLAUDE.md") for f in info["files"]))
        self.assertTrue(any(c["state"] == "missing-pointer-or-source" for c in info["chains"]))

    def test_manifest_cannot_claim_outside_paths_or_replace_corrupt_state(self):
        manifest = sd.manifest_path(self.repo)
        for key in ("../outside/.agents/skills/check", str(self.base / ".agents/skills/check")):
            manifest.write_text(json.dumps({"owner": sd.OWNER, "links": {key: str(self.repo)}}))
            with self.assertRaisesRegex(ValueError, "unsafe"):
                self.run_action("uninstall")
            self.assertTrue(manifest.exists())
        manifest.write_text("[]")
        with self.assertRaisesRegex(ValueError, "schema"):
            self.run_action()

    def test_shared_git_excludes_keep_other_worktree_and_user_lines(self):
        source = self.skill(self.repo / ".claude/skills/check")
        sd.run_git(["-C", str(self.repo), "add", ".claude"], check=True)
        sd.run_git(["-C", str(self.repo), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                    "-c", "core.hooksPath=", "commit", "-qm", "fixture"], check=True)
        wt = self.base / "actual-worktree"
        sd.run_git(["-C", str(self.repo), "worktree", "add", "--detach", str(wt)], check=True)
        exclude = self.repo / ".git/info/exclude"
        original = exclude.read_bytes() + b"\n# USER CONTENT\nprivate-example\n"
        exclude.write_bytes(original)
        self.run_action()
        self.assertEqual(self.run_action(repo=wt)["state"], "ok")
        self.assertNotEqual(sd.manifest_path(self.repo), sd.manifest_path(wt))
        self.assertEqual(exclude.read_text().count("BEGIN"), 2)
        sd.run_git(["-C", str(wt), "check-ignore", ".agents/skills/check"], check=True)
        self.run_action("uninstall", wt)
        self.assertEqual(exclude.read_text().count("BEGIN"), 1)
        self.assertTrue((source / "SKILL.md").exists())
        sd.run_git(["-C", str(self.repo), "check-ignore", ".agents/skills/check"], check=True)
        sd.run_git(["-C", str(self.repo), "worktree", "remove", "--force", str(wt)], check=True)
        self.run_action("uninstall")
        self.assertEqual(exclude.read_bytes(), original)

    def test_modified_owned_exclude_block_is_reported_and_preserved(self):
        self.skill(self.repo / ".claude/skills/check")
        self.run_action()
        exclude = self.repo / ".git/info/exclude"
        changed = exclude.read_bytes().replace(b"BEGIN", b"USER-CHANGED")
        exclude.write_bytes(changed)
        with self.assertRaisesRegex(ValueError, "block changed"):
            self.run_action()
        self.assertEqual(exclude.read_bytes(), changed)
        self.assertFalse(exclude.with_name(".fleet-discovery-exclude.lock").exists())

    def test_exclude_lock_conflict_keeps_created_link_owned_for_retry(self):
        self.skill(self.repo / ".claude/skills/check")
        lock = self.repo / ".git/info/.fleet-discovery-exclude.lock"
        lock.write_text("held by another checkout")
        with self.assertRaises(FileExistsError):
            self.run_action()
        self.assertIn(".agents/skills/check", sd.load_manifest(self.repo)["links"])
        self.assertEqual(lock.read_text(), "held by another checkout")
        lock.unlink()
        self.assertEqual(self.run_action()["state"], "ok")
        self.assertEqual(self.run_action("uninstall")["state"], "ok")

    @unittest.skipUnless(sys.platform == "win32", "PowerShell entry points are Windows-only")
    def test_global_uninstall_retains_repointed_link_and_ownership(self):
        original = self.repo / "original"
        replacement = self.repo / "replacement"
        original.mkdir()
        replacement.mkdir()
        target = self.home / ".claude/hooks"
        sd._link(replacement, target)
        manifest = self.home / "global-manifest.json"
        manifest.write_text(json.dumps({"hooks": {
            "kind": "junction", "source": str(original), "target": str(target)}}))
        # Execute the real manifest loop only; the unrelated OTel/plugin removers
        # are deliberately excluded from this isolated ownership fixture.
        source = (REPO / "uninstall.ps1").read_text(encoding="utf-8")
        loop = source[source.index("$manifest = Get-Content"):]
        script = self.base / "manifest-uninstall.ps1"
        script.write_text("$ManifestPath=$env:FLEET_TEST_MANIFEST\n$discoveryExit=0\n" + loop, encoding="utf-8")
        env = dict(os.environ, FLEET_TEST_MANIFEST=str(manifest))
        ps = str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe")
        result = subprocess.run([ps, "-NoProfile", "-File", str(script)], env=env,
                                capture_output=True, text=True, encoding="utf-8", creationflags=sd.NO_WINDOW)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(target.resolve(), replacement.resolve())
        self.assertIn("hooks", json.loads(manifest.read_text(encoding="utf-8-sig")))

    @unittest.skipUnless(sys.platform == "win32", "PowerShell entry points are Windows-only")
    def test_powershell_scoped_entry_points_never_touch_global_homes(self):
        source = self.skill(self.repo / ".claude/skills/check")
        ps = str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe")
        for script in ("install.ps1", "uninstall.ps1"):
            result = subprocess.run([ps, "-NoProfile", "-File", str(REPO / script), "-ProjectRoot", str(self.repo)],
                                    capture_output=True, text=True, encoding="utf-8", creationflags=sd.NO_WINDOW)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((source / "SKILL.md").exists())
        self.assertFalse(sd.present(self.repo / ".agents/skills/check"))


if __name__ == "__main__":
    unittest.main()
