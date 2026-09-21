"""The hooks' multi-harness wire protocol (split from `_lib.py`, fleet-config#931).

Two halves of one subsystem, both keyed off which harness sent the payload:

* **Inbound** — `read_stdin_json()` reads the hook payload and
  `normalize_payload()` translates a foreign harness's envelope (Grok, Copilot,
  Antigravity, Pi, Codex) into Claude Code's shape, recording the harness and
  event in `_ACTIVE_AGENT` / `_ACTIVE_EVENT`.
* **Outbound** — `block()`, `warn()`, `rewrite_command()` and `allow()` answer
  in that harness's dialect.

`_lib.py` re-exports every public name here, so hooks keep calling
`_lib.block(...)` etc. unchanged. The two globals live **here**: anything that
sets them by hand (the live Codex probes under `tests/`) must set
`harness_wire._ACTIVE_EVENT`, not `_lib._ACTIVE_EVENT`, or the dialect
emitters never see it. stdlib only, no dependency on `_lib`.
"""
from __future__ import annotations

import json
import logging
import re
import sys
from pathlib import Path
from typing import Any, Dict, Optional, TextIO

logger = logging.getLogger("fleet_hooks")


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
    # `{"sessionId", "timestamp", "cwd", "toolName", "toolArgs": {...}}`.
    # `toolArgs` was a JSON-encoded string through 1.0.77 and is an object
    # from 1.0.83 (both verified live, fleet-config#918), so both shapes are
    # accepted. `toolArgs` with no hookEventName and no toolCall is the tell:
    # Claude sends tool_input, Grok sends hookEventName, agy sends toolCall.
    # The full args dict is kept in tool_input because Copilot's modifiedArgs
    # response replaces the WHOLE args object — a hook that rewrites `command`
    # must echo the other keys.
    raw_args = payload.get("toolArgs")
    if isinstance(raw_args, (str, dict)) and "hookEventName" not in payload and "toolCall" not in payload:
        _ACTIVE_AGENT = "copilot"
        if isinstance(raw_args, dict):
            parsed_args = dict(raw_args)
        else:
            try:
                parsed_args = json.loads(raw_args or "{}")
            except json.JSONDecodeError:
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
    # Decode the raw bytes as UTF-8: a piped stdin's text layer defaults to
    # cp1252 on Windows and turns an em dash into mojibake (fleet-config#912).
    # `utf-8-sig` drops a leading BOM, which `json.loads` rejects outright — a
    # shim writing through a UTF-8 console's encoding emits one (fleet-config#920).
    buffer = getattr(sys.stdin, "buffer", None)
    raw = buffer.read().decode("utf-8-sig", errors="replace") if buffer is not None else sys.stdin.read()
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


def _print_text(text: str, stream: TextIO) -> None:
    """Print model-facing plain text, as UTF-8 when Claude Code is the reader.

    Behind a pipe, Python encodes stdio with the ANSI code page (cp1252) unless
    `PYTHONUTF8` / `PYTHONIOENCODING` is set, so an em dash left as byte 0x97
    and Claude Code, which decodes UTF-8, showed U+FFFD (fleet-config#924).
    Re-encoding here, right before the process exits, leaves every other write
    and every hook's own streams alone. Newline translation and the stream's
    error handler are kept, so only the encoding of non-ASCII changes.

    Foreign harnesses keep the locale encoding: no Codex, Grok, Copilot or agy
    decode of these bytes has been verified. The JSON dialects never reach
    here with non-ASCII anyway, since `json.dumps` escapes it.
    """
    reconfigure = getattr(stream, "reconfigure", None)
    if _ACTIVE_AGENT is None and reconfigure is not None:
        reconfigure(encoding="utf-8", errors=stream.errors)
    print(text, file=stream, flush=True)


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
    _print_text(reason, sys.stderr)
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
    _print_text(message, sys.stdout)
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
        tool-args object — verified live on 1.0.77 (fleet-config#547) and
        still honored on 1.0.83, where ``toolArgs`` itself arrives as an
        object (fleet-config#918) — so
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
