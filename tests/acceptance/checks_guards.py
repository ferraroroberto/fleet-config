"""Acceptance checks for the blocking + nudging guard hooks (fleet-config#680).

The hooks whose whole job is a verdict on someone else's tool call: the
`AskUserQuestion` block for chief-managed sessions, the `gh --body` and
cmd.exe-syntax nudges, the default-branch edit guard (driven against real temp
git repos and worktrees), `safe_kill_guard`'s force-push refusal, and the Tier
2/3 warn-hooks' stdout contract.

A guard that only *reports* a block is worse than no guard (CLAUDE.md), so these
assert the real exit code and the real stderr of a real subprocess -- never a
mocked decision. Split out of the former 2681-line `unit_checks.py`; see
`checks_context_filter` for why.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Tuple

from acceptance.shared import (
    HOOKS,
    REPO,
    _Checker,
    run,
)

# Every function below inserts its own sys.path entry (HOOKS or skills/_lib)
# right before its dynamic import -- matches the pre-split file's per-function
# style, so each check's dependency is visible at its own call site.


def _block_askuserquestion_chief_unit_checks() -> Tuple[int, int]:
    """`block_askuserquestion_chief.py` (fleet-config#463): drives the real
    hook subprocess against a temp `CLAUDE_HOOKS_STATE_DIR` carrying a
    `chief-managed.json` marker, so a managed sid's `AskUserQuestion` blocks
    (exit 2) while everything else -- an unmanaged sid, a non-`AskUserQuestion`
    tool, a missing `session_id`, and a corrupt state file -- fails open
    (exit 0), never stranding an ordinary session over a bad read.
    """
    check = _Checker()

    tmp = Path(tempfile.mkdtemp(prefix="block_askuserquestion_"))
    try:
        marker = tmp / "chief-managed.json"
        marker.write_text(json.dumps({
            "sid-managed": {"repo": "fleet-config", "number": 463,
                             "dispatched_at": "2026-07-27T12:00:00Z"},
        }), encoding="utf-8")
        env = {"CLAUDE_HOOKS_STATE_DIR": str(tmp)}

        code, _out, stderr = run(
            "block_askuserquestion_chief",
            {"tool_name": "AskUserQuestion", "session_id": "sid-managed"},
            extra_env=env,
        )
        check("block_askuserquestion: managed sid + AskUserQuestion -> block (exit 2)", code == 2)
        check("block_askuserquestion: block reason mentions the say/exchange fallback",
              "chief_ops.py say" in stderr or "say" in stderr.lower())

        code, _out, _err = run(
            "block_askuserquestion_chief",
            {"tool_name": "AskUserQuestion", "session_id": "sid-unmanaged"},
            extra_env=env,
        )
        check("block_askuserquestion: unmanaged sid -> allow (exit 0)", code == 0)

        code, _out, _err = run(
            "block_askuserquestion_chief",
            {"tool_name": "Bash", "session_id": "sid-managed"},
            extra_env=env,
        )
        check("block_askuserquestion: managed sid but non-AskUserQuestion tool -> allow (exit 0)", code == 0)

        code, _out, _err = run(
            "block_askuserquestion_chief",
            {"tool_name": "AskUserQuestion"},
            extra_env=env,
        )
        check("block_askuserquestion: missing session_id -> allow (exit 0)", code == 0)

        corrupt = tmp / "corrupt"
        corrupt.mkdir()
        corrupt_marker = corrupt / "chief-managed.json"
        corrupt_marker.write_text("{not json", encoding="utf-8")
        code, _out, _err = run(
            "block_askuserquestion_chief",
            {"tool_name": "AskUserQuestion", "session_id": "sid-managed"},
            extra_env={"CLAUDE_HOOKS_STATE_DIR": str(corrupt)},
        )
        check("block_askuserquestion: corrupt state file -> fail open, allow (exit 0)", code == 0)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    return check.failures, check.total


def _warn_channel_unit_checks() -> Tuple[int, int]:
    """`_lib.warn()` speaks a channel the model actually reads (fleet-config#681).

    Claude Code adds a hook's exit-0 **plain-text** stdout to the model's
    context only on `UserPromptSubmit`/`UserPromptExpansion`/`SessionStart`;
    everywhere else it goes to the debug log. Every one of this repo's seven
    nudge sites is `PreToolUse` or `PostToolUse`, so all seven were advising
    nobody — a guard that only reports. These drive real hooks with a real
    `hook_event_name` and assert the per-event JSON dialect, because "the
    nudge printed something" is exactly the assertion that stayed green
    throughout the bug.
    """
    check = _Checker()

    # PreToolUse: `systemMessage` — the one PreToolUse field documented as
    # "added to the conversation as context Claude can see" that does NOT also
    # decide whether the tool call runs (a nudge must stay advisory, so never
    # permissionDecision allow/ask).
    _code, out, _err = run("bash_cmdexe_syntax_guard", {
        "hook_event_name": "PreToolUse", "tool_name": "Bash",
        "tool_input": {"command": "echo %PATH%"},
    })
    try:
        pre = json.loads(out)
    except json.JSONDecodeError:
        pre = None
    check("warn: PreToolUse nudge is JSON, not plain stdout Claude never reads",
          isinstance(pre, dict))
    check("warn: PreToolUse nudge rides `systemMessage` (model-visible on that event)",
          isinstance(pre, dict) and "Nudge:" in str(pre.get("systemMessage", "")))
    check("warn: PreToolUse nudge is NOT a permissionDecision — it must not change whether the tool runs",
          isinstance(pre, dict) and "hookSpecificOutput" not in pre)

    # PostToolUse: `hookSpecificOutput.additionalContext`.
    tmp = Path(tempfile.mkdtemp(prefix="warn_channel_"))
    try:
        target = tmp / "wrapper.py"
        target.write_text(
            "import subprocess\nsubprocess.run(['claude', '-p', 'hi'])\n",
            encoding="utf-8")
        _code, out, _err = run("hub_bypass_warn", {
            "hook_event_name": "PostToolUse", "tool_name": "Write",
            "tool_input": {"file_path": str(target)},
        })
        try:
            post = json.loads(out)
        except json.JSONDecodeError:
            post = None
        hso = post.get("hookSpecificOutput", {}) if isinstance(post, dict) else {}
        check("warn: PostToolUse nudge rides hookSpecificOutput.additionalContext",
              "Nudge:" in str(hso.get("additionalContext", "")))
        check("warn: PostToolUse nudge stamps the matching hookEventName",
              hso.get("hookEventName") == "PostToolUse")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # Both remain non-blocking: a warn is advice, never a refusal.
    check("warn: the PreToolUse nudge still exits 0 (advisory, not a block)", _code == 0)

    # A foreign harness keeps the bare-stdout form — Claude's JSON protocol is
    # Claude's. Grok's tell is `hookEventName` + camelCase tool ids.
    _code, out, _err = run("bash_cmdexe_syntax_guard", {
        "hookEventName": "pre_tool_use", "toolName": "run_terminal_command",
        "toolInput": {"command": "echo %PATH%"},
    })
    check("warn: a Grok-shaped payload still gets the bare-stdout nudge, not Claude JSON",
          _code == 0 and out.strip().startswith("Nudge:"))

    return check.failures, check.total


def _gh_body_file_guard_unit_checks() -> Tuple[int, int]:
    """The warn-only nudge fires on the two payload traps and stays silent
    otherwise. Exit is always 0, so these assert on STDOUT, not the exit code:
    a nudge present (non-empty stdout) for the risky forms, empty for the safe
    ones."""
    check = _Checker()

    def stdout_for(command: str) -> str:
        code, out, _err = run("gh_body_file_guard", {"tool_name": "Bash", "tool_input": {"command": command}})
        # warn-only: the hook must never block (exit non-zero) regardless of input.
        return out.strip() if code == 0 else f"__NONZERO_EXIT_{code}__"

    check("gh_guard: gh pr create --body with backtick -> nudge",
          bool(stdout_for('gh pr create --title x --body "see `uname -a`"')))
    check("gh_guard: gh issue comment --body with heredoc -> nudge",
          bool(stdout_for('gh issue comment 5 --body "$(cat <<EOF\nhi\nEOF\n)"')))
    check("gh_guard: PowerShell here-string through Bash -> nudge",
          bool(stdout_for("printf '%s' @'\nhello\n'@")))
    check("gh_guard: gh pr create --body-file -> silent",
          stdout_for("gh pr create --title x --body-file E:/tmp/pr-116.md") == "")
    check("gh_guard: gh issue list (read) -> silent",
          stdout_for("gh issue list --state open --limit 20") == "")
    check("gh_guard: gh pr create plain inline body (no risky construct) -> silent",
          stdout_for('gh pr create --title x --body "plain text, nothing to expand"') == "")

    return check.failures, check.total


def _bash_cmdexe_syntax_guard_unit_checks() -> Tuple[int, int]:
    """The guard blocks MSYS-mangled cmd /c, nudges cmd-only syntax, and stays
    silent on Bash-native or explicitly MSYS-safe equivalents."""
    check = _Checker()

    def stdout_for(command: str) -> str:
        code, out, _err = run("bash_cmdexe_syntax_guard", {"tool_name": "Bash", "tool_input": {"command": command}})
        # These legacy syntax checks remain warn-only; cmd.exe /c is exercised
        # separately below because that caller shape is now a hard block.
        return out.strip() if code == 0 else f"__NONZERO_EXIT_{code}__"

    code, out, err = run(
        "bash_cmdexe_syntax_guard",
        {"tool_name": "Bash", "tool_input": {"command": 'cmd.exe /c "tray.bat --restart" 2>&1'}},
    )
    check("cmdexe_guard: Bash cmd.exe /c tray restart -> block with root cause",
          code == 2 and not out and "C:/" in err and "PowerShell" in err,
          out + err)

    code, _out, _err = run(
        "bash_cmdexe_syntax_guard",
        {"tool_name": "Bash", "tool_input": {"command": 'cmd.exe /d /s /c "echo safe"'}},
    )
    check("cmdexe_guard: Bash cmd.exe with leading flags then /c -> block", code == 2)

    code, out, err = run(
        "bash_cmdexe_syntax_guard",
        {"tool_name": "Bash", "tool_input": {"command": 'cmd.exe //d //c "echo safe"'}},
    )
    check("cmdexe_guard: Bash cmd.exe //c MSYS-safe spelling -> silent allow",
          code == 0 and not out and not err, out + err)

    code, out, err = run(
        "bash_cmdexe_syntax_guard",
        {"tool_name": "Bash", "tool_input": {"command": 'rg -n "cmd.exe /c" skills'}},
    )
    check("cmdexe_guard: quoted search text containing cmd.exe /c -> silent allow",
          code == 0 and not out and not err, out + err)

    code, out, err = run(
        "bash_cmdexe_syntax_guard",
        {"tool_name": "PowerShell", "tool_input": {"command": 'cmd.exe /c "tray.bat --restart"'}},
    )
    check("cmdexe_guard: PowerShell cmd.exe /c -> outside Bash guard",
          code == 0 and not out and not err, out + err)

    check("cmdexe_guard: %VAR% env reference -> nudge",
          bool(stdout_for("echo %USERPROFILE%")))
    check("cmdexe_guard: dir /s -> nudge",
          bool(stdout_for("dir /s")))
    check("cmdexe_guard: del /f -> nudge",
          bool(stdout_for("del /f file.txt")))
    check("cmdexe_guard: caret line-continuation -> nudge",
          bool(stdout_for("echo hello ^\necho world")))
    check("cmdexe_guard: printf %s (bare percent, no close) -> silent",
          stdout_for('printf "%s\\n" hello') == "")
    check("cmdexe_guard: URL path with /s (no cmd builtin) -> silent",
          stdout_for("curl https://example.com/s/path") == "")
    check("cmdexe_guard: date +%Y%m%d (single-letter format run) -> silent",
          stdout_for("date +%Y%m%d") == "")
    check("cmdexe_guard: plain git log -> silent",
          stdout_for("git log --oneline") == "")

    # issue-yolo's own Phase 4 delegates tray-restart mechanics to
    # `/issue-finish` verbatim rather than restating them (fleet-config#728),
    # so the rule now lives in issue-finish's text; check the combined text
    # an agent following a YOLO run actually reads across both files.
    yolo_skill = (REPO / "skills" / "issue-yolo" / "SKILL.md").read_text(encoding="utf-8")
    finish_skill = (REPO / "skills" / "issue-finish" / "SKILL.md").read_text(encoding="utf-8")
    combined_flat = re.sub(r"\s+", " ", (yolo_skill + finish_skill).replace("**", ""))
    check("cmdexe_guard: issue-yolo (via its delegation to issue-finish) mandates "
          "a real Windows shell for tray restart",
          "real Windows shell" in combined_flat and "cmd /c" in combined_flat)

    return check.failures, check.total


def _branch_before_edit_guard_unit_checks() -> Tuple[int, int]:
    """branch_before_edit_guard.py: real temp git repos/worktrees on
    main/master/a feature branch, crossed with APP_LAUNCHER_SESSION_ID
    presence and the CLAUDE_HOOKS_ALLOW_MAIN_EDIT override (fleet-config#464,
    take 2). Every fixture below deliberately sets `cwd` and the edit
    `file_path`'s directory to *different* paths — the take-1 guard resolved
    the branch from `cwd` and was reverted for exactly the false positives
    that shape hides: a worktree worker judged by the primary checkout's
    branch, and a write outside any repo blocked by the session's cwd repo.
    None of these fixtures configure a git remote, so the master-branch case
    also proves `resolve_default_branch_ref`'s candidate probing (not
    `dirty_tree_check`'s `candidates=()` variant) still detects `master` as
    the protected branch with no `origin` configured. The gitignored-target
    fixtures cover take 2's own false positive (fleet-config#489) and pin the
    exemption to ignored paths only."""
    sys.path.insert(0, str(HOOKS))
    import _lib  # noqa: E402

    check = _Checker()
    launcher_env = {"APP_LAUNCHER_SESSION_ID": "launcher-test"}

    def git_repo(branch: str) -> Path:
        repo = Path(tempfile.mkdtemp(prefix="branch_guard_"))
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True, creationflags=_lib.NO_WINDOW)
        subprocess.run(
            ["git", "config", "user.email", "35553560+ferraroroberto@users.noreply.github.com"],
            cwd=repo, check=True, creationflags=_lib.NO_WINDOW,
        )
        subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True, creationflags=_lib.NO_WINDOW)
        subprocess.run(["git", "checkout", "-q", "-b", branch], cwd=repo, check=True, creationflags=_lib.NO_WINDOW)
        subprocess.run(
            ["git", "commit", "-q", "--allow-empty", "-m", "init"],
            cwd=repo, check=True, creationflags=_lib.NO_WINDOW,
        )
        (repo / "sub").mkdir(exist_ok=True)
        return repo

    def edit_payload(cwd: Path, target_dir: Path, tool: str = "Edit") -> Dict[str, Any]:
        # cwd (session dir) and the edit target's directory are deliberately
        # different paths — see the docstring above.
        return {"tool_name": tool, "cwd": str(cwd), "tool_input": {"file_path": str(target_dir / "f.py")}}

    main_repo = git_repo("main")
    try:
        code, _out, err = run(
            "branch_before_edit_guard", edit_payload(main_repo, main_repo / "sub"), extra_env=launcher_env
        )
        check("branch_guard: main (target != cwd dir) + launcher env -> block", code == 2, err)

        # Explicit empty-string override (not just an omitted extra_env) --
        # the ambient session this suite runs under may itself carry a real
        # APP_LAUNCHER_SESSION_ID, which `run()` would otherwise pass through.
        code, _out, _err = run(
            "branch_before_edit_guard", edit_payload(main_repo, main_repo / "sub"),
            extra_env={"APP_LAUNCHER_SESSION_ID": ""},
        )
        check("branch_guard: main + no launcher env -> allow", code == 0)

        code, _out, _err = run(
            "branch_before_edit_guard", edit_payload(main_repo, main_repo / "sub"),
            extra_env={**launcher_env, "CLAUDE_HOOKS_ALLOW_MAIN_EDIT": "1"},
        )
        check("branch_guard: main + launcher env + override -> allow", code == 0)

        code, _out, err = run(
            "branch_before_edit_guard", edit_payload(main_repo, main_repo / "sub", tool="Write"), extra_env=launcher_env
        )
        check("branch_guard: Write tool covered same as Edit -> block", code == 2, err)

        code, _out, err = run(
            "branch_before_edit_guard", edit_payload(main_repo, main_repo / "sub", tool="MultiEdit"), extra_env=launcher_env
        )
        check("branch_guard: MultiEdit tool covered same as Edit -> block", code == 2, err)

        code, _out, _err = run(
            "branch_before_edit_guard", edit_payload(main_repo, main_repo / "sub", tool="Bash"), extra_env=launcher_env
        )
        check("branch_guard: Bash tool_name -> allow (only guards Edit/Write/MultiEdit)", code == 0)

        # ---- take-1's actual bug: a worktree worker judged by the primary's branch ----
        worktree = main_repo.parent / f"{main_repo.name}-wt-1"
        subprocess.run(
            ["git", "worktree", "add", "-q", "-b", "feat/464-x", str(worktree)],
            cwd=main_repo, check=True, creationflags=_lib.NO_WINDOW,
        )
        try:
            # cwd is the PRIMARY repo (still on main) -- the exact shape that
            # broke take 1. file_path targets the worktree, on its own branch.
            code, _out, _err = run(
                "branch_before_edit_guard", edit_payload(main_repo, worktree), extra_env=launcher_env
            )
            check("branch_guard: worktree target on feature branch, cwd=primary(main) -> allow", code == 0)
        finally:
            subprocess.run(
                ["git", "worktree", "remove", "-f", str(worktree)],
                cwd=main_repo, check=False, creationflags=_lib.NO_WINDOW,
            )

        # ---- take-2's own bug (fleet-config#489): a gitignored target *inside*
        # the repo, on the default branch. Both live repros are covered: a
        # single-file rule (life-os's `.active-skill`) and a directory rule
        # (fleet-config's `hooks/state/`, reached by the chief through a
        # junction). Neither file exists on disk -- `check-ignore` matches the
        # pathname, which is what makes a creating `Write` resolve correctly.
        (main_repo / ".gitignore").write_text(".active-skill\nstate/\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=main_repo, check=True, creationflags=_lib.NO_WINDOW)
        subprocess.run(
            ["git", "commit", "-q", "-m", "ignore rules"],
            cwd=main_repo, check=True, creationflags=_lib.NO_WINDOW,
        )
        (main_repo / "state").mkdir(exist_ok=True)

        def payload_for(target: Path) -> Dict[str, Any]:
            return {"tool_name": "Write", "cwd": str(main_repo), "tool_input": {"file_path": str(target)}}

        code, _out, _err = run(
            "branch_before_edit_guard", payload_for(main_repo / ".active-skill"), extra_env=launcher_env
        )
        check("branch_guard: gitignored file target on main + launcher env -> allow", code == 0)

        code, _out, _err = run(
            "branch_before_edit_guard", payload_for(main_repo / "state" / "chief-handover.md"),
            extra_env=launcher_env,
        )
        check("branch_guard: target under a gitignored directory rule -> allow", code == 0)

        # The exemption is gitignored-only: an untracked, non-ignored new file
        # in the same repo can still be committed to main, so it must block.
        code, _out, err = run(
            "branch_before_edit_guard", payload_for(main_repo / "state.py"), extra_env=launcher_env
        )
        check("branch_guard: untracked but NOT ignored target on main -> still block", code == 2, err)

        # ---- the junction shape (fleet-config#489's second live repro) ----
        # `~/.claude/hooks/` is a junction into this repo, so the chief's write
        # to its gitignored handover file arrives spelled under the junction.
        # git follows a junction for `-C` but matches the *pathname argument*
        # lexically against the worktree root, so the unresolved spelling exits
        # 128 ("is outside repository at ...") -- the fail-closed path. Only
        # the guard's `target.resolve()` keeps this case allowed.
        if sys.platform == "win32":
            link = main_repo.parent / f"{main_repo.name}-junction"
            mk = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link), str(main_repo)],
                capture_output=True, text=True, creationflags=_lib.NO_WINDOW,
            )
            check("branch_guard: junction fixture created", mk.returncode == 0, mk.stdout + mk.stderr)
            if mk.returncode == 0:
                try:
                    code, _out, _err = run(
                        "branch_before_edit_guard",
                        payload_for(link / "state" / "chief-handover.md"), extra_env=launcher_env,
                    )
                    check("branch_guard: gitignored target via a junction path -> allow", code == 0)

                    code, _out, err = run(
                        "branch_before_edit_guard",
                        payload_for(link / "tracked.py"), extra_env=launcher_env,
                    )
                    check("branch_guard: non-ignored target via a junction path -> still block", code == 2, err)
                finally:
                    subprocess.run(
                        ["cmd", "/c", "rmdir", str(link)],
                        capture_output=True, creationflags=_lib.NO_WINDOW,
                    )

        # ---- take-1's other bug: a write target entirely outside any repo ----
        non_repo = Path(tempfile.mkdtemp(prefix="branch_guard_norepo_"))
        try:
            # cwd is a repo on main (e.g. chief's cwd); file_path targets a
            # plain non-git directory (e.g. E:\tmp\chief).
            code, _out, _err = run(
                "branch_before_edit_guard", edit_payload(main_repo, non_repo), extra_env=launcher_env
            )
            check("branch_guard: non-git target dir, cwd=repo(main) -> allow (fail open)", code == 0)
        finally:
            shutil.rmtree(non_repo, ignore_errors=True)
    finally:
        shutil.rmtree(main_repo, ignore_errors=True)

    master_repo = git_repo("master")
    try:
        code, _out, err = run(
            "branch_before_edit_guard", edit_payload(master_repo, master_repo / "sub"), extra_env=launcher_env
        )
        check("branch_guard: master (no origin configured) + launcher env -> block", code == 2, err)
    finally:
        shutil.rmtree(master_repo, ignore_errors=True)

    feature_repo = git_repo("feat/464-x")
    try:
        code, _out, _err = run(
            "branch_before_edit_guard", edit_payload(feature_repo, feature_repo / "sub"), extra_env=launcher_env
        )
        check("branch_guard: feature branch + launcher env -> allow", code == 0)
    finally:
        shutil.rmtree(feature_repo, ignore_errors=True)

    return check.failures, check.total


def _safe_kill_force_push_unit_checks() -> Tuple[int, int]:
    """The force-push guard decides on the *ref being pushed* (fleet-config#562).

    The predicate used to be a word-boundary search for `main`/`master` across
    the whole command line, so `git push --force origin
    chore/rename-main-config-loader` was refused — a legitimate feature-branch
    force-push the module's own docstring promises to allow. A fleet-wide guard
    blocking valid work is the expensive kind of wrong (#464/#472 reverted a
    hook within the hour for it), so the branch-name-contains-main case is
    pinned here alongside the protections it must not weaken.
    """
    sys.path.insert(0, str(HOOKS))
    import safe_kill_guard as skg  # noqa: E402

    check = _Checker()
    push = "git " + "push"  # split so this file's own text isn't a force-push line
    here = REPO

    def blocked(cmd: str) -> bool:
        return skg.forced_push_hits_protected(cmd, here)

    check("force-push: --force origin main -> blocked", blocked(f"{push} --force origin main"))
    check("force-push: -f origin master -> blocked", blocked(f"{push} -f origin master"))
    check("force-push: --force-with-lease origin HEAD:main -> blocked",
          blocked(f"{push} --force-with-lease origin HEAD:main"))
    check("force-push: +refs/heads/master refspec -> blocked",
          blocked(f"{push} --force origin +refs/heads/master"))
    check("force-push: short-flag cluster -fu origin main -> blocked",
          blocked(f"{push} -fu origin main"))
    check("force-push: chained after another command -> blocked",
          blocked(f"git status && {push} --force origin main"))

    check("force-push: feature branch -> allowed", not blocked(f"{push} --force origin feature/foo"))
    check("force-push: branch whose NAME contains 'main' -> allowed (the #562 false positive)",
          not blocked(f"{push} --force origin chore/rename-main-config-loader"))
    check("force-push: branch whose name contains 'master' -> allowed",
          not blocked(f"{push} --force origin fix/12-master-list-parser"))
    check("force-push: a src-side 'main' pushed onto a feature ref -> allowed",
          not blocked(f"{push} --force origin main:feature/staging-main"))
    check("force-push: no force flag -> not a forced push at all",
          skg.forced_push_refspecs(f"{push} origin main") is None)
    check("force-push: --foo is not the short -f (no false positive)",
          skg.forced_push_refspecs(f"{push} --foo origin main") is None)

    check("force-push: refspec-less push reports an empty list, not a guess",
          skg.forced_push_refspecs(f"{push} --force origin") == [])
    check("destination_branch: strips + and refs/heads/, keeps the dst side",
          skg.destination_branch("+refs/heads/main") == "main"
          and skg.destination_branch("HEAD:refs/heads/master") == "master"
          and skg.destination_branch("feature/x") == "feature/x")

    # Refspec-less force push falls back to the checked-out branch of `cwd`.
    tmp = Path(tempfile.mkdtemp(prefix="fc-push-"))
    try:
        subprocess.run(["git", "-C", str(tmp), "init", "-b", "main"], capture_output=True)
        check("force-push: refspec-less push on main -> blocked via the checked-out branch",
              skg.forced_push_hits_protected(f"{push} --force origin", tmp))
        subprocess.run(["git", "-C", str(tmp), "checkout", "-b", "feat/x"], capture_output=True)
        check("force-push: refspec-less push on a feature branch -> allowed",
              not skg.forced_push_hits_protected(f"{push} --force origin", tmp))
        check("_current_branch: unresolvable cwd reports '' (fails open, never guesses)",
              skg._current_branch(tmp / "not-a-repo-here") == "")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    return check.failures, check.total


def _tier23_hooks_unit_checks() -> Tuple[int, int]:
    """The three Tier 2/3 hooks (issue #158): docs-guard env override, plus the
    two warn-only hooks whose output is on STDOUT (exit always 0), so these
    assert nudge-present / silent rather than the exit code. The warn hooks read
    the file from disk, so each case writes a real temp file first.
    """
    check = _Checker()

    # ---- docs_dated_filename_guard: env override flips block -> allow ----
    os.environ["CLAUDE_HOOKS_ALLOW_DATED_DOCS"] = "1"
    try:
        code, _out, _err = run("docs_dated_filename_guard",
                               {"tool_name": "Write",
                                "tool_input": {"file_path": "E:/automation/foo/docs/2026-06-18-retro.md"}})
        check("docs_guard: CLAUDE_HOOKS_ALLOW_DATED_DOCS=1 -> allow (override)", code == 0)
    finally:
        os.environ.pop("CLAUDE_HOOKS_ALLOW_DATED_DOCS", None)

    tmp = Path(tempfile.mkdtemp(prefix="tier23_"))
    try:
        def nudged(hook: str, path: Path, body: str, extra_env: Dict[str, str] | None = None) -> bool:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
            code, out, _err = run(hook, {"tool_name": "Write", "tool_input": {"file_path": str(path)}},
                                   extra_env=extra_env)
            return code == 0 and bool(out.strip())

        # ---- hub_bypass_warn ----
        check("hub_bypass: inline `claude -p` command string -> nudge",
              nudged("hub_bypass_warn", tmp / "wrapper.py",
                     'import subprocess\nsubprocess.run("claude -p hello", shell=True)\n'))
        check("hub_bypass: argv-form ['claude','-p'] -> nudge",
              nudged("hub_bypass_warn", tmp / "argv.py",
                     'from subprocess import Popen\nPopen(["claude", "-p", "hi"])\n'))
        check("hub_bypass: subprocess but no claude -p -> silent",
              not nudged("hub_bypass_warn", tmp / "other.py",
                         'import subprocess\nsubprocess.run(["ls", "-la"])\n'))
        # Points hub_bypass_warn.py at a throwaway projects.toml (via
        # CLAUDE_HOOKS_PROJECTS_TOML) flagging tmp/local-llm-hub as `is_hub`,
        # so the exemption is exercised through the real cwd_prefix-match path
        # instead of a hardcoded directory-name check.
        hub_projects_toml = tmp / "hub_projects.toml"
        hub_projects_toml.write_text(
            '[hub]\ncwd_prefix = "%s"\nis_hub = true\n' % (tmp / "local-llm-hub").as_posix(),
            encoding="utf-8",
        )
        check("hub_bypass: inside a repo flagged is_hub in projects.toml -> silent",
              not nudged("hub_bypass_warn", tmp / "local-llm-hub" / "server.py",
                         'import subprocess\nsubprocess.run("claude -p hello", shell=True)\n',
                         extra_env={"CLAUDE_HOOKS_PROJECTS_TOML": str(hub_projects_toml)}))

        # ---- browser_stealth_lint ----
        bare_launch = 'ctx = p.chromium.launch_persistent_context(user_data_dir="x")\n'
        full_launch = (
            'ctx = p.chromium.launch_persistent_context(\n'
            '    user_data_dir="x", channel="chrome",\n'
            '    ignore_default_args=["--enable-automation"],\n'
            '    args=["--disable-blink-features=AutomationControlled"],\n'
            ')\n'
            'page.add_init_script("Object.defineProperty(navigator, \'webdriver\', {get: () => undefined})")\n'
        )
        check("browser_stealth: chrome_launch.py missing markers -> nudge",
              nudged("browser_stealth_lint", tmp / "chrome_launch.py", bare_launch))
        check("browser_stealth: chrome_launch.py with all markers -> silent",
              not nudged("browser_stealth_lint", tmp / "ok_launch" / "chrome_launch.py", full_launch))
        check("browser_stealth: *_session.py with a launch missing a marker -> nudge",
              nudged("browser_stealth_lint", tmp / "x_session.py", bare_launch + 'channel="chrome"\n'))
        check("browser_stealth: watched name but no launch call -> silent",
              not nudged("browser_stealth_lint", tmp / "browser.py", "PORT = 9222\n"))
        check("browser_stealth: non-watched filename with a launch -> silent",
              not nudged("browser_stealth_lint", tmp / "helper.py", bare_launch))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    return check.failures, check.total
