"""Grok Build's status line, and the opt-in merge that turns it on.

Grok pipes a JSON payload to a ``type = "command"`` status line on stdin
(``~/.grok/docs/user-guide/25-status-line.md``). Rendered here in the Claude
status line's shape, usage first so it survives a narrow pane::

    12%c $0.42 | Claude 5%s 10%w | Grok 4.5 high | app-launcher (main)

Grok exposes no session or weekly allowance to scripts, so no Grok limit is
ever shown. The limits on the row are Claude's, read from the shared quota
snapshot (``docs/quota-snapshots.md``) and labelled as Claude's; a window
that is not fresh reads ``?``, never a number.

``--apply`` adds a command-type ``[ui.status_line]`` to ``~/.grok/config.toml``
only when the file has none, appending one table and keeping every other byte.
A row the user already set, enabled or disabled, is left as it is.
``install.ps1 -ConfigureGrokStatusline`` calls it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tomllib
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "skills" / "_lib"))
from config_edit import atomic_write  # noqa: E402
from quota_snapshot import read_snapshot  # noqa: E402

ESC = "\x1b"
# Grok's own context item turns amber here when the agent reports no
# auto-compaction threshold.
DEFAULT_COMPACT_PCT = 80
# Grok hides a cost below half a cent so it never shows a misleading $0.00.
MIN_COST_USD = 0.005
CLAUDE_PRODUCER = "claude-statusline"


class ConfigError(ValueError):
    """Raised when the Grok config cannot be safely updated."""


def _colored(text: str, color: Optional[str]) -> str:
    return f"{ESC}[{color}m{text}{ESC}[0m" if color else text


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _context(payload: dict) -> str:
    window = payload.get("context_window") or {}
    used = _number(window.get("used_percentage"))
    if used is None:
        return ""
    threshold = _number(window.get("auto_compact_threshold_percent")) or DEFAULT_COMPACT_PCT
    pct = round(used)
    color = "31" if pct >= threshold - 15 else "33" if pct >= threshold - 20 else "32"
    return _colored(f"{pct}%c", color)


def _cost(payload: dict) -> str:
    # Absent means unknown (no price yet, or the ledger is unreadable), not zero.
    usd = _number((payload.get("cost") or {}).get("total_cost_usd"))
    return f"${usd:.2f}" if usd is not None and usd >= MIN_COST_USD else ""


def _limit(window: Optional[dict], suffix: str) -> str:
    pct = _number((window or {}).get("used_percentage"))
    if not window or window.get("state") != "available" or pct is None:
        return f"?%{suffix}"
    pct = round(pct)
    return _colored(f"{pct}%{suffix}", "31" if pct >= 80 else "33" if pct >= 60 else None)


def claude_limits(snapshot: dict) -> str:
    """Claude's session and weekly use, labelled as Claude's; ``?`` when not fresh."""
    source = next((s for s in snapshot.get("sources", []) if s.get("producer") == CLAUDE_PRODUCER), {})
    windows: dict = {}
    if source.get("state") == "available":
        for observation in source.get("observations", []):
            if observation.get("state") == "available":
                windows = {w.get("id"): w for w in observation.get("windows", [])}
                break
    return f"Claude {_limit(windows.get('five_hour'), 's')} {_limit(windows.get('seven_day'), 'w')}"


def _model(payload: dict) -> str:
    name = (payload.get("model") or {}).get("display_name") or ""
    effort = (payload.get("effort") or {}).get("level") or ""
    return " ".join(part for part in (name, effort) if part)


def _place(payload: dict) -> str:
    workspace = payload.get("workspace") or {}
    worktree = (payload.get("worktree") or {}).get("name") or workspace.get("git_worktree")
    folder = worktree or workspace.get("repo_root") or workspace.get("current_dir") or payload.get("cwd") or ""
    name = Path(folder).name if folder else ""
    branch = workspace.get("branch")
    return f"{name} ({branch})" if name and branch else name


def render(payload: dict, snapshot: dict) -> str:
    """The one status row for ``payload``."""
    usage = " ".join(part for part in (_context(payload), _cost(payload)) if part)
    segments = (usage, claude_limits(snapshot), _model(payload), _place(payload))
    return " | ".join(segment for segment in segments if segment)


def status_line_command() -> str:
    """This script under this interpreter, as Grok's command line."""
    return f"{Path(sys.executable).as_posix()} {Path(__file__).resolve().as_posix()}"


def merge_status_line(text: str, command: str) -> tuple[str, str]:
    """``text`` with a command-type ``[ui.status_line]``, and what happened.

    ``added`` appends one table; ``unchanged`` means this exact row is already
    set; ``kept-user-row`` means another row is set and is left alone.
    """
    try:
        current = tomllib.loads(text).get("ui", {})
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"config.toml is not valid TOML: {exc}") from exc
    if not isinstance(current, dict):
        raise ConfigError("ui is not a table")
    existing = current.get("status_line")
    if existing is not None:
        same = isinstance(existing, dict) and existing.get("type") == "command" and existing.get("command") == command
        return text, "unchanged" if same else "kept-user-row"
    newline = "\r\n" if "\r\n" in text else "\n"
    lead = "" if not text else (newline if text.endswith(("\n", "\r")) else newline * 2)
    table = newline.join(("[ui.status_line]", 'type = "command"', f"command = {json.dumps(command)}", ""))
    updated = text + lead + table
    try:
        tomllib.loads(updated)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"adding [ui.status_line] would break config.toml: {exc}") from exc
    return updated, "added"


def default_grok_config_path() -> Path:
    home = os.environ.get("GROK_HOME", "").strip()
    return (Path(home) if home else Path.home() / ".grok") / "config.toml"


def _read(path: Path) -> str:
    if not path.exists():
        return ""
    with path.open("r", encoding="utf-8", newline="") as handle:
        return handle.read()


def _configure(args: argparse.Namespace) -> int:
    try:
        original = _read(args.config)
        updated, state = merge_status_line(original, status_line_command())
        if state == "added" and args.check:
            state = "update-needed"
        elif state == "added":
            atomic_write(args.config, updated)
    except (OSError, ConfigError) as exc:
        print(f"GROK_STATUSLINE status=error detail={exc}")
        return 1
    print(f"GROK_STATUSLINE status={state} path={args.config}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--apply", action="store_true", help="add the row to Grok's config.toml")
    actions.add_argument("--check", action="store_true", help="report whether --apply would change it")
    parser.add_argument("--config", type=Path, default=default_grok_config_path(), help="Grok config.toml path")
    args = parser.parse_args()
    if args.apply or args.check:
        return _configure(args)
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8") or "{}")
    except (ValueError, UnicodeDecodeError):
        payload = {}
    print(render(payload if isinstance(payload, dict) else {}, read_snapshot()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
