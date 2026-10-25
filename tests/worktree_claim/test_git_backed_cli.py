"""worktree_claim's git-backed CLI: reparse-safe teardown, land-primary, root-config mirroring and mode, against real temp repos.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/worktree_claim/test_git_backed_cli.py`  (also invoked by tests/run_acceptance.py)
"""
from __future__ import annotations

import http.server
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
import json as _json

from _suite import _h, check, wc  # noqa: E402


# ---- real junction + reparse-safe teardown: the target survives (#620) ------
#
# Proof this branch exists for: create a REAL junction to a directory holding a
# sentinel file, tear it down the exact way setup_worktree / remove_worktree do
# (strip via _strip_junction BEFORE any recursive delete of the worktree dir),
# and assert the sentinel still exists afterwards. Proven for BOTH junction
# shapes this branch handles: the existing top-level '.venv' junction and the
# NEW declarative nested extra junction ('vendor/comfyui', whose parent dir
# does not pre-exist in a fresh worktree). No git, no real worktree -- a
# throwaway temp-dir fixture only, per the branch's own safety rule.

jt_base = Path(tempfile.mkdtemp(prefix="wc-junction-proof-"))
try:
    primary = jt_base / "primary"
    (primary / ".venv").mkdir(parents=True)
    (primary / ".venv" / "sentinel.txt").write_text("venv-survives", encoding="utf-8")
    (primary / "vendor" / "comfyui").mkdir(parents=True)
    (primary / "vendor" / "comfyui" / "sentinel.txt").write_text("vendor-survives", encoding="utf-8")
    (primary / ".fleet.toml").write_text(
        '[worktree]\nextra_junctions = ["vendor/comfyui"]\n', encoding="utf-8")

    wt = jt_base / "primary-wt-620"
    wt.mkdir()
    # A real `git worktree add` checks out tracked files, and .fleet.toml is
    # tracked -- mirror that so the teardown side reads targets from the
    # worktree's OWN .fleet.toml, exactly as remove_worktree does.
    shutil.copy2(primary / ".fleet.toml", wt / ".fleet.toml")

    targets = wc.worktree_junction_targets(primary)
    check(targets == [".venv", "vendor/comfyui"],
          "junction proof: fixture declares both junction shapes (#620)")

    for rel in targets:
        link = wt / rel
        ok, err = wc._junction(link, primary / rel)
        check(ok, f"junction proof: real mklink /J succeeded for {rel!r} ({err})")

    # Prove the junctions actually work before tearing anything down.
    check((wt / ".venv" / "sentinel.txt").read_text(encoding="utf-8") == "venv-survives",
          "junction proof: .venv junction reads through to the primary's real file")
    check((wt / "vendor" / "comfyui" / "sentinel.txt").read_text(encoding="utf-8") == "vendor-survives",
          "junction proof: nested extra-junction reads through to the primary's real file")

    # Tear down exactly as remove_worktree does: strip every declared target
    # BEFORE any recursive delete of the worktree directory.
    for rel in wc.worktree_junction_targets(wt):
        wc._strip_junction(wt / rel)
    shutil.rmtree(wt, ignore_errors=True)

    check(not wt.exists(), "junction proof: the worktree directory itself is gone")
    check((primary / ".venv" / "sentinel.txt").read_text(encoding="utf-8") == "venv-survives",
          "junction proof: .venv junction TARGET survives teardown intact (#620)")
    check((primary / "vendor" / "comfyui" / "sentinel.txt").read_text(encoding="utf-8") == "vendor-survives",
          "junction proof: nested extra-junction TARGET survives teardown intact (#620)")
finally:
    shutil.rmtree(jt_base, ignore_errors=True)


# ---- land-primary CLI, git-backed: the worktree lane's landing step (#647) ----
#
# End-to-end over real repos, because the CLI *is* what a lane invokes and the
# whole defect was a missing step rather than a wrong computation. Asserts the
# three outcomes a finish summary can carry, and -- the safety property that
# must not regress -- that a refusal leaves the tree exactly as it found it:
# nothing stashed, nothing checked out, nothing forced.

CLI = str(Path(__file__).resolve().parent.parent.parent / "skills" / "_lib" / "worktree_claim.py")
_PRODUCTION_CLI = CLI


def _land(repo: Path, issue: str = "647") -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, CLI, "land-primary", str(repo), issue],
                          capture_output=True, text=True)


def _git_t(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    # `core.hooksPath` -> an empty dir: this machine carries a global commit
    # hook that rejects non-allowlisted author emails, which would otherwise
    # silently leave the fixture repos commit-less.
    res = subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                          "-c", f"core.hooksPath={_no_hooks}", *args],
                         cwd=str(cwd), capture_output=True, text=True)
    if res.returncode != 0 and args[0] in ("init", "clone", "commit", "add", "checkout"):
        raise AssertionError(f"fixture git {args[0]} failed: {res.stderr.strip()}")
    return res


# ---- copy_root_config: gitignored root-level *.json mirrors into worktrees (#714) ----
#
# Some repos keep runtime config at the repo root instead of under config/
# (accounting-quarterly's config.json, home-automation's devices.json) --
# git worktree add populates tracked files only, so a fresh worktree crashed
# on startup with no config at all until this closed the gap. A REAL git repo
# is required here (unlike copy_runtime_config's plain-directory fixtures)
# because "gitignored" is exactly what `git check-ignore` answers.

root_base = Path(tempfile.mkdtemp(prefix="wc-root-"))
_no_hooks = root_base / "nohooks"
_no_hooks.mkdir()
try:
    primary = root_base / "myrepo"
    primary.mkdir()
    _git_t(primary, "init", "-b", "main")
    (primary / ".gitignore").write_text(
        "/config.json\n/classification_rules.json\n/broken.json\n/list.json\n", encoding="utf-8")
    (primary / "package.json").write_text('{"name": "tracked-not-ignored"}', encoding="utf-8")
    (primary / "config.sample.json").write_text("{}", encoding="utf-8")
    _git_t(primary, "add", "-A")
    _git_t(primary, "commit", "-m", "init")

    (primary / "config.json").write_text(
        _json.dumps({"port": 8447, "auth_token": "s3cret"}), encoding="utf-8")
    (primary / "classification_rules.json").write_text(
        _json.dumps({"rules": ["a", "b"]}), encoding="utf-8")
    (primary / "list.json").write_text("[1, 2, 3]", encoding="utf-8")
    (primary / "broken.json").write_text("{not json", encoding="utf-8")

    wt = root_base / ("myrepo" + wc.WT_SEP + "714")
    wt.mkdir()
    copied = wc.copy_root_config(primary, wt)
    names = sorted(p.name for p in copied)

    check(names == ["broken.json", "classification_rules.json", "config.json", "list.json"],
          f"copy_root_config: copies exactly the gitignored root JSONs, nothing tracked (got {names!r}) (#714)")
    check("package.json" not in names,
          "copy_root_config: a tracked, non-gitignored root *.json is never copied (#714)")
    check("config.sample.json" not in names,
          "copy_root_config: *.sample.json excluded even though it's on disk at the root (#714)")

    got = _json.loads((wt / "config.json").read_text(encoding="utf-8"))
    check(got["port"] != 8447 and wc.WT_PORT_BASE <= got["port"] < wc.WT_PORT_BASE + wc.WT_PORT_SPAN,
          "copy_root_config: a top-level port is repointed into the 8500-8999 band, same as config/*.json (#714)")
    check(got["auth_token"] == "s3cret",
          "copy_root_config: every other field, secrets included, copies verbatim (#714)")

    got_rules = _json.loads((wt / "classification_rules.json").read_text(encoding="utf-8"))
    check(got_rules == {"rules": ["a", "b"]},
          "copy_root_config: a file with no top-level port copies untouched (#714)")

    check((wt / "list.json").read_text(encoding="utf-8") == "[1, 2, 3]",
          "copy_root_config: a non-object JSON copies verbatim, never breaks setup (#714)")
    check((wt / "broken.json").read_text(encoding="utf-8") == "{not json",
          "copy_root_config: an unparseable file copies verbatim, never breaks setup (#714)")

    src_now = _json.loads((primary / "config.json").read_text(encoding="utf-8"))
    check(src_now["port"] == 8447, "copy_root_config: the PRIMARY's own port is never rewritten (#714)")

    # Deterministic: same repo + same issue -> same port when it is still free.
    (wt / "config.json").unlink()
    wc.copy_root_config(primary, wt)
    again = _json.loads((wt / "config.json").read_text(encoding="utf-8"))
    check(again["port"] == got["port"],
          "copy_root_config: same lane re-setup reproduces the same port (#714)")

    # An already-present destination is left alone, same as copy_runtime_config.
    wt2 = root_base / ("myrepo" + wc.WT_SEP + "715")
    wt2.mkdir()
    (wt2 / "config.json").write_text('{"port": 1}', encoding="utf-8")
    wc.copy_root_config(primary, wt2)
    check(_json.loads((wt2 / "config.json").read_text(encoding="utf-8"))["port"] == 1,
          "copy_root_config: an existing destination file is not overwritten or repointed (#714)")

    # A shared `assigned` set (what setup_worktree passes both calls) keeps a
    # config/*.json port and a root-level port DISTINCT even with the same
    # issue seed -- each call computing its own independent set could collide.
    portrepo = root_base / "portrepo"
    (portrepo / "config").mkdir(parents=True)
    _git_t(portrepo, "init", "-b", "main")
    (portrepo / "config" / "app.json").write_text(
        _json.dumps({"port": 8447}), encoding="utf-8")
    (portrepo / ".gitignore").write_text("/config.json\n", encoding="utf-8")
    (portrepo / "README.md").write_text("hi", encoding="utf-8")
    _git_t(portrepo, "add", "-A")
    _git_t(portrepo, "commit", "-m", "init")
    (portrepo / "config.json").write_text(_json.dumps({"port": 8447}), encoding="utf-8")

    wt_shared = root_base / ("portrepo" + wc.WT_SEP + "900")
    wt_shared.mkdir()
    shared_assigned: set = set()
    wc.copy_runtime_config(portrepo, wt_shared, shared_assigned)
    wc.copy_root_config(portrepo, wt_shared, shared_assigned)
    port_a = _json.loads((wt_shared / "config" / "app.json").read_text(encoding="utf-8"))["port"]
    port_b = _json.loads((wt_shared / "config.json").read_text(encoding="utf-8"))["port"]
    check(port_a != port_b,
          "copy_root_config + copy_runtime_config: a shared assigned-ports set keeps a "
          "config/*.json port and a root-level port distinct, even with the same issue seed (#714)")

    # A repo with no gitignored root-level JSON at all: no-op, never a setup failure.
    plain = root_base / "plainrepo"
    plain.mkdir()
    _git_t(plain, "init", "-b", "main")
    (plain / "README.md").write_text("hi", encoding="utf-8")
    _git_t(plain, "add", "-A")
    _git_t(plain, "commit", "-m", "init")
    wt3 = root_base / ("plainrepo" + wc.WT_SEP + "1")
    wt3.mkdir()
    check(wc.copy_root_config(plain, wt3) == [],
          "copy_root_config: a repo with no gitignored root JSON -> no-op (#714)")
finally:
    shutil.rmtree(root_base, ignore_errors=True)


land_base = Path(tempfile.mkdtemp(prefix="wc-land-"))
_no_hooks = land_base / "nohooks"
_no_hooks.mkdir()
try:
    upstream = land_base / "upstream"
    upstream.mkdir()
    _git_t(upstream, "init", "-b", "main")
    (upstream / "a.txt").write_text("one", encoding="utf-8")
    _git_t(upstream, "add", "-A")
    _git_t(upstream, "commit", "-m", "one")
    primary = land_base / "myrepo"
    _git_t(land_base, "clone", str(upstream), str(primary))

    # Relocate the library so `myrepo` can be declared a known absence (no
    # tray_cmd) independent of whatever the real fleet's own
    # hooks/projects.toml says. These tests exercise the plain fast-forward /
    # dirty / branch guards, not the #665/#686 service guard, and must not
    # depend on `myrepo` happening to be undeclared for real -- an undeclared
    # repo is now unknown, therefore refuse (fleet-config#686).
    lib_dst = land_base / "skills" / "_lib"
    lib_dst.mkdir(parents=True)
    for mod in (Path(__file__).resolve().parent.parent.parent / "skills" / "_lib").glob("*.py"):
        shutil.copy2(mod, lib_dst / mod.name)
    CLI = str(lib_dst / "worktree_claim.py")
    (land_base / "hooks").mkdir()
    (land_base / "hooks" / "projects.toml").write_text(
        f'[myrepo]\ncwd_prefix = "{primary.as_posix()}"\n', encoding="utf-8")

    # Upstream moves ahead -- exactly the post-merge state a worktree lane is in.
    (upstream / "b.txt").write_text("two", encoding="utf-8")
    _git_t(upstream, "add", "-A")
    _git_t(upstream, "commit", "-m", "two")

    res = _land(primary)
    check(res.returncode == 0 and res.stdout.strip() == "PRIMARY=live behind=0",
          f"land-primary: clean primary behind origin -> fast-forwards, PRIMARY=live (got {res.stdout.strip()!r})")
    check((primary / "b.txt").exists(),
          "land-primary: the merged file is actually present in the primary afterwards")

    res = _land(primary)
    check(res.returncode == 0 and "PRIMARY=live" in res.stdout,
          "land-primary: idempotent -- an already-current primary is live, not an error")

    # Refusal 1: dirty tree. The reason a lane must report stale, never recover.
    (primary / "untracked.txt").write_text("a human's work in progress", encoding="utf-8")
    res = _land(primary)
    check(res.returncode == 1 and
          res.stdout.strip() == "PRIMARY=stale reason=working tree has uncommitted changes",
          f"land-primary: dirty primary -> PRIMARY=stale, exit 1 (got {res.stdout.strip()!r})")
    check((primary / "untracked.txt").read_text(encoding="utf-8") == "a human's work in progress",
          "land-primary: a refused tree is untouched -- never stashed, never forced (#647)")
    (primary / "untracked.txt").unlink()

    # Refusal 2: primary parked off its default branch. The fix is `pull
    # --ff-only` on a tree already on main -- never a checkout from a worktree.
    _git_t(primary, "checkout", "-q", "-b", "fix/599-someone-elses-work")
    res = _land(primary)
    check(res.returncode == 1 and "not 'main'" in res.stdout and "fix/599" in res.stdout,
          f"land-primary: primary off its default branch -> stale, no checkout (got {res.stdout.strip()!r})")
    check(_git_t(primary, "branch", "--show-current").stdout.strip() == "fix/599-someone-elses-work",
          "land-primary: refusing did not switch the primary's branch (#647)")
    _git_t(primary, "checkout", "-q", "main")

    # Refusal 3: a live claim held by a *different* issue.
    lock = wc.lock_dir_for(primary)
    lock.mkdir(parents=True, exist_ok=True)
    wc.write_meta(lock, {"issue": "640", "branch": "fix/640-x", "created_iso": "2026-08-16T09:00:00"})
    res = _land(primary, issue="647")
    check(res.returncode == 1 and "640" in res.stdout,
          f"land-primary: another issue's live claim -> stale, naming the holder (got {res.stdout.strip()!r})")
    check(_land(primary, issue="640").returncode == 0,
          "land-primary: the claim's own issue is still allowed to land")
    shutil.rmtree(lock, ignore_errors=True)

    # Wrong target: a linked worktree, not the primary. Refuses, changes nothing.
    wt = wc.setup_worktree(primary, "647", "fix/647-x")
    res = _land(wt)
    check(res.returncode == 2 and "not a primary checkout" in res.stdout,
          f"land-primary: pointed at a worktree -> exit 2, no landing attempted (got {res.stdout.strip()!r})")
    check(wt.exists(), "land-primary: the mistargeted worktree is left intact (never a teardown)")
    wc.remove_worktree(wt)

    # Every outcome emitted exactly one PRIMARY= line -- the summary is never silent.
    check(all(len([l for l in _land(p, i).stdout.strip().splitlines() if l.startswith("PRIMARY=")]) == 1
              for p, i in ((primary, "647"), (primary, "999"))),
          "land-primary: exactly one PRIMARY= line on stdout in every outcome (#647)")
finally:
    CLI = _PRODUCTION_CLI
    shutil.rmtree(land_base, ignore_errors=True)


# ---- land-primary CLI: refuse a tree a live process is serving (#665) ------
#
# Reproduces the observed case from fixtures, never by mutating the live
# app-launcher checkout: a repo declaring `tray_cmd` + `webapp_port`, an
# `/api/version` answering with the *running* commit, and a primary sitting
# behind its origin. Pre-fix, `land-primary` fast-forwarded it -- that is
# exactly how the app-launcher primary ended up at 1705558 while its webapp
# kept serving 9af573f.
#
# The declaration is injected by *relocating the library*, not by adding a CLI
# flag: `_lib` is copied to a temp tree with its own `hooks/projects.toml`
# beside it, so the production tool grows no way to be told "ignore the tray
# and land anyway".

svc_base = Path(tempfile.mkdtemp(prefix="wc-svc-"))
_running = None
try:
    _no_hooks = svc_base / "nohooks"          # _git_t reads this global at call time
    _no_hooks.mkdir()

    lib_src = Path(__file__).resolve().parent.parent.parent / "skills" / "_lib"
    lib_dst = svc_base / "skills" / "_lib"
    lib_dst.mkdir(parents=True)
    for mod in lib_src.glob("*.py"):
        shutil.copy2(mod, lib_dst / mod.name)
    SVC_CLI = str(lib_dst / "worktree_claim.py")

    def _make_repo(name: str) -> Path:
        """An `upstream` one commit ahead of its clone -- the post-merge state."""
        up = svc_base / f"{name}-upstream"
        up.mkdir()
        _git_t(up, "init", "-b", "main")
        (up / "a.txt").write_text("one", encoding="utf-8")
        _git_t(up, "add", "-A")
        _git_t(up, "commit", "-m", "one")
        clone = svc_base / name
        _git_t(svc_base, "clone", str(up), str(clone))
        (up / "static.js").write_text("the asset the running process has never read", encoding="utf-8")
        _git_t(up, "add", "-A")
        _git_t(up, "commit", "-m", "two")
        return clone

    served = _make_repo("trayrepo")           # declares tray_cmd, service up
    idle = _make_repo("idlerepo")             # declares tray_cmd, service down
    junction = _make_repo("junctionrepo")     # declares no tray_cmd (the fleet-config case)
    portless = _make_repo("portlessrepo")     # declares tray_cmd, nothing to probe

    # A real listener answering /api/version with the commit it is "running" --
    # the same shape app-launcher's webapp reports its own skew with.
    class _Version(http.server.BaseHTTPRequestHandler):
        def do_GET(self):                                        # noqa: N802
            body = json.dumps({"git_sha": "9af573f", "head_sha": "1705558"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):                               # keep the run quiet
            pass

    _running = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Version)
    live_port = _running.server_address[1]
    threading.Thread(target=_running.serve_forever, daemon=True).start()

    # A port nothing is listening on: bound, read back, released.
    _probe = socket.socket()
    _probe.bind(("127.0.0.1", 0))
    dead_port = _probe.getsockname()[1]
    _probe.close()

    (svc_base / "hooks").mkdir()
    (svc_base / "hooks" / "projects.toml").write_text(f"""
[trayrepo]
cwd_prefix       = "{served.as_posix()}"
webapp_port      = {live_port}
tray_cmd         = "tray.bat"
api_version_path = "/api/version"

[idlerepo]
cwd_prefix       = "{idle.as_posix()}"
webapp_port      = {dead_port}
tray_cmd         = "tray.bat"
api_version_path = "/api/version"

[junctionrepo]
cwd_prefix       = "{junction.as_posix()}"

[portlessrepo]
cwd_prefix       = "{portless.as_posix()}"
tray_cmd         = "tray.bat"

[global]
architecture_ignore = []
""", encoding="utf-8")

    def _land_svc(repo: Path) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, SVC_CLI, "land-primary", str(repo), "665"],
                              capture_output=True, text=True)

    res = _land_svc(served)
    out = res.stdout.strip()
    check(res.returncode == 1 and out.startswith("PRIMARY=stale"),
          f"land-primary: live service on the declared port -> stale, exit 1 (got {out!r})")
    check(f":{live_port}" in out and "9af573f" in out,
          "land-primary: the refusal names the port and the commit the live process is actually on")
    check("restart" in out,
          "land-primary: the refusal names the restart as the remedy (#665)")
    check(not (served / "static.js").exists(),
          "land-primary: refusing left the tree behind-but-coherent -- no mixed-commit assets")
    check(len([l for l in out.splitlines() if l.startswith("PRIMARY=")]) == 1,
          "land-primary: the live-service refusal is exactly one PRIMARY= line, like every other")

    res = _land_svc(idle)
    check(res.returncode == 0 and res.stdout.strip() == "PRIMARY=live behind=0",
          f"land-primary: declared tray, nothing listening -> lands normally (got {res.stdout.strip()!r})")
    check((idle / "static.js").exists(),
          "land-primary: the idle repo really did fast-forward, not just report it")

    res = _land_svc(junction)
    check(res.returncode == 0 and res.stdout.strip() == "PRIMARY=live behind=0",
          f"land-primary: no tray_cmd -> lands unconditionally, #647's junction case intact "
          f"(got {res.stdout.strip()!r})")

    res = _land_svc(portless)
    check(res.returncode == 1 and "cannot confirm" in res.stdout,
          f"land-primary: tray declared but unprobeable -> refuse, never assume idle "
          f"(got {res.stdout.strip()!r})")
    check(not (portless / "static.js").exists(),
          "land-primary: the unprobeable repo was left untouched too")

    # A repo the table has never heard of is *unknown*, not a known absence of
    # a declaration -- every fixture repo in the block above this one is
    # registered (fleet-config#686: task-os's tray was live on :8448 for days
    # while undeclared, and the pre-fix code landed it anyway on that basis).
    stranger = _make_repo("undeclaredrepo")
    res = _land_svc(stranger)
    check(res.returncode == 1 and res.stdout.strip().startswith("PRIMARY=stale")
          and "not declared in hooks/projects.toml" in res.stdout,
          f"land-primary: repo absent from projects.toml -> unknown, refuses "
          f"(got {res.stdout.strip()!r})")
    check(not (stranger / "static.js").exists(),
          "land-primary: the undeclared repo was left untouched too")
finally:
    if _running is not None:
        _running.shutdown()
        _running.server_close()
    shutil.rmtree(svc_base, ignore_errors=True)


# ---- mode CLI, git-backed: answers about the cwd, not the argument (#652) ----
#
# Over a REAL linked worktree, deliberately: the whole defect lives in path
# resolution, so a mocked filesystem would reproduce nothing. From inside
# `<repo>-wt-<N>`, `_resolve_path_arg`'s sibling fallback (#165) turns the bare
# repo name every skill documents into the *primary*, and the pre-fix
# implementation answered `primary` to a worktree lane -- silently, and always
# toward the teardown path that fails with "'main' is already used by worktree".

# Pure half first: reconciliation is at repo granularity, never tree granularity.
_g = Path("E:/automation/fleet-config/.git")
check(wc.mode_check(_g, _g) == (True, "repo argument and cwd agree on the repo"),
      "mode_check: same shared git dir -> answerable (a worktree naming its primary)")
check(wc.mode_check(None, _g)[0] is False and "cwd is not inside" in wc.mode_check(None, _g)[1],
      "mode_check: cwd outside git -> not answerable, with a reason")
check(wc.mode_check(_g, None)[0] is False,
      "mode_check: argument not a checkout -> not answerable")
check(wc.mode_check(_g, Path("E:/automation/app-launcher/.git"))[0] is False,
      "mode_check: a genuinely different repo -> not answerable")


def _mode(cwd: Path, arg: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, CLI, "mode", arg],
                          cwd=str(cwd), capture_output=True, text=True)


mode_base = Path(tempfile.mkdtemp(prefix="wc-mode-"))
_no_hooks = mode_base / "nohooks"
_no_hooks.mkdir()
try:
    # Cloned, not `init`ed: `setup_worktree` branches off `main_ref`, which
    # resolves to `origin/main` -- a bare init has no such ref.
    upstream = mode_base / "upstream"
    upstream.mkdir()
    _git_t(upstream, "init", "-b", "main")
    (upstream / "a.txt").write_text("one", encoding="utf-8")
    _git_t(upstream, "add", "-A")
    _git_t(upstream, "commit", "-m", "one")

    primary = mode_base / "myrepo"
    _git_t(mode_base, "clone", str(upstream), str(primary))
    other = mode_base / "otherrepo"
    _git_t(mode_base, "clone", str(upstream), str(other))

    wt = wc.setup_worktree(primary, "652", "fix/652-x")
    check(wt.exists() and wt.name == "myrepo-wt-652",
          "mode fixture: a real linked worktree exists (not a mock)")
    # The disagreement that *is* the bug, asserted directly.
    check(_git_t(wt, "rev-parse", "--path-format=absolute", "--git-dir").stdout.strip()
          != _git_t(wt, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip(),
          "mode fixture: inside the worktree --git-dir and --git-common-dir differ")

    # THE regression: the bare-name form every SKILL.md documents, from the worktree.
    res = _mode(wt, "myrepo")
    check(res.returncode == 0 and res.stdout.strip() == "worktree",
          f"mode: bare repo name from inside <repo>-wt-<N> -> 'worktree' (got {res.stdout.strip()!r}) (#652)")

    res = _mode(primary, "myrepo")
    check(res.returncode == 0 and res.stdout.strip() == "primary",
          f"mode: bare repo name from the primary still -> 'primary' (got {res.stdout.strip()!r})")

    check(_mode(wt, ".").stdout.strip() == "worktree" and
          _mode(primary, ".").stdout.strip() == "primary",
          "mode: the explicit '.' form agrees with the bare-name form in both trees")

    # Naming the *other* tree of the same repo is not a mismatch -- the argument
    # identifies the repo, the cwd selects the tree.
    check(_mode(primary, str(wt)).stdout.strip() == "primary" and
          _mode(wt, str(primary)).stdout.strip() == "worktree",
          "mode: naming the sibling tree still answers about the cwd, not the argument")

    # Non-answers: distinct, reasoned, never a confident wrong answer.
    unknowns = [
        (_mode(wt, str(other)), "a genuinely different repo"),
        (_mode(wt, "no-such-repo-anywhere"), "a name that resolves to nothing"),
        (_mode(mode_base, str(primary)), "a cwd outside any git checkout"),
    ]
    for res, what in unknowns:
        out = res.stdout.strip()
        check(res.returncode == 2 and out.startswith("UNKNOWN reason=") and len(out) > len("UNKNOWN reason="),
              f"mode: {what} -> 'UNKNOWN reason=<why>', exit 2 (got {out!r}, rc={res.returncode})")
    check(all(r.stdout.strip() not in ("primary", "worktree") for r, _ in unknowns),
          "mode: an unreconcilable argument NEVER produces a confident primary/worktree answer (#652)")

    # Audit of the sibling subcommands (#652 criterion): `status` is repo-scoped
    # -- its lock lives in the shared git dir and `worktree list` is identical
    # from every tree -- so argument-vs-cwd resolution cannot change its answer.
    st_from_wt = _git_t(wt, "worktree", "list").stdout.strip()
    st_from_primary = _git_t(primary, "worktree", "list").stdout.strip()
    check(st_from_wt == st_from_primary,
          "status audit: `git worktree list` is identical from the primary and the worktree")
    check(wc.lock_dir_for(wt) == wc.lock_dir_for(primary),
          "status audit: the claim lock resolves to one shared path from either tree (repo-scoped)")

    wc.remove_worktree(wt)
finally:
    shutil.rmtree(mode_base, ignore_errors=True)

_h.report_and_exit("test_git_backed_cli")
