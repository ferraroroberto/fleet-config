"""``projects.toml`` registry + notify-target routing (fleet-config#819).

Split out of ``_lib.py`` (formerly ~1300 lines covering a dozen unrelated
concerns) so the project-registry concern — load ``projects.toml``, match a
``cwd`` against a project's ``cwd_prefix``, resolve which Telegram chat and
which Fleet Board URL a ping should use — lives in one focused module instead
of being one more section in the file every hook imports. No hook's import
line changes: ``_lib.py`` re-exports every name below, so ``_lib.load_registry``,
``_lib.detect_project``, ``_lib.resolve_notify_target`` etc. keep working
unchanged (see the re-export block near the end of ``_lib.py``).

This module has no dependency on ``_lib`` itself — the dependency runs one way
(``_lib`` imports this, never the reverse) so there is no import-order hazard.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover - fallback for older Pythons
    import tomli as tomllib  # type: ignore[no-redef]


HOOKS_DIR = Path(__file__).resolve().parent
PROJECTS_TOML = HOOKS_DIR / "projects.toml"
PROJECTS_TOML_ENV_VAR = "CLAUDE_HOOKS_PROJECTS_TOML"


@dataclass(frozen=True)
class ProjectConfig:
    name: str
    cwd_prefix: Path
    webapp_port: Optional[int]
    tray_cmd: Optional[str]
    restart_cmd: Optional[str]
    api_version_path: Optional[str]
    extra: Dict[str, Any]  # any other key from the [project] table


@dataclass(frozen=True)
class GlobalConfig:
    never_kill_ports: Sequence[int]
    telegram_chat: Optional[str] = None
    # Per-category chats (issue #139). A ping carries a category — "attention"
    # ("come look": blocked / awaiting input / ready-to-validate) vs "log"
    # (activity record: filed / shipped / merged / digests). When the category's
    # chat is unset, routing falls back to `telegram_chat`, so a single chat
    # keeps working and the split can roll out one chat at a time.
    telegram_chat_attention: Optional[str] = None
    telegram_chat_log: Optional[str] = None
    # Base URL for the app-launcher Fleet Board (fleet-config#242), e.g. a
    # Tailscale address so a phone tap resolves outside the LAN. Unset by
    # default — notify_on_idle omits the board deep-link line entirely until
    # this is configured (see resolve_board_url).
    board_url: Optional[str] = None


@dataclass(frozen=True)
class Registry:
    projects: List[ProjectConfig]
    globals: GlobalConfig


def _normalize(p: str) -> str:
    return str(Path(p)).replace("\\", "/").rstrip("/").lower()


def load_registry(path: Optional[Path] = None) -> Registry:
    """Load the project registry from ``projects.toml``.

    ``CLAUDE_HOOKS_PROJECTS_TOML`` overrides the path (same pattern as
    ``notify_send``'s ``CLAUDE_SETTINGS_JSON_PATH``) so acceptance tests can
    point this at a throwaway file with a ``cwd_prefix`` under a temp dir,
    instead of writing test fixtures into the real fleet paths.
    """
    if path is None:
        path = Path(os.environ.get(PROJECTS_TOML_ENV_VAR) or PROJECTS_TOML)
    if not path.exists():
        return Registry(projects=[], globals=GlobalConfig(never_kill_ports=()))

    with path.open("rb") as fh:
        data = tomllib.load(fh)

    globals_table = data.pop("global", {}) if isinstance(data.get("global"), dict) else {}
    never_kill = tuple(int(p) for p in globals_table.get("never_kill_ports", []))
    telegram_chat = globals_table.get("telegram_chat") or None
    telegram_attention = globals_table.get("telegram_chat_attention") or None
    telegram_log = globals_table.get("telegram_chat_log") or None
    board_url = globals_table.get("board_url") or None

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
                tray_cmd=table.get("tray_cmd"),
                restart_cmd=table.get("restart_cmd"),
                api_version_path=table.get("api_version_path"),
                extra={k: v for k, v in table.items() if k not in {
                    "cwd_prefix", "webapp_port",
                    "tray_cmd", "restart_cmd", "api_version_path",
                }},
            )
        )

    return Registry(
        projects=projects,
        globals=GlobalConfig(
            never_kill_ports=never_kill,
            telegram_chat=telegram_chat,
            telegram_chat_attention=telegram_attention,
            telegram_chat_log=telegram_log,
            board_url=board_url,
        ),
    )


_WORKTREE_SUFFIX_RE = re.compile(r"-wt-\d+$")


def _strip_worktree_suffix(normalized_path: str) -> str:
    """Strip a trailing `-wt-<N>` suffix from every path segment.

    `worktree_claim.py`'s sibling-worktree naming (`<repo>-wt-<N>`) has no
    path separator before the suffix, so it never prefix-matches the
    primary checkout's `cwd_prefix` as-is (fleet-config#471).
    """
    return "/".join(_WORKTREE_SUFFIX_RE.sub("", segment) for segment in normalized_path.split("/"))


def _match_project(cwd_norm: str, projects: List[ProjectConfig]) -> Optional[ProjectConfig]:
    best: Optional[ProjectConfig] = None
    best_len = -1
    for project in projects:
        pref_norm = _normalize(str(project.cwd_prefix))
        if cwd_norm == pref_norm or cwd_norm.startswith(pref_norm + "/"):
            if len(pref_norm) > best_len:
                best = project
                best_len = len(pref_norm)
    return best


def detect_project(cwd_path: Path, registry: Optional[Registry] = None) -> Optional[ProjectConfig]:
    """Pick the project whose `cwd_prefix` is the longest match of `cwd_path`.

    Tries the raw cwd first so a repo whose real name happens to contain a
    `-wt-<N>`-shaped segment still matches directly; only falls back to a
    worktree-suffix-stripped retry when the raw path matches nothing, so a
    `<repo>-wt-<N>` sibling worktree resolves to the same project as its
    primary checkout.
    """
    reg = registry or load_registry()
    cwd_norm = _normalize(str(cwd_path))
    match = _match_project(cwd_norm, reg.projects)
    if match is not None:
        return match
    return _match_project(_strip_worktree_suffix(cwd_norm), reg.projects)


# A ping's intent category → the projects.toml chat key that routes it
# (issue #139). Both keys are valid as a [global] entry and as a per-project
# override. An unset category chat falls back to `telegram_chat`.
NOTIFY_CATEGORY_KEYS = {
    "attention": "telegram_chat_attention",
    "log": "telegram_chat_log",
}


def resolve_notify_target(
    cwd_path: Path,
    registry: Optional[Registry] = None,
    *,
    category: Optional[str] = None,
) -> "tuple[Optional[str], str]":
    """Resolve ``(chat, project_name)`` for a Telegram ping from ``cwd_path``.

    A project's own override wins over the ``[global]`` fallback at every level;
    ``name`` is the project key, or ``"claude"`` when ``cwd_path`` matches no
    registered project. Shared by ``notify_on_idle`` (the hook) and
    ``notify_complete`` (the skill-completion helper) so both resolve the chat
    and project name identically.

    ``category`` ("attention" / "log", issue #139) routes the ping to a
    dedicated chat: the per-category key is tried first (project override, then
    ``[global]``); when it is unset the chat **falls back to ``telegram_chat``**.
    That fallback is what keeps a single-chat setup working unchanged and lets
    the split roll out one chat at a time.

    No mention/user leg: Slack needed an ``<@user>`` tag to guarantee a mobile
    push, so this returned a user id too. Telegram pushes every message to a
    chat you are a member of, which made the whole mention path dead weight —
    dropped in fleet-config#540 rather than ported.
    """
    reg = registry or load_registry()
    project = detect_project(cwd_path, reg)

    def pick(key: str, global_value: Optional[str]) -> Optional[str]:
        return (project.extra.get(key) if project else None) or global_value

    chat: Optional[str] = None
    cat_key = NOTIFY_CATEGORY_KEYS.get(category) if category else None
    if cat_key:
        chat = pick(cat_key, getattr(reg.globals, cat_key, None))
    if not chat:
        chat = pick("telegram_chat", reg.globals.telegram_chat)

    name = project.name if project else "claude"
    return chat, name


BOARD_URL_ENV_VAR = "FLEET_BOARD_URL"
DOTENV_PATH_ENV_VAR = "FLEET_CONFIG_ENV_PATH"


def _dotenv_value(key: str) -> Optional[str]:
    """Read one value from fleet-config's ignored root ``.env``; never raise."""
    default_path = Path(__file__).resolve().parent.parent / ".env"
    path = Path(os.environ.get(DOTENV_PATH_ENV_VAR) or default_path)
    try:
        for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            candidate, value = line.split("=", 1)
            if candidate.strip() == key:
                return value.strip().strip("\"'") or None
    except OSError:
        return None
    return None


def resolve_board_url(cwd_path: Path, registry: Optional[Registry] = None) -> Optional[str]:
    """Resolve the app-launcher Fleet Board base URL for a ``?board=<sid>`` deep
    link (fleet-config#242). Precedence: a project's own ``board_url`` override,
    then the ``FLEET_BOARD_URL`` environment variable, fleet-config's ignored
    root ``.env``, then committed ``[global] board_url``. ``None`` means the
    caller must omit the link line, never guess a URL.

    The real value (a Tailscale hostname) is set via ``FLEET_BOARD_URL``, not
    ``[global] board_url``, because fleet-config is a **public** repo
    (fleet-config#271) — same reasoning as ``TELEGRAM_BOT_TOKEN`` staying out of
    ``projects.toml``. Claude Code always injects its ``env`` block into hook
    subprocesses. The ignored ``.env`` fallback makes the same private value
    available to Codex and other launchers that do not inject Claude settings.
    """
    reg = registry or load_registry()
    project = detect_project(cwd_path, reg)
    project_value = project.extra.get("board_url") if project else None
    return (project_value or os.environ.get(BOARD_URL_ENV_VAR)
            or _dotenv_value(BOARD_URL_ENV_VAR) or reg.globals.board_url)
