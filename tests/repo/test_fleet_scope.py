"""Fleet scope: the `architecture_ignore` switch, the loud missing-file error, and the writer.

Everything runs against throwaway `projects.toml` copies and fixture repos,
never the live file.
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "skills" / "_lib"))
import design_sweep_scan as dss  # noqa: E402
import fleet_audit_scan as fas  # noqa: E402
from fleet_core import fleet_repo_scan as frs  # noqa: E402
import fleet_status as fst  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check

EXAMPLE = ROOT / "hooks" / "projects.toml.example"
base = Path(tempfile.mkdtemp(prefix="fleet-scope-"))


def _toml(name: str, ignore: list[str]) -> Path:
    path = base / name
    path.write_text(
        '[awake]\ncwd_prefix = "X:/fleet/awake"\n\n'
        '[asleep]\ncwd_prefix = "X:/fleet/asleep"\n\n'
        "[global]\narchitecture_ignore = [\n"
        + "".join(f'  "{r}",\n' for r in ignore)
        + "]\n",
        encoding="utf-8",
    )
    return path


def _fixture_repo(root: Path, name: str) -> None:
    repo = root / name
    repo.mkdir(parents=True)

    def g(*args: str) -> None:
        subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=True)

    g("init", "-q")
    g("remote", "add", "origin", f"https://github.com/ferraroroberto/{name}.git")
    # An old index.lock parks the repo in /audit-fleet's stale_lock bucket
    # before any fetch, so the scan below never touches the network.
    lock = repo / ".git" / "index.lock"
    lock.write_bytes(b"")
    old = time.time() - 15 * 24 * 3600
    os.utime(lock, (old, old))


# ---- the read side: fleet_repos, iter_fleet_repos, ignored_repos ---------------

scoped = _toml("scoped.toml", ["asleep"])
check(set(frs.fleet_repos(scoped)) == {"awake"}, "fleet_repos drops a repo in architecture_ignore")
check(set(frs.fleet_repos(scoped, include_ignored=True)) == {"awake", "asleep"},
      "fleet_repos(include_ignored=True) keeps it")
check(set(frs.fleet_repos(_toml("open.toml", []))) == {"awake", "asleep"},
      "an empty switch keeps every repo")
check(frs.ignored_repos(scoped) == frozenset({"asleep"}), "ignored_repos reads the switch")
check(set(frs.fleet_repo_tables(scoped)) == {"awake", "asleep"},
      "fleet_repo_tables stays unfiltered (declaration readers)")

fleet_root = base / "fleet"
_fixture_repo(fleet_root, "awake")
_fixture_repo(fleet_root, "asleep")
_fixture_repo(fleet_root, "undeclared")
names = [d.name for d in frs.iter_fleet_repos(fleet_root, projects_toml=scoped)]
check(names == ["awake", "undeclared"], f"iter_fleet_repos skips the switched-off repo, keeps undeclared: {names}")
names = [d.name for d in frs.iter_fleet_repos(fleet_root, include_ignored=True)]
check(names == ["asleep", "awake", "undeclared"], f"include_ignored=True yields every repo: {names}")
names = [d.name for d in frs.iter_fleet_repos(fleet_root, only="asleep", projects_toml=scoped)]
check(names == ["asleep"], f"an explicit `only` reaches a switched-off repo: {names}")

# ---- the two fleet sweeps obey the switch with no change of their own ----------

real_override = os.environ.get(frs.PROJECTS_TOML_ENV_VAR)
os.environ[frs.PROJECTS_TOML_ENV_VAR] = str(scoped)
try:
    swept = dss.scan(str(fleet_root))
    seen = {r["repo"] for b in swept.values() for r in b}
    check("asleep" not in seen and "awake" in seen, f"/design-sweep's scan skips the switched-off repo: {seen}")
    audited = fas.scan(str(fleet_root), dry_run=True)
    seen = {r["repo"] for r in audited["stale_lock"]}
    check(audited["enumerated"] == 2 and "asleep" not in seen,
          f"/audit-fleet's scan skips the switched-off repo: {audited['enumerated']} enumerated, {seen}")
finally:
    if real_override is None:
        os.environ.pop(frs.PROJECTS_TOML_ENV_VAR, None)
    else:
        os.environ[frs.PROJECTS_TOML_ENV_VAR] = real_override

# ---- a missing local file fails loudly, naming the example ---------------------

missing = base / "absent" / "projects.toml"
for label, call in (
    ("fleet_repos", lambda: frs.fleet_repos(missing)),
    ("fleet_repo_tables", lambda: frs.fleet_repo_tables(missing)),
    ("iter_fleet_repos", lambda: list(frs.iter_fleet_repos(fleet_root, projects_toml=missing))),
):
    try:
        call()
        msg = ""
    except frs.ProjectsTomlMissing as exc:
        msg = str(exc)
    check("projects.toml.example" in msg, f"{label} raises ProjectsTomlMissing naming the example: {msg!r}")

# The env override is explicit: it redirects a default read, and pointing it at an
# absent file still raises rather than falling back to the example.
real_override = os.environ.get(frs.PROJECTS_TOML_ENV_VAR)
try:
    os.environ[frs.PROJECTS_TOML_ENV_VAR] = str(scoped)
    check(set(frs.fleet_repos()) == {"awake"}, "the env override redirects a default fleet_repos() read")
    os.environ[frs.PROJECTS_TOML_ENV_VAR] = str(missing)
    try:
        frs.fleet_repos()
        msg = ""
    except frs.ProjectsTomlMissing as exc:
        msg = str(exc)
    check("projects.toml.example" in msg, f"an override naming an absent file raises, never falls back: {msg!r}")
finally:
    if real_override is None:
        os.environ.pop(frs.PROJECTS_TOML_ENV_VAR, None)
    else:
        os.environ[frs.PROJECTS_TOML_ENV_VAR] = real_override

# The gate hands `build_data` the example when the local list is absent; the
# runtime default must keep raising.
_spec = importlib.util.spec_from_file_location(
    "system_map_build_data", ROOT / ".claude" / "skills" / "system-map" / "build_data.py")
bd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bd)
check(set(bd.fleet_repos(EXAMPLE)) == set(frs.fleet_repos(EXAMPLE)), "build_data.fleet_repos reads the path it is given")
check(bd.regenerate(projects_toml=scoped).startswith(bd.HEADER), "build_data.regenerate builds from a given list, local file absent or not")
try:
    bd.regenerate(projects_toml=missing)
    msg = ""
except frs.ProjectsTomlMissing as exc:
    msg = str(exc)
check("projects.toml.example" in msg, f"build_data.regenerate raises ProjectsTomlMissing on a missing list: {msg!r}")

# ---- the writer ------------------------------------------------------------------

scratch = base / "scratch.toml"
shutil.copyfile(EXAMPLE, scratch)
original = scratch.read_bytes()
active_repo = next(r["name"] for r in fst.list_status(scratch) if r["status"] == fst.ACTIVE)

check(fst.set_status(active_repo, fst.DORMANT, scratch) == "changed", "set dormant reports changed")
check(fst.get_status(active_repo, scratch) == fst.DORMANT, "get reads the new status")
check(active_repo not in frs.fleet_repos(scratch), "a flipped repo leaves fleet_repos")
check(fst.set_status(active_repo, fst.DORMANT, scratch) == "unchanged", "a second identical set is a no-op")
after_dormant = tomllib.loads(scratch.read_text(encoding="utf-8"))
before = tomllib.loads(original.decode("utf-8"))
before["global"]["architecture_ignore"].append(active_repo)
check(after_dormant == before, "set dormant changes architecture_ignore and nothing else")
check(fst.set_status(active_repo, fst.ACTIVE, scratch) == "changed", "set active reports changed")
check(scratch.read_bytes() == original, "dormant then active round-trips the file byte for byte")

# `original` is the working copy of the example, which `core.autocrlf` may have
# checked out with CRLF already; normalise before doubling the carriage returns.
crlf_bytes = original.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
crlf = base / "crlf.toml"
crlf.write_bytes(crlf_bytes)
fst.set_status(active_repo, fst.DORMANT, crlf)
fst.set_status(active_repo, fst.ACTIVE, crlf)
check(crlf.read_bytes() == crlf_bytes, "a CRLF file round-trips byte for byte")


def refusal(*args: object) -> str:
    try:
        fst.set_status(*args)  # type: ignore[arg-type]
    except fst.FleetStatusError as exc:
        return str(exc)
    return ""


unknown = refusal("no-such-repo", fst.DORMANT, scratch)
invalid = refusal(active_repo, "sleeping", scratch)
check("unknown repo" in unknown and "no-such-repo" in unknown, f"unknown repo refused by name: {unknown!r}")
check("invalid status" in invalid and "sleeping" in invalid, f"invalid value refused by name: {invalid!r}")
check(unknown != invalid, "the two refusals carry distinct messages")
check(scratch.read_bytes() == original, "refusals leave the file untouched")

broken = base / "broken.toml"
broken.write_text("[global\n", encoding="utf-8")
check("does not parse" in refusal(active_repo, fst.DORMANT, broken), "an unparsable file is refused")
check("projects.toml.example" in refusal(active_repo, fst.DORMANT, missing), "a missing file is refused with the example named")

inline = base / "inline.toml"
inline.write_text('[a]\ncwd_prefix = "X:/a"\n\n[global]\narchitecture_ignore = []\n', encoding="utf-8")
check("multi-line" in refusal("a", fst.DORMANT, inline), "an inline array is refused, not rewritten")
check(inline.read_text(encoding="utf-8").endswith("architecture_ignore = []\n"), "the inline file is untouched")

cli = subprocess.run([sys.executable, str(ROOT / "skills" / "_lib" / "fleet_status.py"),
                      "--projects-toml", str(scratch), "set", "no-such-repo", "dormant"],
                     capture_output=True, text=True)
check(cli.returncode == 2 and "REFUSED: unknown repo" in cli.stderr, f"CLI refuses with exit 2: {cli.returncode} {cli.stderr!r}")

shutil.rmtree(base, ignore_errors=True)
_h.report_and_exit("test_fleet_scope")
