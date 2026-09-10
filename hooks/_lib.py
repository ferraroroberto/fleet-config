"""Shared helpers for the fleet-config hooks.

Every hook in this directory:

* Reads a single JSON payload from stdin (Claude Code's hook contract).
* Returns exit code 0 to allow the action.
* Refuses through block(): Claude uses exit 2 + stderr; Codex PreToolUse
  uses a structured deny + exit 0; Grok adds its own structured deny.
* Or returns exit code 0 with a single-line nudge on **stdout** to advise
  without blocking.

Use the helpers below so each hook stays a few dozen lines of pure rule logic.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

STATE_DIR_ENV_VAR = "CLAUDE_HOOKS_STATE_DIR"

logger = logging.getLogger("fleet_hooks")


def state_dir() -> Path:
    """The shared hooks-state base directory, resolved at call time so the
    ``CLAUDE_HOOKS_STATE_DIR`` override always wins (hermetic acceptance runs
    depend on it). Every hook that persists advisory state under
    ``~/.claude/hooks/state/`` derives its own filename from this rather than
    re-deriving the lookup itself -- eight independent copies of exactly this
    (fleet-config#817) is the failure mode a ninth caller getting it wrong
    guards against."""
    root = os.environ.get(STATE_DIR_ENV_VAR)
    return Path(root) if root else Path.home() / ".claude" / "hooks" / "state"


# ------------------------------------------------------- credential patterns

# The one definition of "what a live credential looks like" for this tier
# (fleet-config#561). Two independent copies used to exist — `context_filter`'s
# four-family redaction regex and `secret_scan_guard`'s one-family commit
# blocker — and the *narrower* one was the copy wired into the guard that
# actually refuses a commit. So the guard blocked a leaked Telegram bot token and
# waved through an OpenAI key, a GitHub PAT, and an AWS access key id. Both now
# read from here, so extending coverage is a one-line change in one place.
#
# `\b` anchors every pattern: without them `sk-` matches inside `risk-…` and
# `gh?_` inside `highp_…`, which is tolerable for a redactor (a false positive
# just redacts a word) but not for a guard that refuses `git commit`. Verified
# against every tracked file in every repo under `E:/automation`: zero matches.
#
# Deliberately live-shaped, not prefix-shaped, so this repo's own docs — which
# legitimately carry the placeholder forms `xoxb-…` and `xoxb-<token>` — never
# trip the guard. A real token has a long secret body; the placeholders don't.
#
# The Slack pattern requires the **three** hyphen-separated groups every real
# Slack token carries (`xoxb-<team>-<bot>-<secret>`, `xoxp-`/`xoxa-` likewise),
# rather than a bare "prefix plus 16 characters". Interior groups are `+` — an
# `xoxa-` app token's second group is a single digit — but the trailing secret
# must be 8+. Without the three-group requirement a *test fixture* naming a
# plausible-looking fake token becomes uncommittable, which is how this pattern
# first blocked its own repo (fleet-config#561).
SECRET_PATTERNS: tuple[tuple[str, str], ...] = (
    ("Slack token (xox…-)", r"\bxox[baprs]-[A-Za-z0-9]+-[A-Za-z0-9]+-[A-Za-z0-9]{8,}"),
    # Telegram bot token: `<bot_id>:<35-char secret>` (fleet-config#540). Added
    # rather than swapping out the Slack entry — this tuple is the tier's general
    # credential list, not a Slack list, and the `xoxb-` token stays live until
    # the workspace is actually decommissioned. The secret half is exactly 35
    # characters, so the length is pinned rather than `{20,}`: a looser rule
    # matches ordinary `HH:MM`-adjacent digit-colon-text runs in a log tail.
    ("Telegram bot token", r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b"),
    ("API key (sk-)", r"\bsk-[A-Za-z0-9_-]{20,}"),
    ("GitHub token (gh?_)", r"\bgh[pousr]_[A-Za-z0-9_]{20,}"),
    ("AWS access key id (AKIA…)", r"\bAKIA[0-9A-Z]{16}"),
)

# The same tuple as one alternation, for redaction (`SECRET_RE.sub(...)`) and
# for "does this output look secret-bearing" tests.
SECRET_RE = re.compile("(" + "|".join(pattern for _, pattern in SECRET_PATTERNS) + ")")


def scan_for_secret(text: str) -> Optional["tuple[str, str]"]:
    """Return ``(label, pattern)`` of the first credential found in ``text``, else ``None``."""
    for label, pattern in SECRET_PATTERNS:
        if re.search(pattern, text):
            return label, pattern
    return None


# ------------------------------------------------------- subprocess spawning

# Pass this as `creationflags=` on **every** subprocess spawn in this directory,
# per the global CLAUDE.md convention "Subprocess spawns must suppress the
# console window (Windows)" (fleet-config#399): a parent with no console of its
# own — pythonw, a tray app, a scheduled task, a daemon — otherwise gets a
# console window flashed on screen for each spawn. Hooks fire under exactly such
# parents, including the headless `claude -p` of every scheduled fleet job.
#
# `subprocess.CREATE_NO_WINDOW` is Windows-only; the conditional expression
# evaluates the platform test first, so the attribute is never touched on POSIX.
# The skill tier keeps its own copy in `skills/_lib/no_window.py` (the two trees
# are junctioned into the agent homes independently, and a hook must stay
# importable with nothing but its own directory on `sys.path`);
# `tests/run_acceptance.py` asserts the two agree. Never combine this with
# `DETACHED_PROCESS` — mutually exclusive (`local-llm-hub`#282).
NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


# ------------------------------------------------------- argv text repair

# Free text that reaches a hook as a **command-line argument** has crossed the
# harness → shell → CreateProcess boundary, and on Windows that boundary is not
# UTF-8 safe end to end. Reproduced on this host (fleet-config#507): a BOM-less
# UTF-8 command handed to Windows PowerShell 5.1 is decoded with the ANSI
# codepage, so the two UTF-8 bytes of `·` (0xC2 0xB7) arrive as the two
# characters `Â·`; a further narrowing to an OEM codepage that has neither turns
# the pair into `??`, which is what landed in the chat.
#
# Two prior instances of the same class already carry fixes on adjacent paths —
# `notify_complete.gh_json` (gh stdout forced to UTF-8) and
# `notify_send._read_text` (piped stdin forced to UTF-8). Those cover *byte*
# streams we own. This covers the argv leg, which we do not own: the only two
# defences are (a) repair the recoverable half here, and (b) never author
# non-ASCII punctuation into an argv string in the first place — skills spell the
# separator with the ASCII token instead (see `notify_complete.normalize_summary`).
_MOJIBAKE_MARKERS = ("Â", "Ã", "â€", "Å", "Ë", "Ð", "ð\x9f")


def repair_mojibake(text: Optional[str]) -> Optional[str]:
    """Undo a UTF-8-bytes-decoded-as-cp1252 round trip (``"Â·"`` → ``"·"``).

    Only rewrites text that both *looks* mojibake-encoded (carries one of the
    telltale Latin-1 lead characters) and survives the round trip cleanly, so
    genuine accented prose — where the cp1252 re-encode produces bytes that are
    not valid UTF-8 — is returned untouched. ASCII and ``None`` short-circuit.

    Irrecoverable by design: once the boundary has replaced a character with
    ``?`` the original codepoint is gone, which is why the ASCII separator token
    exists alongside this repair rather than instead of it.
    """
    if not text or text.isascii():
        return text
    if not any(marker in text for marker in _MOJIBAKE_MARKERS):
        return text
    try:
        repaired = text.encode("cp1252").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text
    return repaired


# ------------------------------------------- foreign-harness payload normalization

# Grok Build (xAI's CLI) scans `~/.claude/settings.json` for hooks by default
# (`[compat.claude] hooks = true`), so every hook in this directory already runs
# inside a Grok session — verified live against grok 0.2.114: its debug log
# reports `hooks: loaded from global source source=...settings.json count=19`
# and then actually executes them. But Grok's stdin envelope is **camelCase**
# (`hookEventName` / `toolName` / `toolInput`) where Claude Code's is snake_case,
# and its event *values* are lower_snake (`pre_tool_use`) where Claude's are
# PascalCase (`PreToolUse`). The result was a double mismatch that made the hooks
# fire and silently do nothing: 6 of 7 guards A/B-tested blocked under a Claude
# payload and allowed the identical dangerous command under a Grok one, while
# still looking healthy in `/hooks` (fleet-config#491).
#
# Normalizing here — the one entry point every hook already routes through —
# fixes all of them at once, rather than duplicating a translation in each hook
# or shipping a parallel Grok adapter per hook. A payload that is already in
# Claude shape is returned **unchanged and identical** (same object), so this is
# a strict pass-through for Claude Code and cannot alter existing behaviour.

AGENT_HINT_KEY = "_fleet_agent"
SHELL_AMBIGUOUS_KEY = "_fleet_shell_ambiguous"

# Grok exposes exactly one shell tool, which can run PowerShell *or* bash syntax
# (nothing stops it invoking `bash -c`). Claude Code splits `Bash` and
# `PowerShell` into separate tools, and `safe_kill_guard` relies on that split to
# avoid false-positiving on an `echo` of the other shell's kill string. Rather
# than guess a shell we cannot observe, normalization flags the ambiguity and the
# one guard that discriminates widens to both rule sets — per the fleet rule that
# a check which cannot establish a fact must say so rather than fold it into the
# passing state.
_SHELL_AGNOSTIC_TOOLS = {"run_terminal_command", "run_terminal_cmd"}

# Grok's lower_snake event values → Claude Code's PascalCase names.
_GROK_EVENTS = {
    "session_start": "SessionStart",
    "user_prompt_submit": "UserPromptSubmit",
    "pre_tool_use": "PreToolUse",
    "post_tool_use": "PostToolUse",
    "permission_denied": "PermissionDenied",
    "notification": "Notification",
    "stop": "Stop",
    "subagent_start": "SubagentStart",
    "subagent_stop": "SubagentStop",
    "pre_compact": "PreCompact",
    "post_compact": "PostCompact",
    "session_end": "SessionEnd",
}

# Grok's internal tool ids → the Claude tool names every matcher and guard here
# is written against. Grok's own `matcher` aliasing goes the other way (it maps
# `Bash` → `run_terminal_command` so a Claude matcher fires); this completes the
# round trip so the hook *body* sees the name it expects.
#
# `search_replace` → `Write`, not `Edit`, and the choice is load-bearing. Grok
# collapses Claude's `Edit`/`Write`/`MultiEdit` into that one tool, so any single
# mapping loses a distinction some guard may rely on. Surveying the actual call
# sites decides it: four hooks accept the whole `{Edit, Write, MultiEdit}` family
# (`py_syntax_check`, `hub_bypass_warn`, `browser_stealth_lint`,
# `branch_before_edit_guard`), exactly one demands a specific member —
# `docs_dated_filename_guard`, which requires `Write` — and **none** requires
# `Edit`. So `Write` satisfies all five and `Edit` would silently disarm the
# dated-docs guard under Grok.
_GROK_TOOLS = {
    "run_terminal_command": "Bash",
    "run_terminal_cmd": "Bash",
    "read_file": "Read",
    "search_replace": "Write",
    "grep": "Grep",
    "list_dir": "Glob",
    "web_search": "WebSearch",
    "spawn_subagent": "Task",
}

# camelCase → snake_case for the envelope fields hooks here actually read.
# Anything else falls through the generic converter below, so a field xAI adds
# later still arrives under a predictable name instead of vanishing.
_GROK_KEYS = {
    "hookEventName": "hook_event_name",
    "sessionId": "session_id",
    "toolName": "tool_name",
    "toolInput": "tool_input",
    "toolResult": "tool_response",
    "toolUseId": "tool_use_id",
    "transcriptPath": "transcript_path",
    "workspaceRoot": "workspace_root",
    "permissionMode": "permission_mode",
    "stopHookActive": "stop_hook_active",
    "lastAssistantMessage": "last_assistant_message",
}

# Copilot CLI tool ids → Claude tool names (fleet-config#547). Unlike Codex
# and agy, Copilot's toolName truthfully names the executing shell (verified
# live on 1.0.77: toolName "powershell" ran PowerShell), so no shell-ambiguity
# marker and no platform override are needed downstream.
_COPILOT_TOOLS = {
    "powershell": "PowerShell",
    "bash": "Bash",
    "shell": "Bash",
    "sh": "Bash",
}

# Antigravity's `agy` CLI tool ids → Claude tool names (fleet-config#546).
# `run_command` is shell-agnostic like Grok's `run_terminal_command`; the live
# probe proved agy executes CommandLine under PowerShell on Windows, but that
# is the *hook body's* platform decision (see context_filter_hook), not a
# payload fact, so the map stays shell-neutral here.
_AGY_TOOLS = {
    "run_command": "Bash",
}

_CAMEL_BOUNDARY = re.compile(r"(?<!^)(?=[A-Z])")

# The harness whose payload this process is currently handling, set by
# `normalize_payload()`. A hook is a one-payload, one-shot process, so a module
# global is the whole lifetime — this exists so `block()` can speak the calling
# harness's refusal dialect without threading the payload through the ~40
# `_lib.block(...)` call sites across the hooks directory.
_ACTIVE_AGENT: Optional[str] = None

# The event this process is handling, set by `read_stdin_json()` once the
# payload is in Claude's shape. Same one-payload-one-process lifetime argument
# as `_ACTIVE_AGENT` above — it exists so `warn()` can pick the stdout dialect
# the event actually delivers to the model, without threading the payload
# through every `_lib.warn(...)` call site (fleet-config#681).
_ACTIVE_EVENT: Optional[str] = None


def _camel_to_snake(key: str) -> str:
    return _CAMEL_BOUNDARY.sub("_", key).lower()


def normalize_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Translate a foreign-harness hook payload into Claude Code's shape.

    Claude Code payloads (and Pi lifecycle ``{"event": ...}`` envelopes) are
    returned **unchanged** — same object, no copy. Foreign envelopes are
    translated below. Codex shares Claude's shape, so only an invoked
    ``.codex/hooks`` entry point establishes Codex provenance.

    The rewritten payload also carries an :data:`AGENT_HINT_KEY` entry naming
    the originating harness, so :mod:`session_state` can attribute the row to
    ``grok`` instead of silently defaulting to ``claude`` (the latent
    mis-attribution the compat shim would otherwise cause).
    """
    global _ACTIVE_AGENT
    _ACTIVE_AGENT = None

    if not isinstance(payload, dict):
        return payload

    # Only the explicit extension envelope establishes Pi provenance. Lifecycle
    # adapters retain their existing pass-through contract.
    if payload.get("fleet_harness") == "pi":
        _ACTIVE_AGENT = "pi"
        event = payload.get("type")
        raw_tool = payload.get("toolName")
        names = {"bash": "Bash", "powershell": "PowerShell", "edit": "Edit", "write": "Write"}
        if event not in {"tool_call", "tool_result"} or raw_tool not in names:
            raise ValueError("unsupported Pi policy event/tool")
        args = payload.get("input")
        if not isinstance(args, dict):
            raise ValueError("missing Pi tool input")
        field = "command" if raw_tool in {"bash", "powershell"} else "path"
        if not isinstance(args.get(field), str) or not args[field].strip():
            raise ValueError("missing Pi " + field)
        if "\0" in args[field]:
            raise ValueError("invalid Pi " + field)
        base = payload.get("cwd")
        if not isinstance(base, str) or not Path(base).is_absolute():
            raise ValueError("missing or relative Pi cwd")
        out = {
            "hook_event_name": "PreToolUse" if event == "tool_call" else "PostToolUse",
            "tool_name": names[raw_tool], "tool_input": dict(args),
            "cwd": payload.get("cwd"), "session_id": payload.get("session_id"),
            "tool_use_id": payload.get("toolCallId"), AGENT_HINT_KEY: "pi",
        }
        if field == "path":
            # Pi's own write/edit resolver supplies the target. Raw aliases are
            # not filesystem paths (e.g. @docs/... writes docs/..., #746).
            resolved = payload.get("fleet_resolved_path")
            if (not isinstance(resolved, str) or "\0" in resolved
                    or not Path(resolved).is_absolute()):
                raise ValueError("Pi resolved edit target unavailable")
            out["tool_input"]["file_path"] = resolved
        if event == "tool_result":
            # A missing flag is unknown, never a successful post-edit event.
            flag = payload.get("isError")
            out["_fleet_edit_outcome"] = "failed" if flag is True else "success" if flag is False else "unknown"
        return out

    # Antigravity's `agy` CLI (fleet-config#546): its PreToolUse payload is
    # `{"toolCall": {"name", "args": {"CommandLine", "Cwd", ...}},
    #   "conversationId", "stepIdx", ...}` — the `toolCall` envelope is the
    # reliable tell (Claude and Grok never send it; verified live against
    # agy 1.1.8). Translated here, once, same contract as the Grok branch.
    # Copilot CLI (fleet-config#547): camelCase envelope with NO event name —
    # `{"sessionId", "timestamp", "cwd", "toolName", "toolArgs": "<JSON string>"}`
    # (verified live on 1.0.77). The string-typed `toolArgs` beside `toolName`
    # is the tell: Claude sends tool_input as an object, Grok sends
    # hookEventName, agy sends toolCall. The full parsed args dict is kept in
    # tool_input because Copilot's modifiedArgs response replaces the WHOLE
    # args object — a hook that rewrites `command` must echo the other keys.
    if "toolArgs" in payload and isinstance(payload.get("toolArgs"), str) and "hookEventName" not in payload:
        _ACTIVE_AGENT = "copilot"
        try:
            parsed_args = json.loads(payload.get("toolArgs") or "{}")
        except (json.JSONDecodeError, TypeError):
            parsed_args = {}
        if not isinstance(parsed_args, dict):
            parsed_args = {}
        raw_tool = str(payload.get("toolName") or "").lower()
        out = {
            "hook_event_name": "PreToolUse",
            "session_id": payload.get("sessionId"),
            "cwd": payload.get("cwd") or "",
            "tool_name": _COPILOT_TOOLS.get(raw_tool, payload.get("toolName") or ""),
            "tool_input": parsed_args,
            AGENT_HINT_KEY: "copilot",
        }
        return out

    tool_call = payload.get("toolCall")
    if isinstance(tool_call, dict):
        _ACTIVE_AGENT = "antigravity"
        args = tool_call.get("args") if isinstance(tool_call.get("args"), dict) else {}
        raw_tool = str(tool_call.get("name") or "")
        out = {
            "hook_event_name": "PreToolUse",
            "session_id": payload.get("conversationId"),
            "transcript_path": payload.get("transcriptPath"),
            "tool_name": _AGY_TOOLS.get(raw_tool, raw_tool),
            "tool_input": {"command": args.get("CommandLine") or ""},
            "cwd": args.get("Cwd") or "",
            AGENT_HINT_KEY: "antigravity",
        }
        if raw_tool in _AGY_TOOLS:
            out[SHELL_AMBIGUOUS_KEY] = True
        return out

    if "hookEventName" not in payload:
        # Codex uses Claude-shaped input. The invoked entry point is provenance;
        # resolving junctions would erase .codex, and inherited launcher/env
        # hints can describe an ancestor rather than this hook's caller (#759).
        entry = Path(sys.argv[0]).absolute() if sys.argv else None
        if (entry is not None and entry.parent.name.lower() == "hooks"
                and entry.parent.parent.name.lower() == ".codex"
                and isinstance(payload.get("hook_event_name"), str)
                and payload["hook_event_name"]):
            _ACTIVE_AGENT = "codex"
            out = {**payload, AGENT_HINT_KEY: "codex"}
            # Bash is Codex's exec alias, not evidence of the executing shell.
            # Reuse the conservative contract of other shell-agnostic tools.
            if out.get("tool_name") in {"Bash", "PowerShell"}:
                out[SHELL_AMBIGUOUS_KEY] = True
            return out
        return payload

    _ACTIVE_AGENT = "grok"
    out: Dict[str, Any] = {}
    for key, value in payload.items():
        out[_GROK_KEYS.get(key) or _camel_to_snake(key)] = value

    event = str(out.get("hook_event_name") or "")
    # Grok fires a second, observe-only Stop at session end (`reason` is
    # "channel_closed"/"shutdown", not "end_turn") *after* SessionEnd has already
    # fired. Mapping that to Claude's `Stop` would resurrect the row SessionEnd
    # just deleted, stranding a dead session on the Board as `needs-you` until
    # the 24h prune. It has no Claude equivalent, so it maps to a name no hook
    # matches and stays inert. (grok docs, user-guide/10-hooks.md.)
    reason = out.get("reason")
    if event == "stop" and isinstance(reason, str) and reason and reason != "end_turn":
        out["hook_event_name"] = "StopAtSessionEnd"
    else:
        out["hook_event_name"] = _GROK_EVENTS.get(event, event)

    raw_tool = out.get("tool_name")
    if isinstance(raw_tool, str) and raw_tool:
        out["tool_name"] = _GROK_TOOLS.get(raw_tool, raw_tool)
        if raw_tool in _SHELL_AGNOSTIC_TOOLS:
            out[SHELL_AMBIGUOUS_KEY] = True

    out[AGENT_HINT_KEY] = "grok"
    return out


def payload_agent(payload: Dict[str, Any]) -> Optional[str]:
    """The harness that produced this payload, when normalization identified one."""
    hint = payload.get(AGENT_HINT_KEY)
    return hint if isinstance(hint, str) and hint else None


def shell_is_ambiguous(payload: Dict[str, Any]) -> bool:
    """True when the payload's shell tool could be running either PowerShell or
    bash, so a shell-discriminating rule must apply both sets rather than pick."""
    return payload.get(SHELL_AMBIGUOUS_KEY) is True


# --------------------------------------------------------------------------- I/O


def read_stdin_json() -> Dict[str, Any]:
    """Read the hook payload from stdin and return it as a dict.

    Returns an empty dict if stdin is empty or unparseable — that lets the
    hook short-circuit to "allow" rather than crash inside Claude's tool loop.

    A non-Claude harness's payload is translated into Claude's shape first (see
    :func:`normalize_payload`), so every hook downstream reads one vocabulary.
    """
    global _ACTIVE_EVENT, _ACTIVE_AGENT
    _ACTIVE_EVENT = None
    _ACTIVE_AGENT = None
    raw = sys.stdin.read()
    if not raw or not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    normalized = normalize_payload(data)
    # Read *after* normalization so a foreign harness's event name is already
    # Claude's. Never mutates the payload — Claude-shaped payloads must come
    # back as the same object (tests/test_payload_normalization.py asserts it).
    event = normalized.get("hook_event_name")
    _ACTIVE_EVENT = event if isinstance(event, str) and event else None
    return normalized


def block(reason: str) -> "NoReturn":
    """Refuse the tool call, in whatever dialect the calling harness understands.

    Claude Code blocks on **exit 2 with the reason on stderr**, and that stays
    the contract here. Grok nominally accepts exit 2 as well, but a live
    grok 0.2.114 session showed the code arriving at its runner as ``1`` — which
    Grok treats as a *hook failure*, and hook failures fail open. The guard
    printed its refusal and the dangerous command ran anyway: the worst possible
    shape, a block that reports success while protecting nothing
    (fleet-config#491).

    Grok's documented escape hatch is that for ``PreToolUse`` a ``deny``
    decision on **stdout is honored regardless of exit code**, so a Grok-sourced
    payload also gets the JSON decision. Claude Code never reaches that branch —
    it is gated on the agent :func:`normalize_payload` identified — so Claude's
    stdout stays clean and its behaviour is byte-for-byte unchanged. Codex
    PreToolUse uses its verified structured deny with exit 0 (fleet-config#759).
    """
    if _ACTIVE_AGENT == "pi":
        if _ACTIVE_EVENT == "PreToolUse":
            print(json.dumps({"fleet_policy": 1, "decision": "block", "message": reason}), flush=True)
            sys.exit(0)
        warn(reason)
    # Codex 0.153.3 exec: stderr + exit 2 reported a block but executed the
    # sentinel. Structured PreToolUse deny + exit 0 refused it and delivered the
    # reason. Other events have different contracts; never label those a deny.
    if _ACTIVE_AGENT == "codex" and _ACTIVE_EVENT == "PreToolUse":
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }}), flush=True)
        sys.exit(0)
    if _ACTIVE_AGENT == "codex" and _ACTIVE_EVENT == "PostToolUse":
        # Codex 0.153.3 drops stderr/exit-2 after apply_patch (#744). The edit
        # already ran: deliver compiler feedback through the existing context
        # channel, without pretending this can deny or undo the completed edit.
        warn(reason)
    if _ACTIVE_AGENT == "grok":
        print(json.dumps({"decision": "deny", "reason": reason}), flush=True)
    print(reason, file=sys.stderr, flush=True)
    sys.exit(2)


# Events whose exit-0 **plain-text** stdout Claude Code adds to the model's
# context. Every other event writes it to the debug log only — which is what
# made all seven `warn()` call sites invisible to the model they were written
# to advise (fleet-config#681). Per code.claude.com/docs/en/hooks: "For most
# events, stdout is written to the debug log but not shown in the transcript.
# The exceptions are UserPromptSubmit, UserPromptExpansion, and SessionStart."
# `SessionStart` is carved out below into `_ADDITIONAL_CONTEXT_EVENTS` instead
# (fleet-config#818) — Claude honors both channels for that event, and the
# wrapped form is the one `chief_handover_sessionstart` already ships in
# production, so routing through it here preserves that hook's verified
# behaviour instead of silently switching it to the other equally-documented
# channel.
_STDOUT_IS_CONTEXT_EVENTS = frozenset({"UserPromptExpansion"})

# Events carrying a `hookSpecificOutput.additionalContext` field — "text added
# to the conversation as context Claude sees" (same reference). `SessionStart`
# joined this set in fleet-config#818 so `chief_handover_sessionstart` could
# stop hand-rolling the identical envelope: without it, a non-Claude harness
# — e.g. Grok, which loads this repo's hooks by default — received a Claude
# JSON envelope it never parses, and the handover log silently never reached
# the model (the exact failure class fleet-config#491 centralised this dialect
# table to prevent). Routing through `warn()` gives every SessionStart nudge
# the same per-harness fallback every other event already gets: foreign
# harnesses fall through to the plain-stdout branch below instead.
_ADDITIONAL_CONTEXT_EVENTS = frozenset({
    "PostToolUse", "PostToolUseFailure", "UserPromptSubmit", "Stop", "SessionStart",
})


def warn(message: str) -> "NoReturn":
    """Exit 0 with a nudge the model actually sees; the action still runs.

    A guard that only *reports* is worse than no guard, and that is exactly
    what this was: bare stdout on exit 0 reaches the model on `SessionStart` /
    `UserPromptSubmit` / `UserPromptExpansion` and **nowhere else**, so the
    `PreToolUse` and `PostToolUse` nudges (`bash_cmdexe_syntax_guard`,
    `gh_body_file_guard`, `hub_bypass_warn`, `browser_stealth_lint`) went to
    the debug log and no further (fleet-config#681). The channel is per-event,
    so the dialect is too — same shape as :func:`block`:

      - ``PostToolUse`` / ``PostToolUseFailure`` / ``UserPromptSubmit`` /
        ``Stop`` / ``SessionStart`` → ``hookSpecificOutput.additionalContext``
        (``chief_handover_sessionstart`` is the ``SessionStart`` caller,
        fleet-config#818).
      - Claude ``PreToolUse`` (and any event without an
        ``additionalContext`` field) → the common ``systemMessage`` field.
      - Codex ``PreToolUse`` → ``hookSpecificOutput.additionalContext``; its
        0.153.3 client ignores the Claude ``systemMessage`` envelope.
        Neither form is a `permissionDecision`: a nudge must stay advisory,
        and both ``allow`` and ``ask`` would change whether the tool call runs.
      - ``UserPromptExpansion`` → plain text, already the model-visible
        channel there.

    A foreign harness keeps the bare-stdout form: Claude's JSON protocol is
    Claude's, and none of the shapes above are part of the Grok/Copilot/agy
    contract.
    """
    if _ACTIVE_AGENT == "pi":
        print(json.dumps({"fleet_policy": 1, "decision": "warn", "message": message}), flush=True)
        sys.exit(0)
    # Codex 0.153.3 ignores Claude's top-level `systemMessage` on PreToolUse:
    # the hook runs and the command proceeds, but a live one-call probe reports
    # "No hook messages were emitted". The same additionalContext envelope
    # already proven on Codex PostToolUse is model-visible on PreToolUse too.
    # Keep Claude on its documented systemMessage route below.
    if _ACTIVE_AGENT == "codex" and _ACTIVE_EVENT == "PreToolUse":
        logger.info("Codex PreToolUse advisory emitted through additionalContext")
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": message,
        }}), flush=True)
        sys.exit(0)
    if _ACTIVE_AGENT in (None, "codex") and _ACTIVE_EVENT not in _STDOUT_IS_CONTEXT_EVENTS:
        if _ACTIVE_EVENT in _ADDITIONAL_CONTEXT_EVENTS:
            payload: Dict[str, Any] = {"hookSpecificOutput": {
                "hookEventName": _ACTIVE_EVENT,
                "additionalContext": message,
            }}
        else:
            payload = {"systemMessage": message}
        print(json.dumps(payload), flush=True)
        sys.exit(0)
    print(message, flush=True)
    sys.exit(0)


def rewrite_command(payload: Dict[str, Any], new_command: str, *, reason: str = "") -> "NoReturn":
    """Allow the call with its command replaced, in the calling harness's
    ``PreToolUse`` rewrite dialect. Exit 0; the tool runs with ``new_command``.

    ``context_filter_hook`` is the one caller (fleet-config#818, extracted
    from it): before this existed, the hook carried three harnesses' outbound
    key names inline (``overwrite``/``CommandLine`` for Antigravity,
    ``modifiedArgs`` for Copilot, ``hookSpecificOutput.updatedInput`` for
    Claude) plus its own provenance sniff, so a fifth harness meant editing a
    hook body instead of extending the one translation point ``block()`` and
    ``warn()`` already own for the *refuse* and *nudge* outbound categories.
    This is that same table for the third category — *rewrite the command*:

      - Antigravity (``agy``): ``overwrite.CommandLine`` merges into the tool
        call's args before it runs — verified live, an overwritten
        ``CommandLine`` actually executed (fleet-config#546).
      - Copilot CLI: ``modifiedArgs`` is a JSON **string** replacing the whole
        tool-args object — verified live on 1.0.77 (fleet-config#547) — so
        ``payload``'s other ``tool_input`` keys (description, mode,
        initial_wait, ...) are echoed back with only ``command`` rewritten.
      - Claude Code (and any harness :func:`normalize_payload` doesn't name
        above — Codex and Grok are both fail-open before reaching a rewrite
        call site, per ``context_filter_hook``'s own early ``allow()``s) —
        ``hookSpecificOutput.updatedInput.command``, Claude's documented
        ``PreToolUse`` rewrite field.

    Keyed off the module-global :data:`_ACTIVE_AGENT` `normalize_payload()`
    set, same as `block()`/`warn()` — no payload/agent threading through call
    sites. ``reason`` rides Claude's ``permissionDecisionReason`` only; the
    other two dialects have no equivalent field.
    """
    if _ACTIVE_AGENT == "antigravity":
        output: Dict[str, Any] = {"decision": "allow", "overwrite": {"CommandLine": new_command}}
    elif _ACTIVE_AGENT == "copilot":
        raw_tool_input = payload.get("tool_input")
        args_out = dict(raw_tool_input) if isinstance(raw_tool_input, dict) else {}
        args_out["command"] = new_command
        output = {"permissionDecision": "allow", "modifiedArgs": json.dumps(args_out)}
    else:
        output = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
                "permissionDecisionReason": reason,
                "updatedInput": {"command": new_command},
            }
        }
    print(json.dumps(output, separators=(",", ":")), flush=True)
    sys.exit(0)


def allow() -> "NoReturn":
    """Exit 0 silently → action proceeds, Claude sees nothing."""
    if _ACTIVE_AGENT == "pi":
        print(json.dumps({"fleet_policy": 1, "decision": "allow", "message": ""}), flush=True)
    sys.exit(0)


# --------------------------------------------------------- Python resolution


def _is_windowsapps_alias(path: str) -> bool:
    return "\\windowsapps\\" in path.replace("/", "\\").lower()


def find_python_executable() -> Optional[str]:
    """Return a real Python executable, avoiding WindowsApps aliases.

    On this machine the WindowsApps ``py.exe`` / ``python.exe`` aliases can hang
    when spawned non-interactively from hooks. Hook code should use this helper
    instead of trusting PATH order.
    """
    local_appdata = os.environ.get("LOCALAPPDATA")
    candidates: list[str] = []
    if local_appdata:
        candidates.extend(
            [
                str(Path(local_appdata) / "Python" / "bin" / "python.exe"),
                str(Path(local_appdata) / "Programs" / "Python" / "Python314" / "python.exe"),
                str(Path(local_appdata) / "Programs" / "Python" / "Python313" / "python.exe"),
                str(Path(local_appdata) / "Programs" / "Python" / "Python312" / "python.exe"),
            ]
        )
    candidates.append(sys.executable)
    for name in ("py", "python"):
        resolved = shutil.which(name)
        if resolved:
            candidates.append(resolved)

    for candidate in candidates:
        if candidate and not _is_windowsapps_alias(candidate) and Path(candidate).exists():
            return candidate
    return None


# ----------------------------------------------------- PowerShell resolution

# The absolute Windows PowerShell 5.1 path every hook must spell out, because
# the `pwsh` on PATH here is a 0-byte WindowsApps reparse stub that fails
# non-interactively (global CLAUDE.md, "Windows PowerShell in spawned commands").
WINDOWS_POWERSHELL = "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"


def powershell_exe() -> str:
    """Resolve a usable PowerShell executable, preferring Windows PowerShell 5.1.

    Probes for the absolute path first and degrades through ``shutil.which`` so a
    machine without it (a POSIX box, a trimmed Windows image) gets a best-effort
    fallback instead of a hard `FileNotFoundError`. Hoisted here from
    `context_filter_cli` (fleet-config#561): `restart_and_verify_webapp` had
    hardcoded the literal with no probe, so `/restart-webapp` hard-failed where
    the wrapper degraded — two resolutions of one fact that had drifted in
    safety.
    """
    if Path(WINDOWS_POWERSHELL).exists():
        return WINDOWS_POWERSHELL
    return shutil.which("powershell") or "powershell"


# --------------------------------------------------------------------- git


def git_env(base: Optional[dict] = None) -> dict:
    """``base`` (default ``os.environ``) plus ``GIT_OPTIONAL_LOCKS=0``.

    Hooks-tier copy of ``skills/_lib/git_run.git_env`` — see that function for
    the full reasoning (fleet-config#667). Short version: ``git status`` takes
    ``.git/index.lock`` only to persist a refreshed stat cache, so killing one
    mid-refresh strands a 0-byte lock that then blocks every write in that repo
    while every *read* keeps exiting 0. This tier matters most, not least:
    ``branch_before_edit_guard`` shells out to ``git`` on every Edit/Write in
    every live session fleet-wide, which is by far the highest-frequency git
    spawn this repo owns.
    """
    env = dict(os.environ if base is None else base)
    env["GIT_OPTIONAL_LOCKS"] = "0"
    return env


def run_git(
    args: Sequence[str], *, check: bool = False, timeout: Optional[float] = None
) -> subprocess.CompletedProcess:
    """Run ``git <args>``, UTF-8 decoded with undecodable bytes replaced.

    Pass ``-C <repo>`` inside ``args`` to target a working tree — the
    convention every call site in this repo uses. ``check=False`` (the default)
    leaves the caller to inspect ``.returncode``/``.stdout``/``.stderr``.

    Deliberately a **hooks-tier copy** of ``skills/_lib/git_run.run_git``, the
    same way :data:`NO_WINDOW` duplicates ``skills/_lib/no_window`` — see that
    constant's note. ``branch_before_edit_guard`` used to reach the skills-tier
    module by inserting ``../skills/_lib`` onto ``sys.path`` at import time
    (fleet-config#564), which breaks the rule the rest of the directory states
    outright (``notify_on_idle``: the two trees are independent, so a sibling is
    reached by subprocess, never a Python import). The practical cost of the
    violation was unbounded: the import sat at module top with no guard, so a
    renamed or absent skills tree would kill a ``PreToolUse`` hook *at import
    time* — before any of its fail-open logic could run — on every Edit/Write in
    every session, fleet-wide. ``tests/acceptance/tree_boundary.py`` now fails
    on any hook that reaches across, and asserts this copy agrees behaviourally
    with the skills-tier original — including ``env=git_env()``.
    """
    return subprocess.run(
        ["git", *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=check, timeout=timeout,
        creationflags=NO_WINDOW, env=git_env(),
    )


def run_gh(
    args: Sequence[str], *, check: bool = False, timeout: Optional[float] = None,
    stdin: Optional[int] = None,
) -> subprocess.CompletedProcess:
    """Run ``gh <args>``, UTF-8 decoded with undecodable bytes replaced.

    The ``gh``-CLI sibling of :func:`run_git` (fleet-config#728): before this
    existed, every ``gh`` call site hand-rolled the identical
    ``capture_output=True, text=True, encoding="utf-8", errors="replace",
    creationflags=NO_WINDOW`` combination — ``text=True`` alone falls back to
    cp1252 on Windows, which raises ``UnicodeDecodeError`` on a non-ASCII issue
    title or body (fleet-config#679) — each restating the same two gotchas in
    its own comment. They had already drifted on ``timeout`` (some passed none,
    some 30s/60s/120s) before this wrapper existed to catch it; that spread is
    real call-site policy, so ``timeout`` stays a parameter here rather than a
    forced constant — only the plumbing is shared. ``stdin`` likewise stays
    optional (``None`` inherits, matching every site except
    ``chief_ops.fetch_issue_state``, which passes ``subprocess.DEVNULL``).

    Deliberately a **hooks-tier copy** of ``skills/_lib/git_run.run_gh`` — the
    same duplication `run_git` already carries between the two trees (see that
    function's docstring for why: `hooks/` must stay importable with nothing
    but its own directory on ``sys.path``).
    """
    return subprocess.run(
        ["gh", *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=check, timeout=timeout,
        creationflags=NO_WINDOW, stdin=stdin,
    )


def resolve_default_branch_ref(
    repo_path: Path,
    candidates: Sequence[str] = ("origin/main", "main", "master"),
    final_fallback: str = "main",
) -> str:
    """The repo's default branch, preferring the remote's own ``origin/HEAD``.

    On ``symbolic-ref refs/remotes/origin/HEAD`` success, returns the ref with
    the ``refs/remotes/`` prefix stripped (e.g. ``origin/main``). On failure,
    probes ``candidates`` in order via ``rev-parse --verify --quiet`` and
    returns the first that resolves; if none do (or ``candidates`` is empty),
    returns ``final_fallback``.

    Hooks-tier copy of ``skills/_lib/git_run.resolve_default_branch_ref`` — see
    :func:`run_git` for why the two trees each carry their own.
    """
    res = run_git(["-C", str(repo_path), "symbolic-ref", "refs/remotes/origin/HEAD"])
    ref = res.stdout.strip()
    if res.returncode == 0 and ref:
        return ref.replace("refs/remotes/", "", 1)
    for cand in candidates:
        if run_git(["-C", str(repo_path), "rev-parse", "--verify", "--quiet", cand]).returncode == 0:
            return cand
    return final_fallback


# ------------------------------------------------------------------ gh CLI


def gh_json(args: Sequence[str], *, timeout: int = 20) -> Dict[str, Any]:
    """Run ``gh <args>`` and parse its JSON stdout. Returns ``{}`` on any error.

    Never raises: a missing gh, a non-zero exit, or unparseable output all yield
    an empty dict so the caller degrades to a link-less message instead of
    crashing a skill mid-run.

    Decodes gh's stdout as UTF-8 explicitly — on Windows ``text=True`` falls back
    to cp1252, which mis-decodes a UTF-8 title (em-dash — -> â€", emoji -> ðŸ§)
    before it ever reaches the chat. Mirrors ``notify_send._read_text``.

    Lives here rather than in `notify_complete` (fleet-config#561) because
    `work_summary` needed the identical helper and could not import it —
    `notify_complete` imports `work_summary`, so the obvious direction was an
    import cycle and the cycle was "resolved" by copying the body. `_lib` is
    imported by both and imports neither, so the cycle dissolves.
    """
    try:
        proc = run_gh(args, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.error("gh call failed: %s", exc)
        return {}
    if proc.returncode != 0:
        logger.error("gh exited %s: %s", proc.returncode, (proc.stderr or "").strip()[:200])
        return {}
    try:
        data = json.loads(proc.stdout)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


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


# --------------------------------------------------------- Shared edit events
#
# EditTarget/EditEvent/edit_event() moved to edit_events.py (fleet-config#819)
# — re-exported here so `_lib.EditEvent`, `_lib.edit_event`, etc. keep working
# unchanged for every existing hook import. Placed after tool_name/tool_input/
# payload_agent above: edit_events.py reaches those back via `import _lib`
# inside its own function bodies (never at its module scope), which is what
# makes this reverse import safe despite `_lib` not having finished loading
# yet at the point it reaches this line.

from edit_events import EditEvent, EditTarget, edit_event  # noqa: E402


# ----------------------------------------------------------- projects.toml
#
# ProjectConfig/GlobalConfig/Registry/load_registry/detect_project/
# resolve_notify_target/resolve_board_url moved to registry.py
# (fleet-config#819) — re-exported here so `_lib.load_registry`,
# `_lib.detect_project`, etc. keep working unchanged for every existing hook
# import. registry.py has no dependency on `_lib`, so this import carries no
# ordering hazard.

from registry import (  # noqa: E402
    BOARD_URL_ENV_VAR,
    DOTENV_PATH_ENV_VAR,
    HOOKS_DIR,
    NOTIFY_CATEGORY_KEYS,
    PROJECTS_TOML,
    PROJECTS_TOML_ENV_VAR,
    GlobalConfig,
    ProjectConfig,
    Registry,
    detect_project,
    load_registry,
    resolve_board_url,
    resolve_notify_target,
)


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

