"""Shared normalized edit view + ``apply_patch`` envelope parsing (fleet-config#819).

Split out of ``_lib.py`` so the edit-event concern — turning a native
Edit/Write/MultiEdit or a Codex ``apply_patch`` ``PreToolUse``/``PostToolUse``
payload into one ``EditEvent`` shape every consumer reads — lives in its own
module instead of being one more section in the file every hook imports. No
hook's import line changes: ``_lib.py`` re-exports every name below, so
``_lib.EditEvent``, ``_lib.EditTarget``, ``_lib.edit_event`` keep working
unchanged (see the re-export block near the end of ``_lib.py``).

``edit_event()`` needs three payload-extraction primitives (``tool_name``,
``tool_input``, ``payload_agent``) that stay in ``_lib`` per the "wire
protocol + payload extraction" split, so this module does ``import _lib`` and
reaches them as ``_lib.<name>`` **inside function bodies only** — never at
module scope. That is what makes the reverse import safe: ``_lib.py`` imports
this module only after those three functions are already defined above it, so
by the time anything actually calls into ``edit_events``, ``_lib`` need not
even have finished executing yet — the attribute lookup happens later, at
call time, once the hook process is fully wired up.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import _lib


@dataclass(frozen=True)
class EditTarget:
    """One intended filesystem operation; a rename keeps both endpoints."""

    path: Path
    operation: str  # add | update | write | delete | rename
    source_path: Optional[Path] = None


@dataclass(frozen=True)
class EditEvent:
    """Lazy normalized edit view, preserving Claude payload object identity.

    status: known | unverified | not_edit. outcome: pending | success | failed
    | unknown. Targets describe intent, never proof a failed patch was atomic.
    Consumers of post events must require success before checking final files.
    """

    status: str
    targets: tuple[EditTarget, ...] = ()
    outcome: str = "unknown"
    reason: str = ""


def _edit_path(raw: Any, payload: Dict[str, Any]) -> Path:
    if not isinstance(raw, str) or not raw.strip() or "\0" in raw:
        raise ValueError("missing or invalid edit path")
    path = Path(raw)
    if not path.is_absolute():
        base = payload.get("cwd")
        if not isinstance(base, str) or not base or not Path(base).is_absolute():
            raise ValueError("relative edit path without an absolute cwd")
        path = Path(base) / path
    return Path(os.path.abspath(path))


def _patch_targets(command: Any, payload: Dict[str, Any]) -> tuple[EditTarget, ...]:
    """Parse the supported apply_patch envelope as data; never execute it.

    Reject the whole target set on unsupported/malformed syntax, rather than
    silently return whichever headers happened to look familiar.
    """
    if not isinstance(command, str):
        raise ValueError("patch command is missing or not text")
    lines = command.strip().splitlines()
    if len(lines) < 3 or lines[0] != "*** Begin Patch" or lines[-1] != "*** End Patch":
        raise ValueError("unsupported patch envelope")
    targets: list[EditTarget] = []
    index = 1
    while index < len(lines) - 1:
        header = re.fullmatch(r"\*\*\* (Add|Update|Delete) File: (.+)", lines[index])
        if not header:
            raise ValueError("unsupported patch operation")
        operation = {"Add": "add", "Update": "update", "Delete": "delete"}[header[1]]
        path = _edit_path(header[2].strip(), payload)
        source = None
        index += 1
        if operation == "update" and lines[index].startswith("*** Move to: "):
            source, path = path, _edit_path(lines[index][13:].strip(), payload)
            operation = "rename"
            index += 1
        body_start = index
        hunk = False
        body_lines = 0
        end_of_file = False
        while index < len(lines) - 1 and not re.match(r"\*\*\* (Add|Update|Delete) File: ", lines[index]):
            line = lines[index]
            if operation == "delete" or end_of_file:
                raise ValueError("unexpected patch body")
            if operation == "add":
                valid = line.startswith("+")
            elif line == "@@" or line.startswith("@@ "):
                hunk, valid = True, True
            elif line == "*** End of File":
                end_of_file, valid = True, hunk
            else:
                # The runtime permits the first update hunk without @@.
                valid = not line or line[0] in " +-"
                hunk = hunk or valid
                body_lines += bool(valid)
            if not valid:
                raise ValueError("unsupported patch hunk")
            index += 1
        if operation in {"update", "rename"} and (index == body_start or not hunk or not body_lines):
            raise ValueError("update patch has no hunk")
        targets.append(EditTarget(path, operation, source))
    if not targets:
        raise ValueError("patch has no targets")
    return tuple(targets)


def edit_event(payload: Dict[str, Any]) -> EditEvent:
    """Shared native/foreign edit boundary; consumers never parse patch text.

    Call after normalize_payload (normally read_stdin_json). Grok's translated
    Write and Claude's Edit/Write/MultiEdit retain their native file_path form.
    apply_patch uses the documented tool_input.command and observed textual
    tool_response; unfamiliar output is unknown, never inferred from existence.
    """
    name = _lib.tool_name(payload)
    if name not in {"Edit", "Write", "MultiEdit", "apply_patch"}:
        return EditEvent("not_edit")
    event = payload.get("hook_event_name")
    outcome = "pending" if event == "PreToolUse" else "unknown"
    if not event and name != "apply_patch":
        outcome = "success"  # Preserve legacy native edit invocations without an event.
    if event == "PostToolUseFailure":
        outcome = "failed"
    elif event == "PostToolUse":
        if name == "apply_patch":
            response = payload.get("tool_response")
            if isinstance(response, str):
                exit_match = re.match(r"Exit code: (-?\d+)\n", response)
                if exit_match and int(exit_match[1]) != 0:
                    outcome = "failed"
                elif (response.startswith("Success. Updated the following files:\n")
                      or (exit_match and int(exit_match[1]) == 0
                          and "\nOutput:\nSuccess. Updated the following files:\n" in response)):
                    outcome = "success"
                elif response.startswith(("apply_patch verification failed:", "Failed to ")):
                    outcome = "failed"
        else:
            outcome = "success"  # Native PostToolUse is the successful edit event.
    if _lib.payload_agent(payload) == "pi" and event == "PostToolUse":
        outcome = payload.get("_fleet_edit_outcome", "unknown")
    try:
        if name == "apply_patch":
            targets = _patch_targets(_lib.tool_input(payload).get("command"), payload)
        else:
            targets = (EditTarget(_edit_path(_lib.tool_input(payload).get("file_path"), payload),
                                  "write" if name == "Write" else "update"),)
    except (ValueError, OSError) as exc:
        return EditEvent("unverified", outcome=outcome, reason=str(exc))
    return EditEvent("known", targets, outcome)
