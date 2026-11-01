"""Handoff-then-compact for chief-dispatched lanes at a per-model context threshold.

Claude Code compacts on its own only near the window limit, at whatever moment
that happens to be, and keeps a generic summary; accuracy falls well before
then. A lane instead stops at a turn end past its threshold, writes a handoff
of its own, and compacts; the handoff comes back after the compaction. This is
the lane counterpart of the chief's own threshold hold in `chief_waiter_stop`
and its handover re-injection in `chief_handover_sessionstart`, and both of
those existing hook entries carry it, so no new settings wiring is needed:

- :func:`stop_refusal` (called by `chief_waiter_stop` for every non-chief
  session) holds a lane's turn end once per window when its context is at or
  over the threshold, telling it to write the handoff and run
  `chief_ops.py lane-compact`, which queues `/compact` behind its own refusals.
- :func:`resume_context` (called by `chief_handover_sessionstart`) hands the
  handoff back when the lane's session restarts after a compaction.

The context reading is the per-session snapshot `statusline-command.ps1`
writes from the statusline payload (`session-context/<session_id>.json`:
percent, window size, model id), so the common "not due" answer costs a few
small file reads and no network. Thresholds come from `thresholds.toml` beside
this file. A running gate (`gate_evidence.py run` keeps a `gate-inflight/`
marker for its lifetime) means no hold: the lane is waiting on it.

Everything fails open: a missing snapshot, an unreadable config or registry, a
marker that cannot be written all allow the turn end and log why. An unknown
gate state counts as a running gate, so it never compacts on a guess.
"""

from __future__ import annotations

import ctypes
import json
import logging
import os
import re
import sys
import time
import tomllib
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import _lib  # noqa: E402

logger = logging.getLogger("fleet_hooks")

CONFIG_PATH = Path(__file__).resolve().parent / "thresholds.toml"
SNAPSHOT_DIRNAME = "session-context"
HANDOFF_DIRNAME = "lane-handoffs"
GATE_DIRNAME = "gate-inflight"
NUDGE_DIRNAME = "lane-compact-nudge"
# Written by `chief_ops.py lane-compact` (skills/_lib/chief/commands.py); the
# tree boundary forbids importing the name, so the acceptance suite holds the
# two spellings together.
QUEUED_DIRNAME = "lane-compact-queued"
# One hold per window, and silence for a window after a queued compaction: the
# compaction runs when the turn ends, while the percentage still reads high.
DEBOUNCE_S = 600
# The statusline re-renders on every message; a snapshot older than this
# describes a session that has gone quiet, not this turn end.
SNAPSHOT_MAX_AGE_S = 900
# A handoff older than this was not written for the compaction that just ran
# (an operator's own later `/compact`, say) and is not re-injected.
RESUME_MAX_AGE_S = 3600
# additionalContext has a ~10K-char ceiling; the chief's handover shares it.
MAX_INLINE_CHARS = 6000
_SID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259


def pid_alive(pid: int) -> bool:
    """Is process `pid` running? Never `os.kill(pid, 0)`: on Windows that terminates it."""
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except OSError:
            return True
        return True
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == _STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def _safe_name(value: str) -> Optional[str]:
    return value if _SID_RE.match(value or "") else None


def handoff_path(launcher_sid: str) -> Path:
    return _lib.state_dir() / HANDOFF_DIRNAME / f"{launcher_sid}.md"


def load_config(path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """The thresholds table, or None (logged) when it is unreadable or turned off."""
    path = path or CONFIG_PATH
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("lane_compact: %s unreadable (%s) -- lane hold off", path, exc)
        return None
    if data.get("enabled") is False:
        return None
    return data


def _valid_percent(value: object) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 99:
        return None
    return value


def threshold_for(config: Dict[str, Any], model_id: str, window: Optional[int]) -> Optional[int]:
    """The threshold for this model and window: model id, then window size, then default."""
    models = config.get("models") if isinstance(config.get("models"), dict) else {}
    if model_id:
        if _valid_percent(models.get(model_id)) is not None:
            return models[model_id]
        prefixes = sorted((k for k in models if model_id.startswith(k)), key=len, reverse=True)
        for key in prefixes:
            if _valid_percent(models[key]) is not None:
                return models[key]
    windows = config.get("windows") if isinstance(config.get("windows"), dict) else {}
    if window is not None and _valid_percent(windows.get(str(window))) is not None:
        return windows[str(window)]
    return _valid_percent(config.get("default"))


def read_snapshot(session_id: str, now: float) -> Tuple[Optional[Dict[str, Any]], str]:
    """`(snapshot, "")` for a fresh reading with a percentage, else `(None, why)`."""
    name = _safe_name(session_id)
    if name is None:
        return None, "no session id"
    path = _lib.state_dir() / SNAPSHOT_DIRNAME / f"{name}.json"
    try:
        age = now - path.stat().st_mtime
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return None, "no statusline snapshot"
    except (OSError, ValueError) as exc:
        return None, f"snapshot unreadable ({type(exc).__name__})"
    if age > SNAPSHOT_MAX_AGE_S:
        return None, f"snapshot stale ({int(age)}s)"
    percent = data.get("used_percentage") if isinstance(data, dict) else None
    if isinstance(percent, bool) or not isinstance(percent, (int, float)):
        return None, "percent not reported"
    window = data.get("context_window_size")
    return {"percent": int(round(percent)), "model": str(data.get("model_id") or ""),
            "window": window if isinstance(window, int) and not isinstance(window, bool) else None}, ""


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path)).rstrip("\\/")


def gate_in_flight(cwd: str) -> Optional[bool]:
    """Is a `gate_evidence.py run` live for the tree holding `cwd`? None when unreadable."""
    directory = _lib.state_dir() / GATE_DIRNAME
    try:
        entries = list(directory.glob("*.json"))
    except OSError:
        return None
    here = _norm(cwd) if cwd else ""
    for entry in entries:
        try:
            row = json.loads(entry.read_text(encoding="utf-8"))
        except FileNotFoundError:
            continue  # the gate finished between the listing and the read
        except (OSError, ValueError):
            return None
        if not isinstance(row, dict):
            return None
        repo, pid = str(row.get("repo") or ""), row.get("pid")
        if not repo or not here or not (here == _norm(repo) or here.startswith(_norm(repo) + os.sep)):
            continue
        if isinstance(pid, int) and not isinstance(pid, bool) and not pid_alive(pid):
            continue  # a gate that died without removing its marker
        return True
    return False


def _recent(path: Path, now: float) -> bool:
    try:
        return now - path.stat().st_mtime < DEBOUNCE_S
    except OSError:
        return False


def _command(notify_on_idle: Any) -> str:
    return f"{notify_on_idle.CHIEF_OPS_PYTHON.as_posix()} {notify_on_idle.CHIEF_OPS.as_posix()} lane-compact"


def stop_refusal(payload: Dict[str, Any], now: Optional[float] = None) -> Optional[str]:
    """The message that holds a lane's turn end for a handoff and a compaction, or None."""
    clock = time.time() if now is None else now
    sid = _safe_name(_lib.launcher_session_id())
    if sid is None:
        return None
    # Imported here, not at module level: the SessionStart side never needs it.
    import notify_on_idle  # noqa: PLC0415

    managed, reason = notify_on_idle.chief_managed_state()
    if not managed:
        if reason == notify_on_idle.CHIEF_UNDETERMINED:
            logger.warning("lane_compact: chief-managed registry unreadable -- allowing the turn end")
        return None
    state = _lib.state_dir()
    if _recent(state / NUDGE_DIRNAME / sid, clock) or _recent(state / QUEUED_DIRNAME / sid, clock):
        return None
    config = load_config()
    if config is None:
        return None
    snapshot, why = read_snapshot(str(payload.get("session_id") or ""), clock)
    if snapshot is None:
        logger.info("lane_compact: context use unknown for lane %s (%s) -- allowing the turn end", sid[:8], why)
        return None
    threshold = threshold_for(config, snapshot["model"], snapshot["window"])
    if threshold is None or snapshot["percent"] < threshold:
        return None
    running = gate_in_flight(_lib.cwd(payload))
    if running is not False:
        logger.info("lane_compact: lane %s over threshold but a gate is %s -- not holding",
                    sid[:8], "running" if running else "in an unknown state")
        return None
    try:
        (state / NUDGE_DIRNAME).mkdir(parents=True, exist_ok=True)
        (state / NUDGE_DIRNAME / sid).write_text(str(int(clock)), encoding="utf-8")
    except OSError as exc:
        # Without the marker every turn end would hold again: skip the hold rather than risk a loop.
        logger.warning("lane_compact: cannot write the nudge marker (%s) -- allowing the turn end", exc)
        return None
    model = snapshot["model"] or "unknown model"
    return (
        f"Context is at {snapshot['percent']}% ({model}; lane compact threshold {threshold}%). "
        "Hand off and compact now, at this turn end:\n"
        f"1. Write the handoff to {handoff_path(sid).as_posix()} with the Write tool, under 6,000 characters: "
        "the issue and its branch/worktree; what is done (commits, PRs, gate results); what is in flight; "
        "the next concrete steps; decisions taken and why; open questions; the brief's constraints that still apply.\n"
        f"2. Run `{_command(notify_on_idle)}` and end the turn on COMPACT_QUEUED.\n"
        "If a step is mid-flight, finish it first. The handoff is handed back to you after the compaction."
    )


def resume_context(payload: Dict[str, Any], now: Optional[float] = None) -> Optional[str]:
    """The lane's handoff, after a compaction of its own session, or None."""
    if str(payload.get("source") or "") != "compact":
        return None
    sid = _safe_name(_lib.launcher_session_id())
    if sid is None:
        return None
    path = handoff_path(sid)
    clock = time.time() if now is None else now
    try:
        if clock - path.stat().st_mtime > RESUME_MAX_AGE_S:
            return None
        content = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not content:
        return None
    if len(content) > MAX_INLINE_CHARS:
        content = (f"{content[:MAX_INLINE_CHARS]}\n\n(handoff truncated at {MAX_INLINE_CHARS} chars; "
                   f"read the rest at {path.as_posix()})")
    return ("Lane handoff written before this compaction -- continue from it. Live repo and GitHub "
            f"state win on facts; this wins on intent and next steps.\n\n{content}")
