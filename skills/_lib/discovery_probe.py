"""Opt-in native discovery and instruction proof with disposable sentinels.

The default native probes do not start model turns.  Skill results come from
native catalog interfaces, and instruction assembly never infers that an
``AGENTS.md`` pointer caused its target to be read.  A real Codex model turn is
available only through the explicit ``--codex-model-proof MODEL`` option.
``--instruction-proof`` also compares the known user-global Claude file locally
on loopback, retaining only metadata.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import uuid
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional

import scoped_discovery as discovery
from git_run import run_git
from no_window import NO_WINDOW


PROBE_PREFIX = "fleet-probe-748"
GLOBAL_SKILL = f"{PROBE_PREFIX}-global"
ROOT_SKILL = f"{PROBE_PREFIX}-root"
PACKAGE_SKILL = f"{PROBE_PREFIX}-package"
SCOPES = ("root", "package", "sibling")
EXPECTED = {
    "root": [GLOBAL_SKILL, ROOT_SKILL],
    "package": [GLOBAL_SKILL, ROOT_SKILL, PACKAGE_SKILL],
    "sibling": [GLOBAL_SKILL, ROOT_SKILL],
}
TIMEOUT = 45


def _run(args: list[str], cwd: Path, env: Optional[dict[str, str]] = None,
         timeout: int = TIMEOUT,
         input_text: Optional[str] = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args, cwd=cwd, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
        creationflags=NO_WINDOW, check=False, input=input_text,
    )


def _skill(scope: Path, name: str) -> None:
    path = scope / ".claude" / "skills" / name
    path.mkdir(parents=True, exist_ok=True)
    (path / "SKILL.md").write_text(
        "---\n"
        f"name: {name}\n"
        "description: Harmless synthetic native-discovery sentinel.\n"
        "---\n"
        "No operations and no model invocation.\n",
        encoding="utf-8",
    )


def _instruction(path: Path, marker: str, minimum_bytes: int = 0,
                 pointer: bool = False) -> None:
    lines = ["See CLAUDE.md."] if pointer else [
        "# Harmless synthetic instruction fixture",
        "This file contains no user, repository, credential, memory, or transcript data.",
    ]
    index = 0
    while len(("\n".join(lines) + f"\nEOF_MARKER={marker}\n").encode()) < minimum_bytes:
        lines.append(f"Synthetic padding {index:05d}: harmless instruction-loading fixture only.")
        index += 1
    lines.append(f"EOF_MARKER={marker}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _setup(base: Path) -> dict[str, Path]:
    home = base / "home"
    repo = base / "repo"
    package = repo / "packages" / "api"
    sibling = repo / "packages" / "other"
    for path in (home, repo, package, sibling):
        path.mkdir(parents=True, exist_ok=True)
    for checkout in (home, repo):
        result = run_git(["-C", str(checkout), "init", "--quiet"], timeout=10)
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or "git init failed")

    _skill(home, GLOBAL_SKILL)
    _skill(repo, ROOT_SKILL)
    _skill(package, PACKAGE_SKILL)
    for checkout in (home, repo):
        result = discovery.reconcile(checkout, "install", None if checkout == home else home)
        if result["state"] != "ok":
            raise RuntimeError(f"synthetic link reconciliation failed: {result}")

    _instruction(
        home / ".agents" / "AGENTS.md", "CODEX_GLOBAL_EOF_748",
        minimum_bytes=40_000,
    )
    _instruction(repo / "CLAUDE.md", "CLAUDE_ROOT_EOF_748", minimum_bytes=40_000)
    _instruction(package / "CLAUDE.md", "CLAUDE_NESTED_EOF_748")
    _instruction(repo / "AGENTS.md", "CODEX_ROOT_POINTER_EOF_748", pointer=True)
    _instruction(package / "AGENTS.md", "CODEX_NESTED_POINTER_EOF_748", pointer=True)
    return {"home": home, "root": repo, "package": package, "sibling": sibling}


def _version(executable: Optional[str]) -> Optional[str]:
    if not executable:
        return None
    result = _run([executable, "--version"], Path.cwd(), timeout=10)
    return (result.stdout or result.stderr).strip() or None


def _expected_skill_path(paths: dict[str, Path], name: str) -> Path:
    scopes = {
        GLOBAL_SKILL: paths["home"],
        ROOT_SKILL: paths["root"],
        PACKAGE_SKILL: paths["package"],
    }
    return scopes[name] / ".claude" / "skills" / name / "SKILL.md"


def _catalog_result(client: str, executable: Optional[str], version: Optional[str],
                    catalogs: dict[str, list[dict[str, str]]], errors: list[str],
                    path_evidence: Optional[bool] = True) -> dict[str, Any]:
    if not executable:
        return {"client": client, "status": "missing", "version": None,
                "scopes": {}, "errors": [f"{client} executable not found"]}
    observed = {scope: [item["name"] for item in catalogs.get(scope, [])] for scope in SCOPES}
    if path_evidence is True:
        path_evidence = all(
            Path(item["path"]).resolve() == Path(item["source"]).resolve()
            for items in catalogs.values() for item in items
        )
    verified = (
        not errors
        and path_evidence is not False
        and all(observed.get(scope) == EXPECTED[scope] for scope in SCOPES)
    )
    return {
        "client": client,
        "status": "verified" if verified else "failed",
        "version": version,
        "scopes": observed,
        "paths_resolve_to_synthetic_sources": (
            path_evidence if path_evidence is not None
            else "unknown: native interface does not expose source paths"
        ),
        "errors": errors,
    }


def _claude(paths: dict[str, Path]) -> dict[str, Any]:
    executable = shutil.which("claude.exe") or shutil.which("claude")
    version = _version(executable)
    if not executable:
        return _catalog_result("claude", None, None, {}, [])
    catalogs: dict[str, list[dict[str, str]]] = {}
    errors: list[str] = []
    for scope in SCOPES:
        env = os.environ.copy()
        env.pop("CLAUDECODE", None)
        env["CLAUDE_CONFIG_DIR"] = str(paths["home"] / ".claude")
        process = subprocess.Popen(
            [executable, "-p", "--input-format", "stream-json", "--output-format",
             "stream-json", "--verbose", "--setting-sources", "user,project",
             "--tools", "", "--strict-mcp-config", "--no-session-persistence"],
            cwd=paths[scope], env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
            creationflags=NO_WINDOW,
        )
        messages: queue.Queue[dict[str, Any]] = queue.Queue()

        def read_output() -> None:
            assert process.stdout is not None
            for line in process.stdout:
                try:
                    messages.put(json.loads(line))
                except json.JSONDecodeError:
                    continue

        threading.Thread(target=read_output, daemon=True).start()
        try:
            assert process.stdin is not None
            process.stdin.write(json.dumps({
                "type": "control_request", "request_id": "probe748",
                "request": {"subtype": "initialize"},
            }) + "\n")
            process.stdin.flush()
            deadline = time.monotonic() + TIMEOUT
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise queue.Empty
                message = messages.get(timeout=remaining)
                if message.get("type") != "control_response":
                    continue
                response = message.get("response", {})
                commands = response.get("response", {}).get("commands", [])
                selected = []
                for command in commands:
                    name = command.get("name", "")
                    if name.startswith(PROBE_PREFIX):
                        selected.append({"name": name})
                catalogs[scope] = sorted(selected, key=lambda item: EXPECTED[scope].index(item["name"]))
                break
        except (queue.Empty, OSError, ValueError) as exc:
            errors.append(f"{scope}: {exc}")
            catalogs[scope] = []
        finally:
            if process.stdin:
                process.stdin.close()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
    return _catalog_result(
        "claude", executable, version, catalogs, errors, path_evidence=None,
    )


def _json_rpc(process: subprocess.Popen[str], messages: queue.Queue[dict[str, Any]],
              request_id: int, method: str, params: dict[str, Any]) -> dict[str, Any]:
    assert process.stdin is not None
    process.stdin.write(json.dumps({"id": request_id, "method": method, "params": params}) + "\n")
    process.stdin.flush()
    deadline = time.monotonic() + TIMEOUT
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise queue.Empty
        message = messages.get(timeout=remaining)
        if message.get("id") == request_id:
            return message


def _codex(paths: dict[str, Path]) -> dict[str, Any]:
    executable = shutil.which("codex.exe") or shutil.which("codex")
    version = _version(executable)
    if not executable:
        return _catalog_result("codex", None, None, {}, [])
    env = os.environ.copy()
    env["CODEX_HOME"] = str(paths["home"] / ".agents")
    process = subprocess.Popen(
        [executable, "app-server", "--stdio"], cwd=paths["root"], env=env,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW,
    )
    messages: queue.Queue[dict[str, Any]] = queue.Queue()

    def read_output() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            try:
                messages.put(json.loads(line))
            except json.JSONDecodeError:
                continue

    threading.Thread(target=read_output, daemon=True).start()
    catalogs: dict[str, list[dict[str, str]]] = {scope: [] for scope in SCOPES}
    errors: list[str] = []
    try:
        initialized = _json_rpc(process, messages, 1, "initialize", {
            "clientInfo": {"name": "fleet_config_discovery_probe", "version": "1"},
        })
        if "error" in initialized:
            raise RuntimeError(str(initialized["error"]))
        assert process.stdin is not None
        process.stdin.write(json.dumps({"method": "initialized"}) + "\n")
        process.stdin.flush()
        result = _json_rpc(process, messages, 2, "skills/list", {
            "cwds": [str(paths[scope]) for scope in SCOPES], "forceReload": True,
        })
        if "error" in result:
            raise RuntimeError(str(result["error"]))
        by_cwd = {str(Path(item["cwd"]).resolve()): item for item in result.get("result", {}).get("data", [])}
        for scope in SCOPES:
            row = by_cwd.get(str(paths[scope].resolve()), {})
            errors.extend(f"{scope}: {error}" for error in row.get("errors", []))
            for skill in row.get("skills", []):
                if skill.get("name", "").startswith(PROBE_PREFIX):
                    path = Path(skill["path"])
                    catalogs[scope].append({"name": skill["name"], "path": str(path),
                                            "source": str(_expected_skill_path(paths, skill["name"]))})
            catalogs[scope].sort(key=lambda item: EXPECTED[scope].index(item["name"]))
    except (queue.Empty, OSError, RuntimeError, ValueError) as exc:
        errors.append(str(exc))
    finally:
        if process.stdin:
            process.stdin.close()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=10)
    return _catalog_result("codex", executable, version, catalogs, errors)


def _pi(paths: dict[str, Path]) -> dict[str, Any]:
    executable = shutil.which("node.exe") or shutil.which("node")
    pi_executable = shutil.which("pi") or shutil.which("pi.ps1")
    if not executable or not pi_executable:
        return _catalog_result("pi", None, None, {}, [])
    version = _version(pi_executable)
    npm = shutil.which("npm.cmd") or shutil.which("npm")
    if not npm:
        return {"client": "pi", "status": "missing", "version": version,
                "scopes": {}, "errors": ["npm executable not found"]}
    npm_root = _run([npm, "root", "-g"], paths["root"], timeout=10)
    module = Path(npm_root.stdout.strip()) / "@earendil-works/pi-coding-agent/dist/core"
    loader = module / "resource-loader.js"
    settings = module / "settings-manager.js"
    if not loader.is_file() or not settings.is_file():
        return {"client": "pi", "status": "unsupported", "version": version,
                "scopes": {}, "errors": ["installed DefaultResourceLoader modules not found"]}
    script = paths["home"] / "pi_probe.mjs"
    script.write_text(
        "import {pathToFileURL} from 'node:url';\n"
        "const [loaderPath, settingsPath, agentDir, ...cwds] = process.argv.slice(2);\n"
        "const {DefaultResourceLoader} = await import(pathToFileURL(loaderPath));\n"
        "const {SettingsManager} = await import(pathToFileURL(settingsPath));\n"
        "for (const cwd of cwds) {\n"
        " const manager=SettingsManager.inMemory({}, {projectTrusted:true});\n"
        " const resource=new DefaultResourceLoader({cwd,agentDir,settingsManager:manager,noExtensions:true,noThemes:true,noPromptTemplates:true,noContextFiles:true});\n"
        " await resource.reload(); const result=resource.getSkills();\n"
        " console.log(JSON.stringify({cwd,skills:result.skills,diagnostics:result.diagnostics}));\n"
        "}\n",
        encoding="utf-8",
    )
    result = _run([executable, str(script), str(loader), str(settings),
                   str(paths["home"] / ".agents"), *(str(paths[scope]) for scope in SCOPES)], paths["root"])
    catalogs: dict[str, list[dict[str, str]]] = {scope: [] for scope in SCOPES}
    errors = [] if result.returncode == 0 else [result.stderr.strip() or f"node exited {result.returncode}"]
    try:
        for line in result.stdout.splitlines():
            row = json.loads(line)
            scope = next(name for name in SCOPES if paths[name].resolve() == Path(row["cwd"]).resolve())
            errors.extend(f"{scope}: {item}" for item in row.get("diagnostics", [])
                          if PROBE_PREFIX in str(item))
            for skill in row.get("skills", []):
                if skill.get("name", "").startswith(PROBE_PREFIX):
                    path = Path(skill.get("filePath") or skill.get("path"))
                    catalogs[scope].append({"name": skill["name"], "path": str(path),
                                            "source": str(_expected_skill_path(paths, skill["name"]))})
            catalogs[scope].sort(key=lambda item: EXPECTED[scope].index(item["name"]))
    except (json.JSONDecodeError, KeyError, StopIteration, TypeError, ValueError) as exc:
        errors.append(str(exc))
    return _catalog_result("pi", pi_executable, version, catalogs, errors)


def _grok_inventory(paths: dict[str, Path]) -> dict[str, Any]:
    executable = shutil.which("grok.exe") or shutil.which("grok")
    version = _version(executable)
    if not executable:
        return _catalog_result("grok", None, None, {}, [])
    catalogs: dict[str, list[dict[str, str]]] = {scope: [] for scope in SCOPES}
    errors: list[str] = []
    env = os.environ.copy()
    env.update({"HOME": str(paths["home"]), "USERPROFILE": str(paths["home"])})
    for scope in SCOPES:
        result = _run([executable, "inspect", "--json"], paths[scope], env=env)
        if result.returncode:
            errors.append(f"{scope}: {result.stderr.strip() or result.returncode}")
            continue
        try:
            row = json.loads(result.stdout)
            for skill in row.get("skills", []):
                if skill.get("name", "").startswith(PROBE_PREFIX):
                    path = Path(skill.get("source", {}).get("path", ""))
                    catalogs[scope].append({"name": skill["name"], "path": str(path),
                                            "source": str(_expected_skill_path(paths, skill["name"]))})
            catalogs[scope].sort(key=lambda item: EXPECTED[scope].index(item["name"]))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            errors.append(f"{scope}: {exc}")
    report = _catalog_result("grok", executable, version, catalogs, errors)
    grok_current_scope_only = {
        "root": [GLOBAL_SKILL, ROOT_SKILL],
        "package": [GLOBAL_SKILL, PACKAGE_SKILL],
        "sibling": [GLOBAL_SKILL],
    }
    if not errors and report["scopes"] == grok_current_scope_only:
        report["status"] = "unsupported"
        report["unsupported_capability"] = (
            "Grok 0.2.118 inspect discovers global plus current-scope skills, "
            "but does not inherit project skills from ancestor scopes"
        )
    return report


def _grok(paths: dict[str, Path]) -> dict[str, Any]:
    """Distinguish observed capability from inconsistent native scope discovery."""
    first = _grok_inventory(paths)
    if first["status"] in {"missing", "failed"}:
        return first
    second = _grok_inventory(paths)
    if first.get("scopes") != second.get("scopes") or first["status"] != second["status"]:
        return {"client": "grok", "version": first.get("version"), "status": "unknown",
                "reason": "native ancestor-scope inventory changed between two fresh invocations",
                "observations": [first, second]}
    first["repeated_native_inventory"] = "same result in two fresh invocations"
    return first


def _codex_instructions(paths: dict[str, Path]) -> dict[str, Any]:
    executable = shutil.which("codex.exe") or shutil.which("codex")
    if not executable:
        return {"client": "codex", "status": "missing"}
    env = os.environ.copy()
    env["CODEX_HOME"] = str(paths["home"] / ".agents")
    result = _run([executable, "debug", "prompt-input", "PROBE_748"], paths["package"], env=env)
    text = result.stdout
    start = text.find("[")
    if result.returncode or start < 0:
        return {"client": "codex", "status": "failed",
                "reason": result.stderr.strip() or "prompt-input JSON absent"}
    try:
        payload = json.loads(text[start:])
        visible = "\n".join(
            part.get("text", "") for item in payload for part in item.get("content", [])
            if isinstance(part, dict)
        )
    except (json.JSONDecodeError, TypeError) as exc:
        return {"client": "codex", "status": "failed", "reason": str(exc)}
    global_path = paths["home"] / ".agents" / "AGENTS.md"
    assembly_verified = all(marker in visible for marker in (
        "CODEX_GLOBAL_EOF_748",
        "CODEX_ROOT_POINTER_EOF_748",
        "CODEX_NESTED_POINTER_EOF_748",
    ))
    return {
        "client": "codex",
        "status": "unknown" if assembly_verified else "failed",
        "mode": "native debug prompt-input; no model turn",
        "global": {"bytes": global_path.stat().st_size,
                   "eof_loaded": "CODEX_GLOBAL_EOF_748" in visible},
        "root_pointer_eof_loaded": "CODEX_ROOT_POINTER_EOF_748" in visible,
        "nested_pointer_eof_loaded": "CODEX_NESTED_POINTER_EOF_748" in visible,
        "claude_target_eof": "unknown: prompt assembly proves pointer bytes, not model obedience to See CLAUDE.md",
        "reason": (
            "pointer bytes assembled; target loading needs --codex-model-proof MODEL"
            if assembly_verified else "one or more instruction assembly EOF markers were absent"
        ),
    }


def _codex_model_instructions(paths: dict[str, Path], model: str) -> dict[str, Any]:
    """Use an explicitly requested Codex model turn to follow synthetic pointers."""
    executable = shutil.which("codex.exe") or shutil.which("codex")
    if not executable:
        return {"client": "codex-model", "status": "missing"}
    root_target = (paths["root"] / "CLAUDE.md").resolve()
    nested_target = (paths["package"] / "CLAUDE.md").resolve()
    expected = "CLAUDE_ROOT_EOF_748,CLAUDE_NESTED_EOF_748"
    command = (
        f"Get-Content -LiteralPath '{root_target}','{nested_target}'"
    )
    prompt = f"""Authorized synthetic instruction-loading probe. Follow the active project
instructions and their canonical CLAUDE.md pointers. Make exactly one exec_command call
with shell=C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe, login=false, and
this exact cmd:
{command}
Read no other paths, call no other tools, and do not retry. After the call, return only
the comma-separated EOF marker values from the two files, in root then nested order."""
    env = os.environ.copy()
    env.pop("CODEX_HOME", None)
    env["PYTHONUTF8"] = "1"
    try:
        result = _run(
            [executable, "exec", "--ephemeral", "--json", "--approve-for-me",
             "-c", f'projects."{paths["root"].as_posix()}".trust_level="trusted"',
             "-m", model, "-C", str(paths["package"]), "-"],
            paths["package"], env=env, timeout=150, input_text=prompt,
        )
    except subprocess.TimeoutExpired:
        return {
            "client": "codex-model", "status": "unknown", "model": model,
            "reason": "Codex model probe timed out after 150 seconds",
        }
    records: list[dict[str, Any]] = []
    try:
        records = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    except json.JSONDecodeError as exc:
        return {
            "client": "codex-model", "status": "unknown", "model": model,
            "reason": f"Codex JSONL could not be parsed: {exc}",
        }
    completed_commands = [
        record["item"] for record in records
        if record.get("type") == "item.completed"
        and isinstance(record.get("item"), dict)
        and record["item"].get("type") == "command_execution"
    ]
    agent_messages = [
        record["item"].get("text", "").strip() for record in records
        if record.get("type") == "item.completed"
        and isinstance(record.get("item"), dict)
        and record["item"].get("type") == "agent_message"
    ]
    completed = completed_commands[0] if len(completed_commands) == 1 else {}
    # Native command JSON may retain doubled Windows separators even after
    # JSON decoding. Normalize separator runs, not only individual slashes.
    invoked = re.sub(r"[\\/]+", "/", str(completed.get("command", ""))).casefold()
    scope_verified = (
        len(completed_commands) == 1
        and str(root_target).replace("\\", "/").casefold() in invoked
        and str(nested_target).replace("\\", "/").casefold() in invoked
        and "get-content -literalpath" in invoked
        and completed.get("exit_code") == 0
    )
    output = str(completed.get("aggregated_output", ""))
    markers_verified = (bool(agent_messages) and agent_messages[-1] == expected
                        and all(marker in output for marker in expected.split(",")))
    if result.returncode:
        status = "unknown"
        reason = f"Codex exited {result.returncode}: {result.stderr.strip() or 'no stderr'}"
    elif not scope_verified:
        status = "unknown"
        reason = "exactly one completed command reading both synthetic targets was not confirmed"
    elif not markers_verified:
        status = "failed"
        reason = "model response did not exactly match both target EOF markers"
    else:
        status = "verified"
        reason = None
    report: dict[str, Any] = {
        "client": "codex-model",
        "status": status,
        "version": _version(executable),
        "model": model,
        "mode": "real Codex model; ephemeral; --approve-for-me; existing account auth",
        "completed_tool_calls": len(completed_commands),
        "tool_exit_code": completed.get("exit_code"),
        "synthetic_target_scope_verified": scope_verified,
        "root_target_bytes": root_target.stat().st_size,
        "root_target_eof_loaded": markers_verified,
        "nested_target_bytes": nested_target.stat().st_size,
        "nested_target_eof_loaded": markers_verified,
        "exact_marker_response": markers_verified,
    }
    if reason:
        report["reason"] = reason
    return report


def _claude_instructions(paths: dict[str, Path]) -> dict[str, Any]:
    """Compare Claude's real request assembly locally; never call a model."""
    executable = shutil.which("claude.exe") or shutil.which("claude")
    if not executable:
        return {"client": "claude", "status": "missing"}
    global_path = Path.home() / ".claude" / "CLAUDE.md"
    if not global_path.is_file():
        return {"client": "claude", "status": "unknown",
                "reason": f"global instruction file missing: {global_path}"}
    captured: list[bytes] = []

    class CaptureHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            if self.headers.get("Content-Encoding", "").lower() == "gzip":
                body = gzip.decompress(body)
            captured.append(body)
            response = json.dumps({
                "type": "error",
                "error": {"type": "invalid_request_error",
                          "message": "local instruction capture complete"},
            }).encode("utf-8")
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), CaptureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)
    env.update({
        "CLAUDE_CONFIG_DIR": str(paths["home"] / ".claude"),
        "ANTHROPIC_API_KEY": "synthetic-dummy-key",
        "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{server.server_port}",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "DISABLE_TELEMETRY": "1",
        "DISABLE_ERROR_REPORTING": "1",
        "NO_PROXY": "127.0.0.1,localhost",
        "no_proxy": "127.0.0.1,localhost",
    })
    for name in ("ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN",
                 "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
                 "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        env.pop(name, None)
    try:
        result = _run(
            [executable, "-p", "--output-format", "stream-json", "--verbose",
             "--setting-sources", "project", "--tools", "", "--strict-mcp-config",
             "--no-session-persistence", "--permission-mode", "dontAsk",
             "--permission-prompts", "none", "--disable-slash-commands", "PROBE_748"],
            paths["package"], env=env, timeout=30,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    if not captured:
        return {"client": "claude", "status": "failed",
                "reason": result.stderr.strip() or "no loopback request captured"}
    try:
        request = json.loads(captured[0].decode("utf-8"))
        visible = "\n".join(
            str(part.get("text", ""))
            for message in request.get("messages", [])
            for part in message.get("content", [])
            if isinstance(part, dict)
        ).replace("\r\n", "\n")
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
        return {"client": "claude", "status": "failed", "reason": str(exc)}
    global_bytes = global_path.read_bytes()
    global_text = global_bytes.decode("utf-8-sig").replace("\r\n", "\n").strip()
    global_lines = [line for line in global_text.splitlines() if line.strip()]
    missing = [
        {"line": index + 1, "bytes": len(line.encode("utf-8")),
         "sha256": hashlib.sha256(line.encode("utf-8")).hexdigest()}
        for index, line in enumerate(global_text.splitlines())
        if line.strip() and line not in visible
    ]
    omitted_delimiters = {
        "<!-- system-map:mermaid:start -->",
        "<!-- system-map:mermaid:end -->",
    }
    only_known_delimiters_omitted = all(
        global_text.splitlines()[item["line"] - 1].strip() in omitted_delimiters
        for item in missing
    )
    root_text = (paths["root"] / "CLAUDE.md").read_text(
        encoding="utf-8-sig").replace("\r\n", "\n").strip()
    nested_text = (paths["package"] / "CLAUDE.md").read_text(
        encoding="utf-8-sig").replace("\r\n", "\n").strip()
    global_eof = global_text[-256:] in visible
    root_full = root_text in visible
    nested_full = nested_text in visible
    verified = global_eof and only_known_delimiters_omitted and root_full and nested_full
    return {
        "client": "claude",
        "status": "verified" if verified else "failed",
        "mode": "dummy key; loopback request capture; no model endpoint or retained request",
        "request_bytes": len(captured[0]),
        "global": {
            "path": str(global_path.resolve()),
            "bytes": len(global_bytes),
            "sha256": hashlib.sha256(global_bytes).hexdigest(),
            "eof_loaded": global_eof,
            "nonempty_lines": len(global_lines),
            "nonempty_lines_loaded": len(global_lines) - len(missing),
            "missing_line_metadata": missing,
            "only_known_generated_delimiters_omitted": only_known_delimiters_omitted,
        },
        "root": {"bytes": len(root_text.encode("utf-8")), "full_and_eof_loaded": root_full},
        "nested": {"bytes": len(nested_text.encode("utf-8")), "full_and_eof_loaded": nested_full},
    }


def _cleanup(paths: dict[str, Path]) -> None:
    for checkout in (paths.get("root"), paths.get("home")):
        if checkout and checkout.exists():
            try:
                discovery.reconcile(checkout, "uninstall", paths.get("home"))
            except (OSError, ValueError, subprocess.SubprocessError):
                pass
    base = paths.get("home", Path()).parent
    for folder, directories, _files in os.walk(base, topdown=True, followlinks=False):
        for name in list(directories):
            path = Path(folder) / name
            if discovery.is_link(path):
                discovery._unlink(path)
                directories.remove(name)


def probe(instruction_proof: bool = False,
          codex_model: Optional[str] = None) -> dict[str, Any]:
    # Python 3.13+ gives TemporaryDirectory a Windows owner-only (0700) ACL.
    # A sandboxed CLI read then fails even though native discovery can read it.
    # This entirely synthetic fixture inherits the temp parent's normal ACL.
    temp_parent = Path(tempfile.gettempdir()).resolve()
    temporary = temp_parent / f"fleet-discovery-probe-{uuid.uuid4().hex}"
    temporary.mkdir(mode=0o777)
    paths: dict[str, Path] = {}
    try:
        paths = _setup(temporary)
        skills = [_claude(paths), _codex(paths), _pi(paths), _grok(paths)]
        instructions = [_codex_instructions(paths)]
        instructions.append(_claude_instructions(paths) if instruction_proof else {
            "client": "claude", "status": "not-probed",
            "reason": "opt in with --instruction-proof for local-only request assembly",
        })
        if codex_model:
            instructions.append(_codex_model_instructions(paths, codex_model))
        return {"fixture": "disposable synthetic files; known global compared locally when requested",
                "skills": skills, "instructions": instructions}
    finally:
        _cleanup(paths or {"home": temporary / "home", "root": temporary / "repo"})
        if temporary.resolve().parent != temp_parent or not temporary.name.startswith("fleet-discovery-probe-"):
            raise ValueError("refusing cleanup outside the owned disposable fixture")
        shutil.rmtree(temporary)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true",
                        help="run native installed-client probes in a disposable fixture")
    parser.add_argument("--instruction-proof", action="store_true",
                        help="also capture Claude's request on loopback and compare instruction EOFs locally")
    parser.add_argument(
        "--codex-model-proof", metavar="MODEL",
        help="explicitly run a real Codex model with existing auth to follow synthetic CLAUDE pointers",
    )
    args = parser.parse_args(argv)
    if args.codex_model_proof and not args.instruction_proof:
        parser.error("--codex-model-proof requires --instruction-proof")
    if not args.run:
        print(json.dumps({"status": "not-probed", "reason": "opt in with --run"}, indent=2))
        return 2
    result = probe(
        instruction_proof=args.instruction_proof,
        codex_model=args.codex_model_proof,
    )
    print(json.dumps(result, indent=2))
    statuses = {row["client"]: row["status"] for row in result["skills"]}
    if any(status == "failed" for status in statuses.values()):
        return 1
    if any(statuses.get(client) != "verified" for client in ("claude", "codex")):
        return 2
    if args.instruction_proof:
        by_client = {row["client"]: row for row in result["instructions"]}
        if by_client["claude"]["status"] != "verified":
            return 1
        if args.codex_model_proof:
            if (by_client["codex"]["status"] == "failed"
                    or by_client["codex-model"]["status"] != "verified"):
                return 1
        elif by_client["codex"]["status"] != "verified":
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
