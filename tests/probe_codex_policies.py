"""Opt-in live Codex probe for the command/edit policies wired in #745."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks"))
from _lib import NO_WINDOW, run_git  # noqa: E402

POLICY_HOOKS = (
    ("PreToolUse", "Bash", "gh_body_file_guard"),
    ("PreToolUse", "Edit|Write|MultiEdit", "docs_dated_filename_guard"),
    ("PreToolUse", "Edit|Write|MultiEdit", "branch_before_edit_guard"),
    ("PostToolUse", "Edit|Write|MultiEdit", "hub_bypass_warn"),
    ("PostToolUse", "Edit|Write|MultiEdit", "browser_stealth_lint"),
)

FEATURE_PATCH = """*** Begin Patch
*** Add File: safe.py
+VALUE = 1
*** Add File: wrapper.py
+import subprocess
+subprocess.run(["claude", "-p", "hi"])
*** Add File: browser.py
+ctx = p.chromium.launch_persistent_context(user_data_dir="x")
*** Add File: docs/hook-policy.md
+safe
*** End Patch"""

DATED_PATCH = """*** Begin Patch
*** Add File: docs/2026-09-05-hook-retro.md
+blocked sentinel
*** End Patch"""

PROJECT_CONTROL_PATCH = """*** Begin Patch
*** Add File: project-only-control.txt
+project hook control
*** End Patch"""

PROJECT_CONTROL_FILE = "project-only-control.txt"
PROJECT_CONTROL_MARKER = "project-hook-ran.txt"
PROJECT_CONTROL_OBSERVATIONS = "project-hook-observations.jsonl"

IGNORED_PATCH = """*** Begin Patch
*** Add File: ignored.txt
+allowed sentinel
*** End Patch"""

MAIN_PATCH = """*** Begin Patch
*** Add File: tracked.py
+BLOCKED = True
*** End Patch"""


def init_repo(root: Path, branch: str) -> None:
    root.mkdir(parents=True)
    if run_git(["init", "-q", "-b", "main", str(root)], timeout=15).returncode:
        raise RuntimeError(f"git init failed: {root}")
    for key, value in (("user.email", "test@example.com"), ("user.name", "test"),
                       ("core.hooksPath", str(root / ".test-hooks"))):
        if run_git(["-C", str(root), "config", key, value], timeout=15).returncode:
            raise RuntimeError(f"git config failed: {key}")
    (root / ".test-hooks").mkdir()
    (root / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    run_git(["-C", str(root), "add", ".gitignore"], timeout=15)
    if run_git(["-C", str(root), "commit", "-q", "-m", "init"], timeout=15).returncode:
        raise RuntimeError(f"git commit failed: {root}")
    if branch != "main" and run_git(["-C", str(root), "checkout", "-q", "-b", branch], timeout=15).returncode:
        raise RuntimeError(f"git checkout failed: {branch}")


def wire_hooks(root: Path, *, project_control: bool = False) -> None:
    hook_dir = root / ".codex" / "hooks"
    hook_dir.mkdir(parents=True)
    source = Path(__file__).resolve().parents[1] / "hooks"
    observations = root / "hook-observations.jsonl"
    by_event: dict[str, list[dict]] = {}
    for event, matcher, module in POLICY_HOOKS:
        wrapper = hook_dir / f"{module}.py"
        wrapper.write_text(
            "import json, sys\n"
            "from pathlib import Path\n"
            f"sys.path.insert(0, {str(source)!r})\n"
            f"import _lib, {module}\n"
            "raw = json.load(sys.stdin)\n"
            "payload = _lib.normalize_payload(raw)\n"
            "_lib._ACTIVE_EVENT = payload.get('hook_event_name')\n"
            f"with Path({str(observations)!r}).open('a', encoding='utf-8') as log:\n"
            f"    log.write(json.dumps({{'module': {module!r}, 'payload': raw}}) + '\\n')\n"
            "_lib.read_stdin_json = lambda: payload\n"
            f"{module}.main()\n",
            encoding="utf-8",
        )
        by_event.setdefault(event, []).append({
            "matcher": matcher,
            "hooks": [{"type": "command",
                       "command": subprocess.list2cmdline([sys.executable, str(wrapper)]),
                       "timeout": 15}],
        })
    if project_control:
        wrapper = hook_dir / "project_only_deny.py"
        marker = root / PROJECT_CONTROL_MARKER
        observations = root / PROJECT_CONTROL_OBSERVATIONS
        source = Path(__file__).resolve().parents[1] / "hooks"
        wrapper.write_text(
            "import json, sys\n"
            "from pathlib import Path\n"
            f"sys.path.insert(0, {str(source)!r})\n"
            "import _lib\n"
            "raw = json.load(sys.stdin)\n"
            "payload = _lib.normalize_payload(raw)\n"
            "_lib._ACTIVE_EVENT = payload.get('hook_event_name')\n"
            "cwd = payload.get('cwd')\n"
            "if not isinstance(cwd, str) or not cwd:\n"
            "    raise SystemExit('project-only control missing cwd')\n"
            f"Path({str(observations)!r}).open('a', encoding='utf-8').write(\n"
            "    json.dumps({'module': 'project_only_deny',\n"
            "                'hook_event_name': payload.get('hook_event_name'),\n"
            "                'tool_name': payload.get('tool_name')}) + '\\n')\n"
            f"Path({str(marker)!r}).write_text('project_only_deny\\n', encoding='utf-8')\n"
            "_lib.read_stdin_json = lambda: payload\n"
            "_lib.block('project-only control refusal')\n",
            encoding="utf-8",
        )
        by_event.setdefault("PreToolUse", []).append({
            "matcher": "Edit|Write|MultiEdit",
            "hooks": [{"type": "command",
                       "command": subprocess.list2cmdline([sys.executable, str(wrapper)]),
                       "timeout": 15}],
        })
    (root / ".codex" / "hooks.json").write_text(json.dumps({"hooks": by_event}), encoding="utf-8")


def project_control_observed(root: Path) -> bool:
    """Require the project-only hook's invocation and effect evidence."""
    try:
        marker = (root / PROJECT_CONTROL_MARKER).read_text(encoding="utf-8")
        lines = (root / PROJECT_CONTROL_OBSERVATIONS).read_text(encoding="utf-8").splitlines()
        observations = [json.loads(line) for line in lines]
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return False
    return (marker == "project_only_deny\n" and len(observations) == 1
            and observations[0] == {
                "module": "project_only_deny",
                "hook_event_name": "PreToolUse",
                "tool_name": "apply_patch",
            })


def successful_file_change_observed(log: str, target: Path) -> bool:
    """Require one parsed Codex completion record for the expected file."""
    try:
        records = [json.loads(line) for line in log.splitlines()]
    except (json.JSONDecodeError, UnicodeDecodeError):
        return False
    matches = []
    for record in records:
        if not isinstance(record, dict):
            return False
        item = record.get("item")
        if not isinstance(item, dict) or item.get("type") != "file_change":
            continue
        if record.get("type") == "item.started" and item.get("status") == "in_progress":
            continue
        if (record.get("type") != "item.completed" or item.get("status") != "completed"
                or not isinstance(item.get("changes"), list)):
            return False
        for change in item["changes"]:
            if (isinstance(change, dict) and change.get("kind") == "add"
                    and isinstance(change.get("path"), str)
                    and Path(change["path"]).resolve() == target.resolve()):
                matches.append(change)
    return len(matches) == 1


def run_codex(codex: str, root: Path, model: str, prompt: str, name: str,
              extra_env: dict[str, str] | None = None, ignore_user_config: bool = False) -> tuple[int, str]:
    env = {**os.environ, "PYTHONUTF8": "1", "CLAUDE_HOOKS_STATE_DIR": str(root / "state"),
           "FLEET_CONTEXT_FILTER_MODE": "off", "APP_LAUNCHER_SESSION_ID": ""}
    if extra_env:
        env.update(extra_env)
    argv = [codex, "exec", "--ephemeral", "--json", "--approve-for-me",
            "--dangerously-bypass-hook-trust"]
    if ignore_user_config:
        argv.append("--ignore-user-config")
    argv.extend(["-c", f'projects."{root.as_posix()}".trust_level="trusted"',
                 "-m", model, "-C", str(root), "-"])
    run_path = root / f"run-{name}.jsonl"
    err_path = root / f"stderr-{name}.txt"
    with run_path.open("w", encoding="utf-8") as out, err_path.open("w", encoding="utf-8") as err:
        try:
            proc = subprocess.run(argv, input=prompt, text=True, encoding="utf-8", stdout=out,
                                  stderr=err, env=env, timeout=240, creationflags=NO_WINDOW)
        except subprocess.TimeoutExpired:
            return 1, ""
    return proc.returncode, run_path.read_text(encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True,
                        help="New disposable directory (must not exist)")
    parser.add_argument("--model", required=True)
    args = parser.parse_args()
    root = args.workspace.absolute()
    codex = shutil.which("codex")
    if os.name != "nt" or root.exists() or not codex:
        parser.error("Requires Windows, installed Codex and a new workspace directory")

    feature = root / "feature"
    main_repo = root / "main"
    discovery_control = root / "project-hook-discovery-control"
    control = root / "ignore-user-config-control"
    init_repo(feature, "feat/policy-probe")
    init_repo(main_repo, "main")
    init_repo(discovery_control, "feat/control")
    init_repo(control, "feat/control")
    for repo in (feature, main_repo):
        wire_hooks(repo)
    wire_hooks(discovery_control, project_control=True)
    wire_hooks(control, project_control=True)

    feature_prompt = f"""Authorized disposable policy conformance test. Make exactly three tool calls in order, then stop and report every hook message verbatim. Do not inspect files, retry, fix warnings, delegate, or use other tools.
1. One exec_command call with shell=C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe and login=false. Its exact command must be: gh pr create --help --body 'see `fleet_conformance_sentinel`'
2. One apply_patch call with this exact patch:
{FEATURE_PATCH}
3. One apply_patch call with this exact patch:
{DATED_PATCH}
"""
    feature_code, feature_log = run_codex(codex, feature, args.model, feature_prompt, "feature")

    main_prompt = f"""Authorized disposable branch-policy conformance test. Make exactly two apply_patch calls in order with the exact patches below. Do not inspect files, retry, switch branch, delegate, or use another tool. Report both tool results and any refusal verbatim.
First patch:
{IGNORED_PATCH}
Second patch:
{MAIN_PATCH}
"""
    main_code, main_log = run_codex(
        codex, main_repo, args.model, main_prompt, "main",
        {"APP_LAUNCHER_SESSION_ID": "codex-policy-probe"},
    )

    discovery_prompt = f"""Authorized disposable project-hook discovery control. Make exactly one apply_patch call with this exact patch, then stop. Do not use another tool. Report the tool result and any refusal verbatim.
{PROJECT_CONTROL_PATCH}
"""
    discovery_code, discovery_log = run_codex(
        codex, discovery_control, args.model, discovery_prompt, "project-hook-discovery",
    )

    control_prompt = f"""Authorized disposable project-hook suppression control. Make exactly one apply_patch call with this exact patch, then stop. Do not use another tool. Report the tool result verbatim.
{PROJECT_CONTROL_PATCH}
"""
    control_code, control_log = run_codex(
        codex, control, args.model, control_prompt, "control", ignore_user_config=True,
    )

    feature_effects = (
        all((feature / path).exists() for path in ("safe.py", "wrapper.py", "browser.py",
                                                   "docs/hook-policy.md"))
        and not (feature / "docs/2026-09-05-hook-retro.md").exists()
    )
    feature_messages = all(marker in feature_log for marker in (
        "Write the markdown", "wrapper.py", "browser launch target", "2026-09-05-hook-retro.md",
    ))
    main_effects = (main_repo / "ignored.txt").exists() and not (main_repo / "tracked.py").exists()
    main_message = "editing on 'main'" in main_log
    discovery_effect = (not (discovery_control / PROJECT_CONTROL_FILE).exists()
                        and project_control_observed(discovery_control))
    discovery_message = "project-only control refusal" in discovery_log
    control_effect = ((control / PROJECT_CONTROL_FILE).exists()
                      and not (control / PROJECT_CONTROL_MARKER).exists()
                      and not (control / PROJECT_CONTROL_OBSERVATIONS).exists())
    control_message = successful_file_change_observed(
        control_log, control / PROJECT_CONTROL_FILE)
    passed = (feature_code == 0 and main_code == 0 and discovery_code == 0 and control_code == 0
              and feature_effects and feature_messages and main_effects and main_message
              and discovery_effect and discovery_message and control_effect and control_message)
    version = subprocess.run([codex, "--version"], capture_output=True, text=True, timeout=15,
                             creationflags=NO_WINDOW).stdout.strip()
    print(json.dumps({
        "version": version, "conformance": "pass" if passed else "fail",
        "approve_for_me": True, "feature_branch": feature_effects,
        "pre_advisory_visible": "Write the markdown" in feature_log,
        "post_advisories_visible": all(marker in feature_log for marker in ("wrapper.py", "browser launch target")),
        "dated_docs_blocked": not (feature / "docs/2026-09-05-hook-retro.md").exists(),
        "main_ignored_allowed": (main_repo / "ignored.txt").exists(),
        "main_tracked_blocked": not (main_repo / "tracked.py").exists() and main_message,
        "project_hook_discovery_observed": discovery_effect and discovery_message,
        "ignore_user_config_suppresses_project_hook": control_effect and control_message,
        "installed_dated_docs_guard_still_active": not (feature / "docs/2026-09-05-hook-retro.md").exists(),
        "evidence": str(root),
    }))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
