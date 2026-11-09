"""runtime_files: the guarded writers refuse a path under a code folder (fleet-config#1949).

The runtime-write ratchet cannot resolve a write whose path is a parameter or a CLI
argument, so those writes go through one helper per import world (``hooks/runtime_files``
and ``skills/_lib/fleet_core/runtime_files.py``) that raises ``ValueError`` when the
resolved path sits under a code folder. Every entry point is driven here against a
sandbox standing in for the checkout (``REPO_ROOT`` patched to a temp tree), so a guard
that stopped refusing would write into the temp tree, never into this repo.
"""
from __future__ import annotations

import ast
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "skills" / "_lib"))
sys.path.insert(0, str(ROOT / "hooks"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_lib"))
import runtime_files as hooks_tier  # noqa: E402
from check_harness import CheckHarness  # noqa: E402
from fleet_core import runtime_files as skills_tier  # noqa: E402

_h = CheckHarness()
check = _h.check

SHARED = ("checked", "make_dirs", "touch_file", "save_text", "save_bytes", "open_for_write",
          "copy_with_metadata", "connect_sqlite", "connect_read_only")
SKILLS_ONLY = ("copy_file", "copy_tree", "move", "file_handler")


def _refused(call) -> bool:  # type: ignore[no-untyped-def]
    try:
        call()
    except ValueError:
        return True
    return False


def _link(link: Path, target: Path) -> bool:
    """A directory link at ``link`` -> ``target``; False when this account cannot make one."""
    try:
        os.symlink(target, link, target_is_directory=True)
        return True
    except OSError:
        pass
    if sys.platform != "win32":
        return False
    proc = subprocess.run(["cmd.exe", "/c", "mklink", "/J", str(link), str(target)],
                          capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
    return proc.returncode == 0


base = Path(tempfile.mkdtemp(prefix="runtime-files-"))
try:
    for tier_name, mod in (("skills tier", skills_tier), ("hooks tier", hooks_tier)):
        repo = base / tier_name.split()[0] / "repo"
        for folder in ("hooks", "skills", "scripts", ".claude", "hooks/state", "docs"):
            (repo / folder).mkdir(parents=True)
        outside = base / tier_name.split()[0] / "outside"
        outside.mkdir()
        (repo / "hooks" / "seed.txt").write_text("seed", encoding="utf-8")
        (repo / "hooks" / "seed.db").write_bytes(b"")
        (outside / "seed.txt").write_text("seed", encoding="utf-8")

        with mock.patch.object(mod, "REPO_ROOT", repo.resolve()):
            bad = repo / "hooks" / "new"
            bad_file = repo / "scripts" / "out.txt"
            tag = f"{tier_name}: "

            # checked: the one decision every entry point shares
            check(_refused(lambda: mod.checked(bad)), tag + "checked refuses a path under hooks/")
            for folder in ("hooks", "skills", "scripts", ".claude"):
                check(_refused(lambda f=folder: mod.checked(repo / f / "x" / "y.txt")),
                      tag + f"checked refuses a path under {folder}/")
            check(_refused(lambda: mod.checked(repo / "hooks")), tag + "checked refuses the code folder itself")
            check(_refused(lambda: mod.checked(repo / "hooks" / ".." / "hooks" / "x")),
                  tag + "checked refuses a path that reaches a code folder through ..")
            check(mod.checked(repo / "docs" / "x.md") == (repo / "docs" / "x.md").resolve(),
                  tag + "checked passes a path in the checkout outside every code folder")
            check(mod.checked(outside / "x") == (outside / "x").resolve(), tag + "checked passes a path outside the checkout")
            check(mod.checked(repo / "hooks" / "state" / "x.json") == (repo / "hooks" / "state" / "x.json").resolve(),
                  tag + "checked passes the gitignored hooks/state live-state folder")
            check(_refused(lambda: mod.checked(repo / "hooks" / "statefoo" / "x")),
                  tag + "the hooks/state carve-out does not cover a sibling that shares the prefix")
            check(_refused(lambda: mod.checked(repo / "hooks" / "state" / ".." / "x")),
                  tag + "the carve-out is judged on the resolved path, not the spelling")
            old_cwd = Path.cwd()
            os.chdir(repo / "scripts")
            try:
                check(_refused(lambda: mod.checked("rel.txt")), tag + "checked resolves a relative path against the cwd")
            finally:
                os.chdir(old_cwd)
            if sys.platform == "win32":
                check(_refused(lambda: mod.checked(repo / "HOOKS" / "x")),
                      tag + "checked compares case-insensitively on Windows")

            # a link from outside into a code folder is followed, not trusted
            link = outside / "via-link"
            if _link(link, repo / "hooks"):
                check(_refused(lambda: mod.checked(link / "x.txt")), tag + "checked follows a link into a code folder")
                check(_refused(lambda: mod.save_text(link / "x.txt", "no")) and not (repo / "hooks" / "x.txt").exists(),
                      tag + "save_text through a link into a code folder writes nothing")
            else:
                _h.skip(tag + "no directory link could be created on this account; the link-following check did not run")

            # make_dirs
            check(_refused(lambda: mod.make_dirs(bad, parents=True)) and not bad.exists(),
                  tag + "make_dirs refuses and creates nothing")
            made = mod.make_dirs(outside / "a" / "b", parents=True, exist_ok=True)
            check(made.is_dir() and made == (outside / "a" / "b").resolve(), tag + "make_dirs creates a folder outside")
            try:
                mod.make_dirs(outside / "missing" / "leaf")
                check(False, tag + "make_dirs without parents must raise FileNotFoundError for a missing parent")
            except FileNotFoundError:
                pass
            try:
                mod.make_dirs(outside / "a")
                check(False, tag + "make_dirs without exist_ok must raise FileExistsError on an existing folder")
            except FileExistsError:
                pass

            # touch_file
            check(_refused(lambda: mod.touch_file(bad_file)) and not bad_file.exists(), tag + "touch_file refuses and creates nothing")
            mod.touch_file(outside / "t.flag")
            check((outside / "t.flag").is_file(), tag + "touch_file creates a file outside")

            # save_text
            check(_refused(lambda: mod.save_text(bad_file, "x", encoding="utf-8")) and not bad_file.exists(),
                  tag + "save_text refuses and writes nothing")
            n = mod.save_text(outside / "t.txt", "héllo\n", encoding="utf-8", newline="")
            check((outside / "t.txt").read_bytes() == "héllo\n".encode("utf-8") and n == 6,
                  tag + "save_text writes the text and returns the character count")

            # save_bytes
            check(_refused(lambda: mod.save_bytes(bad_file, b"x")) and not bad_file.exists(),
                  tag + "save_bytes refuses and writes nothing")
            check(mod.save_bytes(outside / "b.bin", b"\x00\x01") == 2 and (outside / "b.bin").read_bytes() == b"\x00\x01",
                  tag + "save_bytes writes the bytes and returns the count")

            # open_for_write
            for mode in ("w", "a", "x", "wb", "ab"):
                check(_refused(lambda m=mode: mod.open_for_write(repo / "scripts" / f"o-{m}", m)) and not list((repo / "scripts").glob("o-*")),
                      tag + f"open_for_write refuses mode {mode!r} and opens nothing")
            with mod.open_for_write(outside / "o.txt", "w", encoding="utf-8", newline="\n") as fh:
                fh.write("line\n")
            with mod.open_for_write(outside / "o.txt", "a", encoding="utf-8") as fh:
                fh.write("more\n")
            check((outside / "o.txt").read_text(encoding="utf-8") == "line\nmore\n", tag + "open_for_write opens, appends and passes keywords through")

            # copy_with_metadata
            check(_refused(lambda: mod.copy_with_metadata(outside / "seed.txt", repo / "scripts" / "c.txt"))
                  and not (repo / "scripts" / "c.txt").exists(), tag + "copy_with_metadata refuses a destination under a code folder")
            dst = mod.copy_with_metadata(outside / "seed.txt", outside / "copy.txt")
            check(Path(dst).read_text(encoding="utf-8") == "seed", tag + "copy_with_metadata copies outside")
            check(not _refused(lambda: mod.copy_with_metadata(repo / "hooks" / "seed.txt", outside / "from-code.txt")),
                  tag + "copy_with_metadata may read from a code folder (only the destination is guarded)")

            # connect_sqlite
            check(_refused(lambda: mod.connect_sqlite(repo / "hooks" / "n.db", timeout=1)) and not (repo / "hooks" / "n.db").exists(),
                  tag + "connect_sqlite refuses and creates nothing")
            conn = mod.connect_sqlite(str(outside / "n.db"), timeout=1)
            conn.execute("create table t (a)")
            conn.commit()
            conn.close()
            check((outside / "n.db").is_file(), tag + "connect_sqlite opens a database outside")

            # connect_read_only
            check(_refused(lambda: mod.connect_read_only((outside / "n.db").as_uri())), tag + "connect_read_only refuses a URI that is not mode=ro")
            ro = mod.connect_read_only(f"{(outside / 'n.db').as_uri()}?mode=ro", timeout=1)
            try:
                ro.execute("insert into t values (1)")
                check(False, tag + "connect_read_only must not accept a write")
            except sqlite3.OperationalError:
                pass
            finally:
                ro.close()

    # the skills tier carries the writers only skills use
    with mock.patch.object(skills_tier, "REPO_ROOT", (base / "skills" / "repo").resolve()):
        repo = base / "skills" / "repo"
        outside = base / "skills" / "outside"
        check(_refused(lambda: skills_tier.copy_file(outside / "seed.txt", repo / "scripts" / "f.txt")) and not (repo / "scripts" / "f.txt").exists(),
              "skills tier: copy_file refuses a destination under a code folder")
        skills_tier.copy_file(outside / "seed.txt", outside / "f.txt")
        check((outside / "f.txt").read_text(encoding="utf-8") == "seed", "skills tier: copy_file copies outside")

        src_tree = outside / "tree"
        (src_tree / "sub").mkdir(parents=True)
        (src_tree / "sub" / "a.txt").write_text("a", encoding="utf-8")
        check(_refused(lambda: skills_tier.copy_tree(src_tree, repo / "scripts" / "tree")) and not (repo / "scripts" / "tree").exists(),
              "skills tier: copy_tree refuses a destination under a code folder")
        skills_tier.copy_tree(src_tree, outside / "tree-copy")
        check((outside / "tree-copy" / "sub" / "a.txt").read_text(encoding="utf-8") == "a", "skills tier: copy_tree copies outside")

        (outside / "m.txt").write_text("m", encoding="utf-8")
        check(_refused(lambda: skills_tier.move(outside / "m.txt", repo / "scripts" / "m.txt")) and (outside / "m.txt").exists(),
              "skills tier: move refuses a destination under a code folder and leaves the source")
        skills_tier.move(outside / "m.txt", outside / "moved.txt")
        check((outside / "moved.txt").read_text(encoding="utf-8") == "m" and not (outside / "m.txt").exists(), "skills tier: move relocates outside")

        check(_refused(lambda: skills_tier.file_handler(repo / "scripts" / "x.log")) and not (repo / "scripts" / "x.log").exists(),
              "skills tier: file_handler refuses and creates nothing")
        handler = skills_tier.file_handler(outside / "x.log")
        try:
            check(isinstance(handler, logging.FileHandler) and (outside / "x.log").exists(), "skills tier: file_handler opens a log outside")
        finally:
            handler.close()

    # the real module constants: the guard follows the ratchet's configuration
    cfg = tomllib.loads((ROOT / ".fleet.toml").read_text(encoding="utf-8"))["runtime_write_ratchet"]
    for mod_name, mod in (("skills tier", skills_tier), ("hooks tier", hooks_tier)):
        check(tuple(mod.CODE_FOLDERS) == tuple(cfg["code_folders"]),
              f"{mod_name}: CODE_FOLDERS equals [runtime_write_ratchet] code_folders in .fleet.toml")
        check(mod.REPO_ROOT == ROOT.resolve(), f"{mod_name}: REPO_ROOT is this checkout")
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    for folder in skills_tier.LIVE_STATE_FOLDERS:
        check(f"{folder}/" in gitignore, f"the live-state carve-out {folder} is gitignored, so it is not tracked code")

    # the two tiers cannot import each other, so the shared bodies are held to each other here
    def _functions(path: Path) -> dict[str, str]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        return {n.name: ast.dump(n) for n in tree.body if isinstance(n, ast.FunctionDef)}

    skills_funcs = _functions(ROOT / "skills" / "_lib" / "fleet_core" / "runtime_files.py")
    hooks_funcs = _functions(ROOT / "hooks" / "runtime_files" / "__init__.py")
    for name in SHARED:
        check(name in skills_funcs and skills_funcs.get(name) == hooks_funcs.get(name),
              f"the hooks and skills copies of {name} are the same code")
    for name in hooks_funcs:
        check(name in SHARED or name.startswith("_"), f"hooks tier defines nothing the skills tier lacks: {name}")
    for name in SKILLS_ONLY:
        check(name in skills_funcs, f"skills tier defines {name}")
finally:
    shutil.rmtree(base, ignore_errors=True)

_h.report_and_exit("test_runtime_files")
