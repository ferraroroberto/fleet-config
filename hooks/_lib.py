"""Shared helpers for the claude-config hooks.

Every hook in this directory:

* Reads a single JSON payload from stdin (Claude Code's hook contract).
* Returns exit code 0 to allow the action.
* Returns exit code 2 with a one-line reason on **stderr** to block the action
  (Claude sees the stderr and adjusts).
* Or returns exit code 0 with a single-line nudge on **stdout** to advise
  without blocking.

Use the helpers below so each hook stays a few dozen lines of pure rule logic.
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover - fallback for older Pythons
    import tomli as tomllib  # type: ignore[no-redef]


HOOKS_DIR = Path(__file__).resolve().parent
PROJECTS_TOML = HOOKS_DIR / "projects.toml"


# --------------------------------------------------------------------------- I/O


def read_stdin_json() -> Dict[str, Any]:
    """Read the hook payload from stdin and return it as a dict.

    Returns an empty dict if stdin is empty or unparseable — that lets the
    hook short-circuit to "allow" rather than crash inside Claude's tool loop.
    """
    raw = sys.stdin.read()
    if not raw or not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def block(reason: str) -> "NoReturn":
    """Exit 2 with a single-line reason on stderr → Claude treats it as a block."""
    print(reason, file=sys.stderr, flush=True)
    sys.exit(2)


def warn(message: str) -> "NoReturn":
    """Exit 0 with a single-line nudge on stdout → Claude sees the message but the action still runs."""
    print(message, flush=True)
    sys.exit(0)


def allow() -> "NoReturn":
    """Exit 0 silently → action proceeds, Claude sees nothing."""
    sys.exit(0)


# --------------------------------------------------------- Payload extraction


def tool_name(payload: Dict[str, Any]) -> str:
    return str(payload.get("tool_name") or "")


def tool_input(payload: Dict[str, Any]) -> Dict[str, Any]:
    ti = payload.get("tool_input")
    return ti if isinstance(ti, dict) else {}


def cwd(payload: Dict[str, Any]) -> Path:
    """Best-effort working directory for the call.

    Claude Code sends `cwd` in the payload; fall back to the process cwd if it's
    missing.
    """
    raw = payload.get("cwd")
    if isinstance(raw, str) and raw:
        return Path(raw)
    return Path(os.getcwd())


def command_string(payload: Dict[str, Any]) -> str:
    """Pull the executed command out of a Bash/PowerShell tool_input."""
    return str(tool_input(payload).get("command") or "")


def file_path(payload: Dict[str, Any]) -> Optional[Path]:
    """Pull the file path out of an Edit/Write tool_input, if present."""
    raw = tool_input(payload).get("file_path")
    if isinstance(raw, str) and raw:
        return Path(raw)
    return None


# ----------------------------------------------------------- projects.toml


@dataclass(frozen=True)
class ProjectConfig:
    name: str
    cwd_prefix: Path
    webapp_port: Optional[int]
    gate_trigger_globs: Sequence[str]
    gate_cmd: Optional[str]
    tray_cmd: Optional[str]
    restart_cmd: Optional[str]
    api_version_path: Optional[str]
    extra: Dict[str, Any]  # any other key from the [project] table


@dataclass(frozen=True)
class GlobalConfig:
    never_kill_ports: Sequence[int]
    slack_notify_channel: Optional[str] = None
    slack_notify_user: Optional[str] = None
    slack_notify_mention: bool = False


@dataclass(frozen=True)
class Registry:
    projects: List[ProjectConfig]
    globals: GlobalConfig


def _normalize(p: str) -> str:
    return str(Path(p)).replace("\\", "/").rstrip("/").lower()


def load_registry(path: Path = PROJECTS_TOML) -> Registry:
    if not path.exists():
        return Registry(projects=[], globals=GlobalConfig(never_kill_ports=()))

    with path.open("rb") as fh:
        data = tomllib.load(fh)

    globals_table = data.pop("global", {}) if isinstance(data.get("global"), dict) else {}
    never_kill = tuple(int(p) for p in globals_table.get("never_kill_ports", []))
    slack_channel = globals_table.get("slack_notify_channel") or None
    slack_user = globals_table.get("slack_notify_user") or None
    slack_mention = bool(globals_table.get("slack_notify_mention", False))

    projects: List[ProjectConfig] = []
    for name, table in data.items():
        if not isinstance(table, dict):
            continue
        prefix_raw = table.get("cwd_prefix")
        if not isinstance(prefix_raw, str) or not prefix_raw:
            continue
        webapp_port = table.get("webapp_port")
        projects.append(
            ProjectConfig(
                name=name,
                cwd_prefix=Path(prefix_raw),
                webapp_port=int(webapp_port) if webapp_port is not None else None,
                gate_trigger_globs=tuple(table.get("gate_trigger_globs", []) or []),
                gate_cmd=table.get("gate_cmd"),
                tray_cmd=table.get("tray_cmd"),
                restart_cmd=table.get("restart_cmd"),
                api_version_path=table.get("api_version_path"),
                extra={k: v for k, v in table.items() if k not in {
                    "cwd_prefix", "webapp_port", "gate_trigger_globs",
                    "gate_cmd", "tray_cmd", "restart_cmd", "api_version_path",
                }},
            )
        )

    return Registry(
        projects=projects,
        globals=GlobalConfig(
            never_kill_ports=never_kill,
            slack_notify_channel=slack_channel,
            slack_notify_user=slack_user,
            slack_notify_mention=slack_mention,
        ),
    )


def detect_project(cwd_path: Path, registry: Optional[Registry] = None) -> Optional[ProjectConfig]:
    """Pick the project whose `cwd_prefix` is the longest match of `cwd_path`."""
    reg = registry or load_registry()
    cwd_norm = _normalize(str(cwd_path))
    best: Optional[ProjectConfig] = None
    best_len = -1
    for project in reg.projects:
        pref_norm = _normalize(str(project.cwd_prefix))
        if cwd_norm == pref_norm or cwd_norm.startswith(pref_norm + "/"):
            if len(pref_norm) > best_len:
                best = project
                best_len = len(pref_norm)
    return best


def resolve_slack_target(
    cwd_path: Path, registry: Optional[Registry] = None
) -> "tuple[Optional[str], Optional[str], str]":
    """Resolve ``(channel, user, project_name)`` for a Slack ping from ``cwd_path``.

    A project's own ``slack_notify_channel`` / ``slack_notify_user`` override the
    ``[global]`` fallback; ``name`` is the project key, or ``"claude"`` when
    ``cwd_path`` matches no registered project. Shared by ``notify_on_idle`` (the
    hook) and ``notify_complete`` (the skill-completion helper) so both resolve
    the channel, mention, and project name identically.
    """
    reg = registry or load_registry()
    project = detect_project(cwd_path, reg)
    channel = (project.extra.get("slack_notify_channel") if project else None) or reg.globals.slack_notify_channel
    user = (project.extra.get("slack_notify_user") if project else None) or reg.globals.slack_notify_user
    name = project.name if project else "claude"
    return channel, user, name


# ------------------------------------------------------------------- .venv


def find_venv_python(start: Path) -> Optional[Path]:
    """Walk up from `start` looking for `.venv/Scripts/python.exe` (Windows) or `.venv/bin/python`."""
    candidates_rel = (
        Path(".venv") / "Scripts" / "python.exe",
        Path(".venv") / "bin" / "python",
    )
    for parent in [start, *start.parents]:
        for rel in candidates_rel:
            candidate = parent / rel
            if candidate.exists():
                return candidate
    return None

