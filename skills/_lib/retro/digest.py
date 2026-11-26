"""Reduce one session log to the facts a retro needs: counts, tool names and paths.

`/retro` reads this digest instead of quoting the transcript, so nothing a person
typed and nothing a tool printed can reach a filed issue. Every value is a count, a
tool name, a skill name, an error class from a fixed list, an error signature built
only from harness, hook and ratchet text and a fixed list of program names
(`signature.py`), or a path made relative
to the session's repo (or its sibling repos). A path in a private repo is replaced
by `<private>`. Claude Code, Codex and Pi logs are read through `adapters.py` into
the same shape, with `agent` naming which.

  digest.py --current                 the running Claude Code session (`CLAUDE_CODE_SESSION_ID`)
  digest.py <session.jsonl>           a named Claude Code, Codex or Pi session log
  digest.py ... --private <repo>      also redact paths in <repo> (repeatable)

Prints the digest as one JSON object, exit 0. A log that cannot be found or read,
or whose shape no adapter recognises, prints `RETRO_DIGEST=NOT_CONFIRMED
reason=<text>` (`unmeasured: ...` for an unrecognised shape), exit 2, and never an
empty digest: an empty one would read as a clean session. Lines that are not JSON
objects are counted in `unparsed_lines`, never dropped unseen. stdlib only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path, PurePath
from typing import Any, Dict, Iterable, List, Optional, Tuple

if __package__:
    from . import adapters, command_head
    from . import signature as sig
else:  # run as a script: its own folder is on sys.path
    import adapters
    import command_head
    import signature as sig

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from worktree_residue import WT_SEP  # noqa: E402

#: Repos whose file paths never leave the machine, even as paths.
DEFAULT_PRIVATE = ("life-os",)
#: A file read this often in one session is navigation friction worth a look.
REPEAT_READ_MIN = 3
#: A tool result this large is an expensive call: it costs context for every later turn.
LARGE_RESULT_CHARS = 20000
TOP_N = 5

#: First match wins, so the specific classes come before the generic exit code.
ERROR_CLASSES: Tuple[Tuple[str, re.Pattern[str]], ...] = (
    ("user_rejected", re.compile(r"doesn't want to proceed|tool use was rejected", re.I)),
    ("hook_refusal", re.compile(r"hook error|blocked by .*hook|hook .*blocked", re.I)),
    ("edit_mismatch", re.compile(
        r"String to replace not found|Found \d+ matches of the string|Could not find (?:the exact text|edits\[)"
        r"|Found \d+ occurrences of|Failed to find expected lines", re.I)),
    ("file_state", re.compile(r"has not been read yet|modified since (?:it was )?read", re.I)),
    ("timeout", re.compile(r"timed out|Exit code:? 143\b", re.I)),
    ("missing_path", re.compile(
        r"No such file or directory|does not exist|cannot find the path|Path not found", re.I)),
    ("permission", re.compile(r"Permission denied|Access is denied", re.I)),
    # Claude Code opens a failed command with its exit line; Codex may, Pi closes with it.
    ("exit_nonzero", re.compile(
        r"^\s*(?:Exit code:? \d+|(?:Process|Command) exited with code \d+)", re.I | re.M)),
)
_COMMAND = re.compile(r"<command-name>/?([\w:.-]+)</command-name>")
_INTERRUPT = "[Request interrupted by user"
_HOOK_EVENT = re.compile(r"\b(PreToolUse|PostToolUse|UserPromptSubmit|Stop|SessionStart):(\w+) hook")
#: The refusing hook module, in either wiring; a module name is safe to show.
#: The shim names it in its command line, `hook_dispatch.py` opens each refusal with it.
_SHIM_HOOK = re.compile(r"-Hook\s+([\w.-]+)")
_DISPATCH_HOOK = re.compile(r"(?:^|\]: )\[hook:([a-z0-9_]+)\] ", re.M)
#: Tool names across harnesses: Claude Code capitalises, Pi does not, Codex patches.
_EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit", "edit", "write", "apply_patch"}
_READ_TOOLS = {"Read", "read"}
_SEARCH_TOOLS = {"Grep", "Glob", "Read", "grep", "find", "ls", "read"}
#: Pi runs a skill as a `/skill:<name>` prompt.
_PI_SKILL = re.compile(r"^/skill:([\w:.-]+)", re.M)
_WORKTREE_SUFFIX = re.compile(rf"{re.escape(WT_SEP)}[\w.-]+$", re.I)


class NotConfirmed(Exception):
    """The transcript could not be found, read or recognised."""


def classify(text: str) -> Tuple[str, str]:
    """The fixed error class of one failed tool result's text, and the span that matched."""
    for name, pattern in ERROR_CLASSES:
        found = pattern.search(text)
        if found:
            return name, found.group(0)
    return "other", ""


def classify_error(text: str) -> str:
    """The fixed error class of one failed tool result's text."""
    return classify(text)[0]


def refusing_hooks(text: str) -> List[str]:
    """The hook modules one refusal text names, or `["unknown"]`.

    The shim name is read from the command line only, so a name the reason
    merely quotes is never taken for the refusing hook.
    """
    shim = _SHIM_HOOK.search(text.split("]: ", 1)[0])
    if shim:
        return [shim.group(1)]
    return list(dict.fromkeys(_DISPATCH_HOOK.findall(text))) or ["unknown"]


class PathMask:
    """Turns an absolute path into a public-safe one, or `<private>`."""

    def __init__(self, cwd: str, private: Iterable[str]) -> None:
        self.cwd = PurePath(cwd) if cwd else None
        self.root = self.cwd.parent if self.cwd else None
        self.private = {p.lower() for p in private}

    def __call__(self, raw: str) -> str:
        path = PurePath(raw.replace("\\", "/"))
        rel = self._relative(path)
        if rel is None:
            return f"<outside>/{path.name}"
        if rel.parts and rel.parts[0].lower() in self.private:
            return "<private>"
        return rel.as_posix()

    def private_session(self) -> bool:
        return bool(self.cwd) and self.cwd.name.lower() in self.private

    def _relative(self, path: PurePath) -> Optional[PurePath]:
        if self.cwd is None or self.root is None:
            return None
        if not path.is_absolute():
            return PurePath(self.cwd.name, path)
        try:
            return path.relative_to(self.root)
        except ValueError:
            return None


def read_records(path: Path) -> Tuple[List[Dict[str, Any]], int]:
    """Every JSON object line of a log, and the count of non-blank lines that were not one."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise NotConfirmed(f"cannot read {path.name}: {exc.strerror or exc}") from exc
    records: List[Dict[str, Any]] = []
    unparsed = 0
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            row = None
        if isinstance(row, dict):
            records.append(row)
        else:
            unparsed += 1
    return records, unparsed


def repo_of(cwd: str, mask: PathMask) -> str:
    """The fleet repo a session ran in: its working folder, a `-wt-<N>` worktree folded in."""
    if not cwd:
        return "unknown"
    if mask.private_session():
        return "<private>"
    return _WORKTREE_SUFFIX.sub("", PurePath(cwd.replace("\\", "/")).name) or "unknown"


class Digest:
    """Accumulates the counts over a session's events, in order."""

    def __init__(self, mask: PathMask) -> None:
        self.mask = mask
        self.tool_calls: Counter[str] = Counter()
        self.errors: Dict[str, Counter[str]] = {}
        self.signatures: Counter[str] = Counter()
        self.ratchet_rules: Counter[str] = Counter()
        self.hook_refusals: Counter[str] = Counter()
        self.skills: Counter[str] = Counter()
        self.reads: Counter[str] = Counter()
        self.interrupts_after: Counter[str] = Counter()
        self.large: List[Tuple[int, str, str]] = []
        self.pending: Dict[str, Tuple[str, str, Any]] = {}
        self.seen_calls: set = set()
        self.repeated_calls = 0
        self.owner_prompts = 0
        self.interrupts = 0
        self.lookups_before_edit = 0
        self.edited = False
        self.last_tool = "none"
        self.turn_ms: Optional[int] = None

    def feed(self, event: Tuple[Any, ...]) -> None:
        """Count one adapter event (see `adapters`)."""
        kind = event[0]
        if kind == "call":
            self._tool_use(*event[1:])
        elif kind == "result":
            self._tool_result(*event[1:])
        elif kind == "prompt":
            self._prompt_text(*event[1:])
        elif kind == "interrupt":
            self._interrupt()
        elif kind == "turn_ms":
            self.turn_ms = (self.turn_ms or 0) + int(event[1])

    def _tool_use(self, call_id: str, name: str, args: Dict[str, Any]) -> None:
        self.tool_calls[name] += 1
        self.last_tool = name
        key = hashlib.sha256(json.dumps([name, args], sort_keys=True, default=str).encode()).hexdigest()
        self.repeated_calls += key in self.seen_calls
        self.seen_calls.add(key)
        target = str(args.get("file_path") or args.get("notebook_path") or args.get("path") or "")
        shown = self.mask(target) if target else ""
        if name in _READ_TOOLS and target:
            self.reads[shown] += 1
        if name in _EDIT_TOOLS:
            self.edited = True
        elif name in _SEARCH_TOOLS and not self.edited:
            self.lookups_before_edit += 1
        if name == "Skill" and args.get("skill"):
            self.skills[str(args["skill"])] += 1
        # Claude Code and Pi name a shell call's line `command`, Codex also `cmd`.
        self.pending[call_id] = (name, shown, args.get("command") or args.get("cmd"))

    def _interrupt(self) -> None:
        self.interrupts += 1
        self.interrupts_after[self.last_tool] += 1

    def _prompt_text(self, text: str, meta: bool) -> None:
        if text.startswith(_INTERRUPT):
            self._interrupt()
            return
        for name in _COMMAND.findall(text) + _PI_SKILL.findall(text):
            self.skills[name] += 1
        if not meta and not text.lstrip().startswith("<"):
            self.owner_prompts += 1

    def _tool_result(self, call_id: str, text: str, is_error: bool) -> None:
        name, shown, command = self.pending.get(call_id, ("unknown", "", None))
        if len(text) >= LARGE_RESULT_CHARS:
            self.large.append((len(text), name, shown))
        if not is_error:
            return
        cls, span = classify(text)
        self.errors.setdefault(name, Counter())[cls] += 1
        self.ratchet_rules.update(sig.ratchet_rules(text))
        hooks: List[str] = []
        if cls == "hook_refusal":
            event = _HOOK_EVENT.search(text)
            key = f"{event.group(1)}:{event.group(2)}" if event else f"unknown:{name}"
            hooks = refusing_hooks(text)
            for hook in hooks:
                self.hook_refusals[f"{key}:{hook}"] += 1
        program = command_head.program(command) if cls == "exit_nonzero" else ""
        self.signatures[sig.signature(name, cls, text, span, hooks, program)] += 1

    def result(self, source: str, agent: str, repo: str, unparsed: int) -> Dict[str, Any]:
        private = self.mask.private_session()
        large = sorted(self.large, reverse=True)[:TOP_N]
        return {
            "source": source,
            "agent": agent,
            "repo": repo,
            "private_session": private,
            "unparsed_lines": unparsed,
            "owner_prompts": self.owner_prompts,
            "turn_seconds": None if self.turn_ms is None else round(self.turn_ms / 1000),
            "skills": dict(self.skills.most_common()),
            "tool_calls": dict(self.tool_calls.most_common()),
            "tool_errors": {t: dict(c.most_common()) for t, c in sorted(self.errors.items())},
            "error_signatures": dict(self.signatures.most_common()),
            "ratchet_rules": dict(self.ratchet_rules.most_common()),
            "hook_refusals": dict(self.hook_refusals.most_common()),
            "interrupts": {"count": self.interrupts, "after_tool": dict(self.interrupts_after.most_common())},
            "repeated_identical_calls": self.repeated_calls,
            "lookups_before_first_edit": self.lookups_before_edit if self.edited else None,
            "repeat_reads": [] if private else [
                {"path": p, "reads": n} for p, n in self.reads.most_common() if n >= REPEAT_READ_MIN][:TOP_N],
            "large_results": [{"tool": t, "chars": n, "path": "<private>" if private and p else p}
                              for n, t, p in large],
        }


def build(path: Path, private: Iterable[str]) -> Dict[str, Any]:
    """The digest of one Claude Code, Codex or Pi session log; an unrecognised one raises NotConfirmed."""
    records, unparsed = read_records(path)
    try:
        agent = adapters.detect(records)
    except adapters.Unmeasured as exc:
        raise NotConfirmed(f"unmeasured: {path.name}: {exc}") from exc
    cwd = adapters.session_cwd(agent, records)
    mask = PathMask(cwd, private)
    digest = Digest(mask)
    for event in adapters.events(agent, records):
        digest.feed(event)
    return digest.result(path.name, agent, repo_of(cwd, mask), unparsed)


def current_transcript(home: Path, session_id: Optional[str]) -> Path:
    """The running session's transcript, found by its session id under the projects dir."""
    if not session_id:
        raise NotConfirmed("no CLAUDE_CODE_SESSION_ID in the environment; pass a transcript path")
    hits = sorted((home / ".claude" / "projects").glob(f"*/{session_id}.jsonl"))
    if not hits:
        raise NotConfirmed("no transcript file for the running session id")
    return hits[0]


def main(argv: Optional[List[str]] = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("transcript", nargs="?", help="a Claude Code, Codex or Pi session .jsonl path")
    parser.add_argument("--current", action="store_true", help="the running session")
    parser.add_argument("--private", action="append", default=[], help="another repo to redact")
    args = parser.parse_args(argv)
    try:
        if args.current == bool(args.transcript):
            raise NotConfirmed("pass exactly one of --current or a transcript path")
        path = (current_transcript(Path.home(), os.environ.get("CLAUDE_CODE_SESSION_ID"))
                if args.current else Path(args.transcript))
        out = build(path, [*DEFAULT_PRIVATE, *args.private])
    except NotConfirmed as exc:
        print(f"RETRO_DIGEST=NOT_CONFIRMED reason={exc}")
        return 2
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
