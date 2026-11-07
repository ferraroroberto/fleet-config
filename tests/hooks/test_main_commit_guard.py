"""Tests for hooks/main_commit_guard.py: `git commit` on the default branch is refused.

Real throwaway repos and the real hook process, driven with sample `PreToolUse`
payloads directly and through `hook_dispatch.py`:

- a commit on the default branch is refused with the branch pattern and `/quick`;
- a feature branch, a `quick/<slug>` branch, a linked worktree on its own branch,
  a detached HEAD, an unborn repo's first commit and a non-commit command pass;
- the repo is read from the payload cwd, a leading `cd`, `git -C` and a nested shell;
- the scheduled map refresh passes only with its marker (granted through the real
  `skills/_lib/commit_guard/marker.py`), only for the repo and paths it names, and
  never once expired or revoked;
- a branch the hook cannot read is NOT CONFIRMED: it warns and lets the commit through.

Run: E:/automation/fleet-config/.venv/Scripts/python.exe tests/hooks/test_main_commit_guard.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Tuple

REPO = Path(__file__).resolve().parent.parent.parent
HOOKS = REPO / "hooks"
MARKER_CLI = REPO / "skills" / "_lib" / "commit_guard" / "marker.py"
sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402
from git_fixtures import init_repo  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_h = CheckHarness()
check = _h.check
PYTHON = sys.executable
ALLOW_ENV = "CLAUDE_HOOKS_ALLOW_MAIN_COMMIT"


def git(repo: Path, *args: str) -> str:
    res = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8")
    if res.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {res.stderr}")
    return res.stdout.strip()


def env_for(state: Path, **extra: str) -> Dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in (ALLOW_ENV, "APP_LAUNCHER_SESSION_ID")}
    env.update({"CLAUDE_HOOKS_STATE_DIR": str(state), "PYTHONUTF8": "1"})
    env.update(extra)
    return env


def drive(command: str, cwd: Path, env: Dict[str, str], tool: str = "Bash",
          dispatch: bool = False) -> Tuple[int, str, str]:
    payload = {"hook_event_name": "PreToolUse", "tool_name": tool, "cwd": str(cwd),
               "session_id": "test-session", "tool_input": {"command": command}}
    script = [str(HOOKS / "hook_dispatch.py"), "main_commit_guard"] if dispatch \
        else [str(HOOKS / "main_commit_guard.py")]
    res = subprocess.run([PYTHON, *script], input=json.dumps(payload), capture_output=True, text=True,
                         encoding="utf-8", timeout=90, env=env)
    return res.returncode, res.stdout, res.stderr


def marker(env: Dict[str, str], verb: str, repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([PYTHON, str(MARKER_CLI), verb, str(repo), *args], capture_output=True, text=True,
                          encoding="utf-8", timeout=60, env=env)


def make_repo(branch: str = "main") -> Path:
    repo = init_repo(branch)
    (repo / "architecture").mkdir()
    (repo / "architecture" / "map.txt").write_text("v1\n", encoding="utf-8")
    (repo / "app.py").write_text("x = 1\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    return repo


COMMIT = 'git commit -m "docs: refresh"'
state = Path(tempfile.mkdtemp(prefix="main_commit_state_"))
made: List[Path] = []


def fresh(branch: str = "main") -> Path:
    repo = make_repo(branch)
    made.append(repo)
    return repo


try:
    env = env_for(state)
    repo = fresh()

    # ---- the refusal ----
    code, _o, err = drive(COMMIT, repo, env)
    check(code == 2 and "'main'" in err and "<type>/<issue-N>-<slug>" in err and "/quick" in err,
          f"a commit on the default branch is refused with the branch pattern and /quick — got {code} {err[:300]}")
    code, _o, err = drive(COMMIT, repo, env, dispatch=True)
    check(code == 2 and "'main'" in err, f"the refusal holds through hook_dispatch — got {code} {err[:300]}")
    code, _o, err = drive(COMMIT, repo, env, tool="PowerShell")
    check(code == 2, f"the same from PowerShell is refused — got {code} {err[:200]}")
    code, _o, err = drive("git commit --amend --no-edit", repo, env)
    check(code == 2, f"an amend on the default branch is refused — got {code} {err[:200]}")
    code, _o, err = drive(f'cd "{repo}" && git add -A && {COMMIT}', Path(tempfile.gettempdir()), env)
    check(code == 2, f"the repo is read from a leading cd — got {code} {err[:200]}")
    code, _o, err = drive(f'git -C "{repo}" commit -m "docs: x"', Path(tempfile.gettempdir()), env)
    check(code == 2, f"the repo is read from git -C — got {code} {err[:200]}")
    code, _o, err = drive("bash -c \"git commit -m 'docs: x'\"", repo, env)
    check(code == 2, f"a commit inside a nested shell is refused — got {code} {err[:200]}")
    code, _o, err = drive(f"{COMMIT} && echo done", repo, env)
    check(code == 2, f"a commit chained with another command is refused — got {code} {err[:200]}")

    # ---- what passes ----
    code, _o, err = drive("git status --short", repo, env)
    check(code == 0, f"a non-commit command passes — got {code} {err[:200]}")
    code, _o, err = drive('gh issue create --title t --body "run git commit on main"', repo, env)
    check(code == 0, f"a body that merely names git commit passes — got {code} {err[:200]}")
    code, _o, err = drive(COMMIT, repo, env, tool="Edit")
    check(code == 0, f"a non-shell tool passes — got {code} {err[:200]}")
    code, _o, err = drive(COMMIT, repo, env_for(state, **{ALLOW_ENV: "1"}))
    check(code == 0, f"the owner's session-level override lets the commit through — got {code} {err[:200]}")
    code, _o, err = drive(f"{ALLOW_ENV}=1 {COMMIT}", repo, env)
    check(code == 2, f"an override typed into the command itself does not count — got {code}")

    for branch in ("feat/1916-guard", "fix/3-x", "quick/typo"):
        git(repo, "checkout", "-q", "-b", branch)
        code, _o, err = drive(COMMIT, repo, env)
        check(code == 0, f"a commit on {branch} passes — got {code} {err[:200]}")
        git(repo, "checkout", "-q", "main")

    wt = Path(tempfile.mkdtemp(prefix="main_commit_wt_")) / "wt"
    git(repo, "worktree", "add", "-q", "-b", "feat/9-wt", str(wt))
    made.append(wt.parent)
    code, _o, err = drive(COMMIT, wt, env)
    check(code == 0, f"a worktree on its own branch passes while the primary sits on main — got {code} {err[:200]}")
    code, _o, err = drive(COMMIT, repo, env)
    check(code == 2, f"the primary on main is still refused beside that worktree — got {code}")

    git(repo, "checkout", "-q", "--detach")
    code, _o, err = drive(COMMIT, repo, env)
    check(code == 0, f"a detached HEAD is not the default branch — got {code} {err[:200]}")
    git(repo, "checkout", "-q", "main")

    master = fresh("master")
    code, _o, err = drive(COMMIT, master, env)
    check(code == 2 and "'master'" in err, f"a master default is refused too — got {code} {err[:200]}")

    unborn = init_repo("main", empty_commit=False)
    made.append(unborn)
    code, _o, err = drive(COMMIT, unborn, env)
    check(code == 0, f"the first commit of a new repo cannot take the PR path — got {code} {err[:200]}")

    # ---- NOT CONFIRMED: an unreadable branch warns and lets the commit through ----
    nowhere = Path(tempfile.mkdtemp(prefix="main_commit_norepo_"))
    made.append(nowhere)
    code, out, err = drive(COMMIT, nowhere, env)
    check(code == 0 and "NOT CONFIRMED" in (out + err),
          f"a directory git cannot read warns NOT CONFIRMED and passes — got {code} {(out + err)[:300]}")
    code, out, err = drive(f'cd "{nowhere / "missing"}" && {COMMIT}', repo, env)
    check(code == 0 and "NOT CONFIRMED" in (out + err),
          f"a cd into nothing warns NOT CONFIRMED and passes — got {code} {(out + err)[:300]}")
    code, out, err = drive("cd $REPO && " + COMMIT, repo, env)
    check(code == 0 and "NOT CONFIRMED" in (out + err),
          f"an unreadable cd warns NOT CONFIRMED and passes — got {code} {(out + err)[:300]}")

    # ---- the scheduled map refresh: a marker, scoped to the repo and the paths ----
    (repo / "architecture" / "map.txt").write_text("v2\n", encoding="utf-8")
    git(repo, "add", "architecture/map.txt")
    code, _o, err = drive(COMMIT, repo, env)
    check(code == 2, f"architecture/ staged on main is refused without a marker — got {code}")

    res = marker(env, "grant", repo, "--reason", "system-map", "--path", "architecture/")
    check(res.returncode == 0, f"grant succeeds — got {res.returncode} {res.stderr[:200]}")
    code, _o, err = drive(COMMIT, repo, env)
    check(code == 0, f"the granted refresh commits architecture/ on main — got {code} {err[:300]}")
    code, _o, err = drive(COMMIT, repo, env, dispatch=True)
    check(code == 0, f"the granted refresh also passes through hook_dispatch — got {code} {err[:300]}")

    (repo / "app.py").write_text("x = 2\n", encoding="utf-8")
    git(repo, "add", "app.py")
    code, _o, err = drive(COMMIT, repo, env)
    check(code == 2 and "app.py" in err and "architecture/" in err,
          f"a path outside the grant is refused, naming it — got {code} {err[:300]}")
    git(repo, "reset", "-q", "app.py")

    other = fresh()
    (other / "architecture" / "map.txt").write_text("v2\n", encoding="utf-8")
    git(other, "add", "architecture/map.txt")
    code, _o, err = drive(COMMIT, other, env)
    check(code == 2, f"one repo's grant does not cover another repo — got {code}")

    res = marker(env, "revoke", repo)
    check(res.returncode == 0, f"revoke succeeds — got {res.returncode} {res.stderr[:200]}")
    code, _o, err = drive(COMMIT, repo, env)
    check(code == 2, f"a revoked grant no longer covers the commit — got {code}")

    marker(env, "grant", repo, "--reason", "config-map", "--path", "architecture/", "--minutes", "1")
    files = list((state / "main-commit-allow").glob("*.json"))
    check(len(files) == 1, f"one marker file per repo — got {files}")
    row = json.loads(files[0].read_text(encoding="utf-8"))
    row["expires_at"] = time.time() - 5
    files[0].write_text(json.dumps(row), encoding="utf-8")
    code, _o, err = drive(COMMIT, repo, env)
    check(code == 2, f"an expired grant no longer covers the commit — got {code}")

    files[0].write_text("{not json", encoding="utf-8")
    code, _o, err = drive(COMMIT, repo, env)
    check(code == 2, f"a garbled marker grants nothing — got {code}")

    res = marker(env, "grant", repo, "--reason", "x", "--path", "architecture/", "--minutes", "600")
    check(res.returncode != 0, "a grant longer than an hour is refused")
    res = marker(env, "grant", repo, "--reason", "x")
    check(res.returncode != 0, "a grant with no path is refused")
    res = marker(env, "status", repo)
    check(res.returncode == 0 and "GRANT=none" in res.stdout, f"status reports no grant — got {res.stdout!r}")
finally:
    shutil.rmtree(state, ignore_errors=True)
    for path in made:
        shutil.rmtree(path, ignore_errors=True)

_h.report_and_exit("main_commit_guard")
