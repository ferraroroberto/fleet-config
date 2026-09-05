"""Codex policy wiring behavior: real hook subprocesses, no tool execution."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

REPO = Path(__file__).resolve().parent.parent
HOOKS = REPO / "hooks"
sys.path.insert(0, str(HOOKS))
import _lib  # noqa: E402
from probe_codex_policies import (  # noqa: E402
    PROJECT_CONTROL_MARKER,
    PROJECT_CONTROL_OBSERVATIONS,
    project_control_observed,
    successful_file_change_observed,
)


failures = 0


def check(ok: bool, label: str, detail: str = "") -> None:
    global failures
    print(f"{'OK   ' if ok else 'FAIL '} {label}")
    if not ok:
        failures += 1
        if detail:
            print("       ", detail.replace("\n", "\n        "))


def patch(*entries: tuple[str, str]) -> str:
    lines = ["*** Begin Patch"]
    for path, content in entries:
        lines.append(f"*** Add File: {path}")
        lines.extend("+" + line for line in content.splitlines())
    lines.append("*** End Patch")
    return "\n".join(lines)


def drive(hook: str, payload: dict, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """Run a worktree hook while preserving trusted Codex invocation provenance."""
    driver = (
        "import importlib,sys; hook_dir,module,entry=sys.argv[1:4]; "
        "sys.path.insert(0,hook_dir); sys.argv=[entry]; importlib.import_module(module).main()"
    )
    entry = f"C:/Users/test/.codex/hooks/{hook}.py"
    env = {k: v for k, v in os.environ.items() if k != "TELEGRAM_BOT_TOKEN"}
    env.update({"APP_LAUNCHER_SESSION_ID": "", "CLAUDE_SETTINGS_JSON_PATH":
                str(Path(tempfile.gettempdir()) / "fleet-config-test-no-settings.json")})
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-c", driver, str(HOOKS), hook, entry],
        input=json.dumps(payload), capture_output=True, text=True, timeout=20,
        env=env, creationflags=_lib.NO_WINDOW,
    )


def output_json(result: subprocess.CompletedProcess) -> dict:
    try:
        value = json.loads(result.stdout or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def pre_payload(tool: str, tool_input: dict, cwd: Path) -> dict:
    return {"hook_event_name": "PreToolUse", "tool_name": tool,
            "tool_input": tool_input, "cwd": str(cwd)}


def post_patch_payload(command: str, cwd: Path) -> dict:
    return {"hook_event_name": "PostToolUse", "tool_name": "apply_patch",
            "tool_input": {"command": command},
            "tool_response": "Success. Updated the following files:\nA files",
            "cwd": str(cwd)}


root = Path(tempfile.mkdtemp(prefix="codex_policy_"))
try:
    # Project-only control classification must require both invocation and
    # effect evidence; absence or malformed observation is never suppression.
    project_control = root / "project-control"
    project_control.mkdir()
    (project_control / PROJECT_CONTROL_MARKER).write_text(
        "project_only_deny\n", encoding="utf-8")
    observation = {
        "module": "project_only_deny",
        "hook_event_name": "PreToolUse",
        "tool_name": "apply_patch",
    }
    (project_control / PROJECT_CONTROL_OBSERVATIONS).write_text(
        json.dumps(observation) + "\n", encoding="utf-8")
    check(project_control_observed(project_control),
          "Project-only control: valid invocation/effect evidence is accepted")
    (project_control / PROJECT_CONTROL_OBSERVATIONS).unlink()
    check(not project_control_observed(project_control),
          "Project-only control: missing invocation evidence stays unverified")
    (project_control / PROJECT_CONTROL_OBSERVATIONS).write_text(
        "not-json\n", encoding="utf-8")
    check(not project_control_observed(project_control),
          "Project-only control: malformed invocation evidence stays unverified")
    target = project_control / "project-only-control.txt"
    completed_change = json.dumps({
        "type": "item.completed",
        "item": {"type": "file_change", "status": "completed",
                  "changes": [{"path": str(target), "kind": "add"}]},
    })
    started_change = json.dumps({
        "type": "item.started",
        "item": {"type": "file_change", "status": "in_progress",
                  "changes": [{"path": str(target), "kind": "add"}]},
    })
    check(successful_file_change_observed(started_change + "\n" + completed_change, target),
          "Project-only control: started then completed file effect is accepted")
    check(not successful_file_change_observed(started_change, target),
          "Project-only control: started-only file effect stays unverified")
    check(not successful_file_change_observed("", target),
          "Project-only control: missing tool result stays unverified")
    check(not successful_file_change_observed("not-json\n" + completed_change, target),
          "Project-only control: malformed tool result stays unverified")

    # Command policy: use Codex's observed tool_input.command envelope.
    risky_gh = drive("gh_body_file_guard", pre_payload(
        "Bash", {"command": 'gh pr create --body "see `uname -a`"'}, root))
    risky_wire = output_json(risky_gh)
    risky_context = risky_wire.get("hookSpecificOutput", {})
    check(risky_gh.returncode == 0 and risky_context.get("hookEventName") == "PreToolUse"
          and "Nudge:" in risky_context.get("additionalContext", ""),
          "Codex gh-body positive: advisory reaches the model", risky_gh.stdout + risky_gh.stderr)
    safe_gh = drive("gh_body_file_guard", pre_payload(
        "Bash", {"command": "gh pr create --body-file E:/tmp/pr.md"}, root))
    check(safe_gh.returncode == 0 and not safe_gh.stdout and not safe_gh.stderr,
          "Codex gh-body negative: safe body-file command stays silent")

    # Dated docs: put the violation second to prove every patch target is read.
    dated = patch(("notes.txt", "safe"), ("docs/2026-09-05-retro.md", "bad"))
    dated_result = drive("docs_dated_filename_guard",
                         pre_payload("apply_patch", {"command": dated}, root))
    dated_hso = output_json(dated_result).get("hookSpecificOutput", {})
    check(dated_result.returncode == 0 and dated_hso.get("permissionDecision") == "deny"
          and "2026-09-05-retro.md" in dated_hso.get("permissionDecisionReason", ""),
          "Codex dated-docs positive: second patch target is denied", dated_result.stdout)
    durable_result = drive("docs_dated_filename_guard", pre_payload(
        "apply_patch", {"command": patch(("docs/hook-policies.md", "safe"),
                                           ("notes.txt", "safe"))}, root))
    check(durable_result.returncode == 0 and not durable_result.stdout,
          "Codex dated-docs negative: topic-named docs are allowed")

    # Branch policy: a real main/feature repo, with the main violation second.
    repo = root / "repo"
    repo.mkdir()
    for args in (("init", "-q", "-b", "main"),
                 ("config", "user.email", "test@example.com"),
                 ("config", "user.name", "test")):
        subprocess.run(["git", *args], cwd=repo, check=True, creationflags=_lib.NO_WINDOW)
    isolated_hooks = repo / ".test-hooks"
    isolated_hooks.mkdir()
    subprocess.run(["git", "config", "core.hooksPath", str(isolated_hooks)], cwd=repo,
                   check=True, creationflags=_lib.NO_WINDOW)
    (repo / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    subprocess.run(["git", "add", ".gitignore"], cwd=repo, check=True,
                   creationflags=_lib.NO_WINDOW)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True,
                   creationflags=_lib.NO_WINDOW)
    branch_patch = patch(("ignored.txt", "safe"), ("tracked.py", "VALUE = 1"))
    branch_result = drive("branch_before_edit_guard",
                          pre_payload("apply_patch", {"command": branch_patch}, repo),
                          {"APP_LAUNCHER_SESSION_ID": "codex-test"})
    branch_hso = output_json(branch_result).get("hookSpecificOutput", {})
    check(branch_result.returncode == 0 and branch_hso.get("permissionDecision") == "deny",
          "Codex branch positive: second main-tree patch target is denied", branch_result.stdout)
    subprocess.run(["git", "checkout", "-q", "-b", "feat/test"], cwd=repo, check=True,
                   creationflags=_lib.NO_WINDOW)
    feature_result = drive("branch_before_edit_guard",
                           pre_payload("apply_patch", {"command": branch_patch}, repo),
                           {"APP_LAUNCHER_SESSION_ID": "codex-test"})
    check(feature_result.returncode == 0 and not feature_result.stdout,
          "Codex branch negative: feature-branch patch is allowed")

    # Post-edit policies inspect every surviving target after a confirmed patch.
    (root / "safe.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "wrapper.py").write_text(
        "import subprocess\nsubprocess.run(['claude', '-p', 'hi'])\n", encoding="utf-8")
    hub_patch = patch(("safe.py", "VALUE = 1"),
                      ("wrapper.py", "import subprocess\nsubprocess.run(['claude', '-p', 'hi'])"))
    hub_result = drive("hub_bypass_warn", post_patch_payload(hub_patch, root))
    hub_context = output_json(hub_result).get("hookSpecificOutput", {})
    check(hub_result.returncode == 0 and hub_context.get("hookEventName") == "PostToolUse"
          and "wrapper.py" in hub_context.get("additionalContext", ""),
          "Codex hub positive: second patch target advises through PostToolUse context",
          hub_result.stdout)
    safe_hub = drive("hub_bypass_warn", post_patch_payload(
        patch(("safe.py", "VALUE = 1"), ("other.py", "VALUE = 2")), root))
    check(safe_hub.returncode == 0 and not safe_hub.stdout,
          "Codex hub negative: ordinary Python patch stays silent")

    bare_launch = 'ctx = p.chromium.launch_persistent_context(user_data_dir="x")\n'
    full_launch = (
        'ctx = p.chromium.launch_persistent_context(user_data_dir="x", channel="chrome", '
        'ignore_default_args=["--enable-automation"], '
        'args=["--disable-blink-features=AutomationControlled"])\n'
        'page.add_init_script("Object.defineProperty(navigator, \'webdriver\', {get: () => undefined})")\n'
    )
    (root / "helper.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "browser.py").write_text(bare_launch, encoding="utf-8")
    browser_result = drive("browser_stealth_lint", post_patch_payload(
        patch(("helper.py", "VALUE = 1"), ("browser.py", bare_launch.rstrip())), root))
    browser_context = output_json(browser_result).get("hookSpecificOutput", {})
    check(browser_result.returncode == 0 and browser_context.get("hookEventName") == "PostToolUse"
          and "browser.py" in browser_context.get("additionalContext", ""),
          "Codex browser positive: second patch target advises through PostToolUse context",
          browser_result.stdout)
    (root / "browser.py").write_text(full_launch, encoding="utf-8")
    safe_browser = drive("browser_stealth_lint", post_patch_payload(
        patch(("helper.py", "VALUE = 1"), ("browser.py", full_launch.rstrip())), root))
    check(safe_browser.returncode == 0 and not safe_browser.stdout,
          "Codex browser negative: complete stealth launch stays silent")

    # Native Claude and Grok compatibility remain on their original edit path.
    claude = subprocess.run(
        [sys.executable, str(HOOKS / "docs_dated_filename_guard.py")],
        input=json.dumps(pre_payload("Write", {"file_path": str(root / "docs" / "2026-09-05-x.md")}, root)),
        capture_output=True, text=True, timeout=20, creationflags=_lib.NO_WINDOW,
    )
    check(claude.returncode == 2 and not claude.stdout and "Blocked:" in claude.stderr,
          "Claude native Write keeps exit-2/stderr blocking behavior")
    grok = subprocess.run(
        [sys.executable, str(HOOKS / "docs_dated_filename_guard.py")],
        input=json.dumps({"hookEventName": "pre_tool_use", "toolName": "search_replace",
                          "toolInput": {"file_path": str(root / "docs" / "2026-09-05-x.md")},
                          "cwd": str(root)}), capture_output=True, text=True, timeout=20,
        creationflags=_lib.NO_WINDOW,
    )
    check(grok.returncode == 2 and output_json(grok).get("decision") == "deny",
          "Grok Claude-compat Write keeps structured denial")
finally:
    shutil.rmtree(root, ignore_errors=True)

print(f"Total: 19 | Failed: {failures}")
sys.exit(1 if failures else 0)
