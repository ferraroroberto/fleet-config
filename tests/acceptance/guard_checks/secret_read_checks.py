"""Acceptance checks for `secret_scan_guard`'s read sink.

A command that prints an untracked secret-bearing file (`cat .env`, `grep token
config/webapp_config.json`, `Get-Content config\\notify_config.json`, an inline
Python dump) is refused; keys-only reads, sample files, tracked files, missing
files and unrelated commands pass. Claude's `Read` and content-mode `Grep`
of the same files are refused by the same rules. Every fixture is an invented file in a temp
dir holding one obviously fake value, and every refusal is asserted not to
contain it. The verdicts come from real hook processes: the standalone module,
`hook_dispatch.py` with the live Bash and PowerShell lists, and a Codex
`.codex/hooks` entry point.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

from acceptance.checks_dispatch import BASH_HOOKS, POWERSHELL_HOOKS, dispatch
from acceptance.hook_matrix import FAKE_GHP
from acceptance.shared import (
    HOOK_SPAWN_TIMEOUT_S,
    HOOKS,
    PYTHON,
    _Checker,
    hook_env,
    run,
)

FAKE_VALUE = "FAKE-VALUE-NOT-A-SECRET-0000"

REFUSED = (
    ("Bash", "grep token config/webapp_config.json"),
    ("Bash", "cat .env"),
    ("PowerShell", r"Get-Content config\notify_config.json"),
    ("Bash", "cd sub && cat .env"),
    ("Bash", "python -c \"import json; d = json.load(open('config/webapp_config.json')); print(d)\""),
    ("Bash", "cat config/*.json"),
    ("PowerShell", "Select-String -Path config/webapp_config.json -Pattern token"),
    ("Bash", "head -n 3 .env.local"),
    ("Bash", "python - <<'EOF'\nimport json\nprint(json.load(open('config/webapp_config.json')))\nEOF"),
    ("PowerShell", "powershell -Command \"Get-Content .env\""),
)
ALLOWED = (
    ("Bash", "jq 'keys' config/webapp_config.json"),
    ("Bash", "jq -r 'keys[]' config/webapp_config.json"),
    ("Bash", "grep -o '\"[a-z_]*\":' config/webapp_config.json"),
    ("Bash", "grep -o '^[A-Za-z_][A-Za-z0-9_]*=' .env"),
    ("Bash", "grep -c token .env"),
    ("PowerShell", "Select-String -Path .env -Pattern TOKEN -Quiet"),
    ("Bash", "python -c \"import json; print(list(json.load(open('config/webapp_config.json')).keys()))\""),
    ("Bash", "cat .env.example"),
    ("PowerShell", r"Get-Content config\webapp_config.sample.json"),
    ("Bash", "cat config/missing_config.json"),
    ("Bash", "ls config"),
    ("Bash", "grep token docs/README.md"),
    ("Bash", "cp .env .env.bak"),
    ("Bash", "sed -i 's/a/b/' .env"),
)


def _fixture_tree(root: Path) -> None:
    """Invented secret-bearing files, a sample copy and a docs file, all outside any repo."""
    (root / "config").mkdir()
    (root / "docs").mkdir()
    (root / "sub").mkdir()
    secret = json.dumps({"bot_token": FAKE_VALUE, "chat_id": FAKE_VALUE})
    for rel in ("config/webapp_config.json", "config/notify_config.json"):
        (root / rel).write_text(secret, encoding="utf-8")
    (root / "config/webapp_config.sample.json").write_text('{"bot_token": ""}', encoding="utf-8")
    for rel in (".env", ".env.local", "sub/.env"):
        (root / rel).write_text(f"TOKEN={FAKE_VALUE}\n", encoding="utf-8")
    (root / ".env.example").write_text("TOKEN=\n", encoding="utf-8")
    (root / "docs/README.md").write_text("Set the token in .env.\n", encoding="utf-8")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, timeout=30)


def _tracked_repo(root: Path) -> Path:
    """A temp git repo with a committed `config.json`: tracked, so printing it leaks nothing new."""
    repo = root / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "config.json").write_text('{"name": "fixture"}', encoding="utf-8")
    (repo / ".env").write_text(f"TOKEN={FAKE_VALUE}\n", encoding="utf-8")
    _git(repo, "add", "config.json")
    _git(repo, "commit", "-q", "-m", "fixture")
    return repo


def _codex_entry(root: Path) -> Path:
    entry = root / ".codex" / "hooks" / "secret_scan_guard.py"
    entry.parent.mkdir(parents=True)
    entry.write_text(f"import sys\nsys.path.insert(0, {str(HOOKS)!r})\n"
                     "import secret_scan_guard\nsecret_scan_guard.main()\n", encoding="utf-8")
    return entry


def _is_codex_deny(stdout: str) -> bool:
    try:
        return json.loads(stdout).get("hookSpecificOutput", {}).get("permissionDecision") == "deny"
    except ValueError:
        return False


def _payload(tool: str, command: str, cwd: Path) -> Dict[str, object]:
    return {"hook_event_name": "PreToolUse", "tool_name": tool, "cwd": str(cwd),
            "session_id": "secret-read-acceptance", "tool_input": {"command": command}}


def _registry(root: Path, name: str, body: str) -> Dict[str, str]:
    toml = root / f"projects-{name}.toml"
    toml.write_text("[global]\nnever_kill_ports = []\n" + body, encoding="utf-8")
    return {"CLAUDE_HOOKS_PROJECTS_TOML": str(toml)}


def _matrix_checks(check: _Checker, verdict: Callable[..., Tuple[int, str, str]], root: Path) -> None:
    for tool, command in REFUSED:
        code, out, err = verdict(tool, command)
        check(f"secret_read: {tool} `{command.splitlines()[0]}` -> refused",
              code == 2 and "secret-bearing file" in err and FAKE_VALUE not in out + err, err)
    for tool, command in ALLOWED:
        code, out, err = verdict(tool, command)
        check(f"secret_read: {tool} `{command}` -> allowed", code == 0 and not out, err)
    _code, _out, err = verdict("Bash", "cat .env")
    check("secret_read: refusal names the file and the keys-only forms",
          ".env" in err and "jq 'keys'" in err and "secret_read_allow" in err, err)


def _git_and_switch_checks(check: _Checker, verdict: Callable[..., Tuple[int, str, str]],
                           root: Path) -> None:
    repo = _tracked_repo(root)
    check("secret_read: a tracked config.json prints freely",
          verdict("Bash", "cat config.json", repo)[0] == 0)
    check("secret_read: an untracked .env inside a repo is refused",
          verdict("Bash", "cat .env", repo)[0] == 2)
    code, _out, err = verdict("Bash", "cat .env", None, {"PATH": ""})
    check("secret_read: git unavailable -> refused as unconfirmed, not passed",
          code == 2 and "could not confirm" in err and FAKE_VALUE not in err, err)
    allow = _registry(root, "allow", 'secret_read_allow = ["*/config/notify_config.json"]\n')
    check("secret_read: a secret_read_allow glob lets that path print",
          verdict("PowerShell", r"Get-Content config\notify_config.json", None, allow)[0] == 0)
    check("secret_read: secret_read_allow leaves other paths refused",
          verdict("Bash", "cat config/webapp_config.json", None, allow)[0] == 2)
    off = _registry(root, "off", "secret_read_guard = false\n")
    check("secret_read: secret_read_guard = false turns the read sink off",
          verdict("Bash", "cat .env", None, off)[0] == 0)
    code, _out, err = verdict("Bash", f'git commit -m "wip {FAKE_GHP}"', None, off)
    check("secret_read: the switch leaves the commit sink running",
          code == 2 and "staged for commit" in err, err)


def _harness_checks(check: _Checker, root: Path) -> None:
    for tool, hooks in (("Bash", BASH_HOOKS), ("PowerShell", POWERSHELL_HOOKS)):
        code, out, err = dispatch(hooks, _payload(tool, "cat .env", root))
        check(f"secret_read: hook_dispatch {tool} list refuses `cat .env` (Claude exit 2)",
              code == 2 and b"secret-bearing file" in err and FAKE_VALUE.encode() not in out + err,
              f"{code} {err!r}")
        code, out, err = dispatch(hooks, _payload(tool, "jq 'keys' config/webapp_config.json", root))
        check(f"secret_read: hook_dispatch {tool} list allows a keys-only read",
              code == 0 and b"secret-bearing" not in out + err, f"{code} {out!r} {err!r}")
    entry = _codex_entry(root)
    for command, refused in (("cat .env", True), ("cat .env.example", False)):
        res = subprocess.run([PYTHON, str(entry)], input=json.dumps(_payload("Bash", command, root)),
                             capture_output=True, text=True, encoding="utf-8", errors="replace",
                             timeout=HOOK_SPAWN_TIMEOUT_S, env=hook_env())
        check(f"secret_read: Codex `{command}` -> {'deny' if refused else 'allow'}",
              res.returncode == 0 and _is_codex_deny(res.stdout) is refused
              and FAKE_VALUE not in res.stdout + res.stderr, res.stdout + res.stderr)


def _tool_payload(tool: str, tool_input: Dict[str, object], cwd: Path) -> Dict[str, object]:
    return {"hook_event_name": "PreToolUse", "tool_name": tool, "cwd": str(cwd),
            "session_id": "secret-read-acceptance", "tool_input": tool_input}


def _tool_cases(root: Path) -> Tuple[Tuple[str, str, Dict[str, object]], ...]:
    """`(label, tool, tool_input)` for Claude's own Read and Grep, refused first."""
    return (
        ("Read of an untracked .env", "Read", {"file_path": str(root / ".env")}),
        ("Read of a relative .env.local", "Read", {"file_path": ".env.local"}),
        ("Grep content over config/webapp_config.json", "Grep",
         {"pattern": "token", "path": str(root / "config/webapp_config.json"), "output_mode": "content"}),
        ("Grep content with glob config/*.json", "Grep",
         {"pattern": "token", "path": str(root), "glob": "config/*.json", "output_mode": "content"}),
        ("Grep content with glob **/.env", "Grep",
         {"pattern": "TOKEN", "glob": "**/.env", "output_mode": "content"}),
        ("Grep content with a bare-name glob .env under sub/", "Grep",
         {"pattern": "TOKEN", "path": str(root / "sub"), "glob": ".env", "output_mode": "content"}),
        ("Grep content with a brace glob config/*.{json,toml}", "Grep",
         {"pattern": "token", "glob": "config/*.{json,toml}", "output_mode": "content"}),
    )


_TOOL_ALLOWED = (
    ("Read of .env.example", "Read", {"file_path": ".env.example"}),
    ("Read of a sample config", "Read", {"file_path": "config/webapp_config.sample.json"}),
    ("Read of a missing config", "Read", {"file_path": "config/missing_config.json"}),
    ("Read of a hook source file", "Read", {"file_path": str(HOOKS / "secret_scan_guard.py")}),
    ("Grep files_with_matches over .env", "Grep",
     {"pattern": "TOKEN", "path": ".env", "output_mode": "files_with_matches"}),
    ("Grep count over .env", "Grep", {"pattern": "TOKEN", "path": ".env", "output_mode": "count"}),
    ("Grep with the default output mode over .env", "Grep", {"pattern": "TOKEN", "path": ".env"}),
    ("Grep content over hooks/", "Grep",
     {"pattern": "def main", "path": str(HOOKS), "output_mode": "content"}),
    ("Grep content over hooks/ with glob *.py", "Grep",
     {"pattern": "def main", "path": str(HOOKS), "glob": "**/*.py", "output_mode": "content"}),
)


def _tool_checks(check: _Checker, root: Path) -> None:
    """Claude's `Read` and `Grep` tools print a file without a shell; the same rules judge them."""
    def verdict(tool: str, tool_input: Dict[str, object], cwd: Optional[Path] = None,
                extra_env: Optional[Dict[str, str]] = None) -> Tuple[int, str, str]:
        return run("secret_scan_guard", _tool_payload(tool, tool_input, cwd or root), extra_env=extra_env)

    for label, tool, tool_input in _tool_cases(root):
        code, out, err = verdict(tool, tool_input)
        check(f"secret_read: {label} -> refused",
              code == 2 and "secret-bearing file" in err and FAKE_VALUE not in out + err, err)
    for label, tool, tool_input in _TOOL_ALLOWED:
        code, out, err = verdict(tool, tool_input)
        check(f"secret_read: {label} -> allowed", code == 0 and not out, err)
    repo = root / "repo"
    check("secret_read: Read of a tracked config.json -> allowed",
          verdict("Read", {"file_path": "config.json"}, repo)[0] == 0)
    allow = _registry(root, "tool-allow", 'secret_read_allow = ["*/config/notify_config.json"]\n')
    check("secret_read: secret_read_allow lets a Read of that path through",
          verdict("Read", {"file_path": "config/notify_config.json"}, None, allow)[0] == 0)
    check("secret_read: secret_read_allow leaves another Read refused",
          verdict("Read", {"file_path": "config/webapp_config.json"}, None, allow)[0] == 2)
    off = _registry(root, "tool-off", "secret_read_guard = false\n")
    check("secret_read: secret_read_guard = false lets a Read of .env through",
          verdict("Read", {"file_path": ".env"}, None, off)[0] == 0)
    check("secret_read: secret_read_guard = false lets a content Grep of .env through",
          verdict("Grep", {"pattern": "TOKEN", "path": ".env", "output_mode": "content"}, None, off)[0] == 0)
    template = json.loads((HOOKS.parent / "settings.template.json").read_text(encoding="utf-8"))
    wired = [hook.get("command", "") for block in template["hooks"]["PreToolUse"]
             if block.get("matcher") == "Read|Grep" for hook in block.get("hooks", [])]
    check("secret_read: settings.template.json wires secret_scan_guard on Read|Grep",
          any(cmd.endswith("-Hook secret_scan_guard") for cmd in wired), str(wired))


def _secret_read_unit_checks() -> Tuple[int, int]:
    check = _Checker()
    root = Path(tempfile.mkdtemp(prefix="secret_read_"))
    try:
        _fixture_tree(root)

        def verdict(tool: str, command: str, cwd: Optional[Path] = None,
                    extra_env: Optional[Dict[str, str]] = None) -> Tuple[int, str, str]:
            return run("secret_scan_guard", _payload(tool, command, cwd or root), extra_env=extra_env)

        _matrix_checks(check, verdict, root)
        _git_and_switch_checks(check, verdict, root)
        _tool_checks(check, root)
        _harness_checks(check, root)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return check.failures, check.total
