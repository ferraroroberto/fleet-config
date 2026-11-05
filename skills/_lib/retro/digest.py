"""Reduce one session transcript to the facts a retro needs: counts, tool names and paths.

`/retro` reads this digest instead of quoting the transcript, so nothing a person
typed and nothing a tool printed can reach a filed issue. Every value is a count, a
tool name, a skill name, an error class from a fixed list, or a path made relative
to the session's repo (or its sibling repos). A path in a private repo is replaced
by `<private>`.

  digest.py --current                 the running session (`CLAUDE_CODE_SESSION_ID`)
  digest.py <transcript.jsonl>        a named transcript
  digest.py ... --private <repo>      also redact paths in <repo> (repeatable)

Prints the digest as one JSON object, exit 0. A transcript that cannot be found,
read or recognised prints `RETRO_DIGEST=NOT_CONFIRMED reason=<text>`, exit 2, and
never an empty digest: an empty one would read as a clean session. Only the
Claude Code JSONL shape is recognised; another harness's session is reviewed from
its own context. stdlib only.
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
    ("edit_mismatch", re.compile(r"String to replace not found|Found \d+ matches of the string", re.I)),
    ("file_state", re.compile(r"has not been read yet|modified since (?:it was )?read", re.I)),
    ("timeout", re.compile(r"timed out|Exit code 143\b", re.I)),
    ("missing_path", re.compile(r"No such file or directory|does not exist|cannot find the path", re.I)),
    ("permission", re.compile(r"Permission denied|Access is denied", re.I)),
    ("exit_nonzero", re.compile(r"^\s*Exit code \d+", re.I)),
)
_COMMAND = re.compile(r"<command-name>/?([\w:.-]+)</command-name>")
_INTERRUPT = "[Request interrupted by user"
_HOOK_EVENT = re.compile(r"\b(PreToolUse|PostToolUse|UserPromptSubmit|Stop|SessionStart):(\w+) hook")
#: The fleet shim names the refusing hook module; a module name is safe to show.
_HOOK_NAME = re.compile(r"-Hook\s+([\w.-]+)")
_EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
_SEARCH_TOOLS = {"Grep", "Glob", "Read"}


class NotConfirmed(Exception):
    """The transcript could not be found, read or recognised."""


def classify_error(text: str) -> str:
    """The fixed error class of one failed tool result's text."""
    for name, pattern in ERROR_CLASSES:
        if pattern.search(text):
            return name
    return "other"


def _block_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b.get("text", "") for b in content if isinstance(b, dict))
    return ""


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


def read_records(path: Path) -> List[Dict[str, Any]]:
    """Every JSON object line of a transcript; an unrecognised file raises NotConfirmed."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise NotConfirmed(f"cannot read {path.name}: {exc.strerror or exc}") from exc
    records = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            records.append(row)
    if not any(r.get("type") in ("user", "assistant") and isinstance(r.get("message"), dict) for r in records):
        raise NotConfirmed(f"{path.name} holds no user/assistant messages in the recognised shape")
    return records


class Digest:
    """Accumulates the counts over a transcript's records, in order."""

    def __init__(self, mask: PathMask) -> None:
        self.mask = mask
        self.tool_calls: Counter[str] = Counter()
        self.errors: Dict[str, Counter[str]] = {}
        self.hook_refusals: Counter[str] = Counter()
        self.skills: Counter[str] = Counter()
        self.reads: Counter[str] = Counter()
        self.interrupts_after: Counter[str] = Counter()
        self.large: List[Tuple[int, str, str]] = []
        self.pending: Dict[str, Tuple[str, str]] = {}
        self.seen_calls: set = set()
        self.repeated_calls = 0
        self.owner_prompts = 0
        self.interrupts = 0
        self.lookups_before_edit = 0
        self.edited = False
        self.last_tool = "none"
        self.turn_ms = 0

    def add(self, rec: Dict[str, Any]) -> None:
        kind = rec.get("type")
        if kind == "system" and rec.get("subtype") == "turn_duration":
            self.turn_ms += int(rec.get("durationMs") or 0)
        msg = rec.get("message")
        if not isinstance(msg, dict):
            return
        if kind == "assistant":
            for block in msg.get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    self._tool_use(block)
        elif kind == "user":
            self._user(msg.get("content"), bool(rec.get("isMeta")))

    def _tool_use(self, block: Dict[str, Any]) -> None:
        name = str(block.get("name") or "unknown")
        args = block.get("input") if isinstance(block.get("input"), dict) else {}
        self.tool_calls[name] += 1
        self.last_tool = name
        key = hashlib.sha256(json.dumps([name, args], sort_keys=True, default=str).encode()).hexdigest()
        self.repeated_calls += key in self.seen_calls
        self.seen_calls.add(key)
        target = str(args.get("file_path") or args.get("notebook_path") or "")
        shown = self.mask(target) if target else ""
        if name == "Read" and target:
            self.reads[shown] += 1
        if name in _EDIT_TOOLS:
            self.edited = True
        elif name in _SEARCH_TOOLS and not self.edited:
            self.lookups_before_edit += 1
        if name == "Skill" and args.get("skill"):
            self.skills[str(args["skill"])] += 1
        self.pending[str(block.get("id") or "")] = (name, shown)

    def _user(self, content: Any, meta: bool) -> None:
        if isinstance(content, str):
            self._prompt_text(content, meta)
            return
        for block in content or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                self._prompt_text(str(block.get("text") or ""), meta)
            elif block.get("type") == "tool_result":
                self._tool_result(block)

    def _prompt_text(self, text: str, meta: bool) -> None:
        if text.startswith(_INTERRUPT):
            self.interrupts += 1
            self.interrupts_after[self.last_tool] += 1
            return
        for name in _COMMAND.findall(text):
            self.skills[name] += 1
        if not meta and not text.lstrip().startswith("<"):
            self.owner_prompts += 1

    def _tool_result(self, block: Dict[str, Any]) -> None:
        name, shown = self.pending.get(str(block.get("tool_use_id") or ""), ("unknown", ""))
        text = _block_text(block.get("content"))
        if len(text) >= LARGE_RESULT_CHARS:
            self.large.append((len(text), name, shown))
        if not block.get("is_error"):
            return
        cls = classify_error(text)
        self.errors.setdefault(name, Counter())[cls] += 1
        if cls == "hook_refusal":
            event, hook = _HOOK_EVENT.search(text), _HOOK_NAME.search(text)
            key = f"{event.group(1)}:{event.group(2)}" if event else f"unknown:{name}"
            self.hook_refusals[f"{key}:{hook.group(1)}" if hook else key] += 1

    def result(self, source: str) -> Dict[str, Any]:
        private = self.mask.private_session()
        large = sorted(self.large, reverse=True)[:TOP_N]
        return {
            "source": source,
            "private_session": private,
            "owner_prompts": self.owner_prompts,
            "turn_seconds": round(self.turn_ms / 1000),
            "skills": dict(self.skills.most_common()),
            "tool_calls": dict(self.tool_calls.most_common()),
            "tool_errors": {t: dict(c.most_common()) for t, c in sorted(self.errors.items())},
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
    """The digest of one transcript file."""
    records = read_records(path)
    cwd = next((str(r["cwd"]) for r in records if r.get("cwd")), "")
    digest = Digest(PathMask(cwd, private))
    for rec in records:
        digest.add(rec)
    return digest.result(path.name)


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
    parser.add_argument("transcript", nargs="?", help="a transcript .jsonl path")
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
