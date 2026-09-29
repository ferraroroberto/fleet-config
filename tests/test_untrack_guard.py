"""untrack_guard.py: a fast-forward keeps every live file the merge untracks (fleet-config#1086).

The regression fixture reproduces the automation#144 loss in miniature: a
primary checkout holds tracked machine-local configs, and upstream merges a
commit that untracks them (`git rm --cached` + `.gitignore`, and a rename to
`.sample`). Before the fix, `land-primary`'s plain `pull --ff-only` deleted the
live copies; now they survive and are reported.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "skills" / "_lib"))
import untrack_guard as ug  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check

LIVE = '{"root": "E:/machine/local/path"}'


# ---- parse_removed_paths: pure ---------------------------------------------

check(ug.parse_removed_paths("") == [], "parse_removed_paths: empty diff -> nothing removed")
check(ug.parse_removed_paths("D\0a/x.json\0R100\0b.json\0b.json.sample\0D\0c d.json\0")
      == ["a/x.json", "b.json", "c d.json"],
      "parse_removed_paths: D rows and the OLD side of R rows, spaces intact")
check(ug.parse_removed_paths("M\0kept.txt\0R087\0old.txt\0new.txt\0") == ["old.txt"],
      "parse_removed_paths: a non-D/R row is skipped without desynchronising the stream")


# ---- fixtures -----------------------------------------------------------------

base = Path(tempfile.mkdtemp(prefix="untrack-guard-"))
_no_hooks = base / "nohooks"
_no_hooks.mkdir()


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    # `core.hooksPath` -> an empty dir: this machine's global commit hook
    # rejects non-allowlisted author emails.
    res = subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                          "-c", f"core.hooksPath={_no_hooks}", *args],
                         cwd=str(cwd), capture_output=True, text=True)
    if res.returncode != 0 and args[0] in ("init", "clone", "commit", "add", "rm", "mv"):
        raise AssertionError(f"fixture git {args[0]} failed: {res.stderr.strip()}")
    return res


def _upstream(name: str) -> Path:
    """Upstream with three tracked files a later commit will untrack or delete."""
    up = base / f"{name}-upstream"
    (up / "config").mkdir(parents=True)
    _git(up, "init", "-b", "main")
    (up / "config" / "app.json").write_text(LIVE, encoding="utf-8")
    (up / "config" / "b.json").write_text('{"b": 1}', encoding="utf-8")
    (up / "old.txt").write_text("retired", encoding="utf-8")
    _git(up, "add", "-A")
    _git(up, "commit", "-m", "track the configs")
    return up


def _untrack(up: Path) -> None:
    """The automation#144 shape: untrack + ignore, rename to .sample, plus a plain delete."""
    _git(up, "rm", "--cached", "-q", "config/app.json")
    _git(up, "mv", "config/b.json", "config/b.json.sample")
    _git(up, "rm", "-q", "old.txt")
    (up / ".gitignore").write_text("config/*.json\n", encoding="utf-8")
    _git(up, "add", ".gitignore")
    _git(up, "commit", "-m", "untrack machine-local configs")


def _clone(up: Path, name: str) -> Path:
    clone = base / name
    _git(base, "clone", "-q", str(up), str(clone))
    return clone


try:
    # ---- the regression: land-primary, end to end --------------------------
    up = _upstream("myrepo")
    primary = _clone(up, "myrepo")
    _untrack(up)

    # Relocate the library so `myrepo` is a declared, tray-less project (the
    # same isolation tests/test_worktree_claim.py uses for land-primary).
    lib_dst = base / "skills" / "_lib"
    lib_dst.mkdir(parents=True)
    for mod in (ROOT / "skills" / "_lib").glob("*.py"):
        shutil.copy2(mod, lib_dst / mod.name)
    (base / "hooks").mkdir()
    (base / "hooks" / "projects.toml").write_text(
        f'[myrepo]\ncwd_prefix = "{primary.as_posix()}"\n', encoding="utf-8")

    res = subprocess.run([sys.executable, str(lib_dst / "worktree_claim.py"),
                          "land-primary", str(primary), "1086"],
                         capture_output=True, text=True)
    lines = res.stdout.strip().splitlines()
    check(res.returncode == 0 and "PRIMARY=live behind=0" in lines,
          f"land-primary: the untracking merge lands (got rc={res.returncode} {res.stdout.strip()!r} {res.stderr.strip()[-200:]!r})")
    app = primary / "config" / "app.json"
    check(app.is_file() and app.read_text(encoding="utf-8") == LIVE,
          "land-primary: a live config the merge untracked survives the fast-forward, content intact")
    check((primary / "config" / "b.json").is_file() and (primary / "config" / "b.json.sample").is_file(),
          "land-primary: a config renamed to .sample keeps its live copy beside the new sample")
    check(not (primary / "old.txt").exists(),
          "land-primary: a tracked file the merge deletes and does not ignore stays deleted")
    check("RESTORED_UNTRACKED=config/app.json,config/b.json" in lines,
          f"land-primary: every restored path is reported, never silently (got {lines!r})")
    check(_git(primary, "status", "--porcelain").stdout.strip() == "",
          "land-primary: the restored files are ignored, so the primary stays clean")

    # ---- the CLI, against the branch's upstream ------------------------------
    up2 = _upstream("clirepo")
    cli_clone = _clone(up2, "clirepo")
    _untrack(up2)
    res = subprocess.run([sys.executable, str(ROOT / "skills" / "_lib" / "untrack_guard.py"),
                          "fast-forward", str(cli_clone)], capture_output=True, text=True)
    out = res.stdout
    check(res.returncode == 0 and "FF=done" in out
          and "RESTORED_UNTRACKED=config/app.json,config/b.json" in out,
          f"CLI fast-forward: fetches, lands @{{u}} and reports the restores (got {out.strip()!r})")
    check((cli_clone / "config" / "app.json").read_text(encoding="utf-8") == LIVE,
          "CLI fast-forward: the live config is back with its content")

    res = subprocess.run([sys.executable, str(ROOT / "skills" / "_lib" / "untrack_guard.py"),
                          "fast-forward", str(cli_clone)], capture_output=True, text=True)
    check(res.returncode == 0 and "RESTORED_UNTRACKED=none" in res.stdout,
          "CLI fast-forward: an already-current tree restores nothing and says none")

    # ---- refusals leave the tree untouched ---------------------------------
    up3 = _upstream("refrepo")
    ref_clone = _clone(up3, "refrepo")
    ff = ug.guarded_fast_forward(ref_clone, "origin/no-such-branch")
    check(not ff.ok and "cannot resolve" in ff.detail and (ref_clone / "config" / "app.json").is_file(),
          "guarded_fast_forward: an unresolvable target refuses and touches nothing")
    check(ug.report_lines(ff) == [], "report_lines: nothing restored -> nothing to report")

    # A diverged tree: the fast-forward refuses and the stash is not leaked.
    _untrack(up3)
    (ref_clone / "local.txt").write_text("x", encoding="utf-8")
    _git(ref_clone, "add", "local.txt")
    _git(ref_clone, "commit", "-m", "diverge")
    _git(ref_clone, "fetch", "-q")
    ff = ug.guarded_fast_forward(ref_clone, "origin/main")
    check(not ff.ok and "merge --ff-only failed" in ff.detail and ff.stash is None,
          "guarded_fast_forward: a non-fast-forward refuses, no stash left behind")
    check((ref_clone / "config" / "app.json").read_text(encoding="utf-8") == LIVE,
          "guarded_fast_forward: a refused fast-forward leaves the live config where it was")

    # Unknown ignore state: neither restored nor lost -- kept aside, reported.
    up4 = _upstream("unkrepo")
    unk_clone = _clone(up4, "unkrepo")
    _untrack(up4)
    _git(unk_clone, "fetch", "-q")
    real_is_ignored = ug.is_ignored
    ug.is_ignored = lambda repo, rel: None
    try:
        ff = ug.guarded_fast_forward(unk_clone, "origin/main")
    finally:
        ug.is_ignored = real_is_ignored
    check(ff.ok and ff.restored == [] and ff.kept_aside == ["config/app.json", "config/b.json", "old.txt"]
          and ff.stash is not None
          and (ff.stash / "config" / "app.json").read_text(encoding="utf-8") == LIVE,
          "guarded_fast_forward: an unknown ignore state keeps the file in the stash, not restored, not lost")
    check(any(l.startswith("KEPT_ASIDE=config/app.json stash=") for l in ug.report_lines(ff)),
          "report_lines: a kept-aside file is reported with its stash directory")
    if ff.stash:
        shutil.rmtree(ff.stash, ignore_errors=True)
finally:
    shutil.rmtree(base, ignore_errors=True)


_h.report_and_exit("test_untrack_guard")
