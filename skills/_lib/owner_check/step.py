"""The exact step an owner note carries: `run` lines and `expect`, and what is too vague.

A note is useful only when the owner can copy, run and answer without opening the
issue. Each `run` line names where it happens (`WHERE`) and holds the literal
command, URL or screen; `expect` says what success looks like. Pure: no I/O.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Sequence

from owner_check import block

# Where a step happens. The first two take a command; `browser` takes a URL; a step
# with no command names the screen it happens on.
TERMINAL, ELEVATED, BROWSER, PHONE, SCREEN = "terminal", "elevated", "browser", "phone", "screen"
WHERE: Dict[str, str] = {
    TERMINAL: "a command in a plain terminal",
    ELEVATED: "a command in an elevated shell",
    BROWSER: "a URL to open on the PC",
    PHONE: "a URL or screen on the phone",
    SCREEN: "a screen or control of a desktop app or device",
}
_ALIASES = {"plain terminal": TERMINAL, "elevated shell": ELEVATED}

USAGE = (f'--run "<where>: <literal command, URL or screen>" (repeat, in order; where: {", ".join(WHERE)}) '
         'and --expect "<what success looks like>"')

# Text that sends the owner somewhere else to find the actual step.
_REFERENCE = re.compile(
    r"\b(documented|as described|described (above|below|in)|(see|read|follow|check) (the )?"
    r"((docs?|issue|readme|pr|above|below|body|comment)\b|\S+\.md\b)|per (the )?(docs?|issue|readme|pr)\b|"
    r"(the|that) (usual|right|correct|appropriate|relevant|same|above|following|necessary|recorded)"
    r"( [\w-]+){0,2}? (command|script|steps)\b|"
    r"(in|from) the (issue|docs?|readme|pr)\b|the way the (issue|docs?) describes)", re.IGNORECASE)
# A `<name>` or an ellipsis still has to be filled in, so it cannot be copied and run.
_PLACEHOLDER = re.compile(r"<[A-Za-z][\w .|/-]*>|…|(?<!\S)\.\.\.(?!\S)")
# A command line that opens with one of these is a sentence, not a command.
_PROSE_OPENERS = frozenset({"run", "execute", "use", "open", "press", "click", "check", "then", "the", "a"})
_URL = re.compile(r"https?://\S+|\b(localhost|127\.0\.0\.1):\d+", re.IGNORECASE)
# A setting's name followed by a value, so `BOT_TOKEN=abc...` counts and `fleet_password.py` does not.
_SECRET = re.compile(r"(?<![a-z])(password|passwd|token|secret|api[_-]?key|pin)(?![a-z])\s*[=:]\s*"
                     r"[\"']?[A-Za-z0-9_\-:.]{4,}", re.IGNORECASE)
_EMPTY_EXPECT = re.compile(r"^\W*(it )?(just )?(works?|working|success(ful)?|ok(ay)?|passes|done|fine)"
                           r"( (fine|correctly|properly|as expected))?\W*$", re.IGNORECASE)
_MIN_EXPECT_WORDS = 3


def refers_elsewhere(text: str) -> Optional[str]:
    """Why `text` makes the owner look the step up, or None."""
    m = _REFERENCE.search(text or "")
    if m:
        return f"says {m.group(0)!r} instead of the step itself; write the exact command, URL or screen"
    return None


def _run_problem(where: str, text: str) -> Optional[str]:
    problem = block.vague_text(text) or refers_elsewhere(text)
    if problem:
        return problem
    hole = _PLACEHOLDER.search(text)
    if hole:
        return f"holds the placeholder {hole.group(0)!r}, so it cannot be copied and run"
    if _SECRET.search(text):
        return "carries what looks like a secret value; a note is public, so name the setting, never its value"
    if where in (TERMINAL, ELEVATED):
        if text.split()[0].lower() in _PROSE_OPENERS:
            return "is a sentence, not a command; give the literal command line"
    elif where == BROWSER:
        if not _URL.search(text):
            return "has no URL to open"
    elif not _URL.search(text) and len(text.split()) < 2:
        return "names no URL or screen"
    return None


def parse_run(text: str) -> Dict[str, str]:
    """`<where>: <text>` as `{"where", "text"}`; ValueError names what is wrong with it.

    The command's own spacing is kept: collapsing it would change a quoted argument.
    """
    head, sep, rest = str(text or "").partition(":")
    where = head.strip().lower()
    where = _ALIASES.get(where, where)
    if not sep or where not in WHERE:
        raise ValueError(f"--run {text!r} must start with where it runs: one of {', '.join(WHERE)}, then ': '")
    command = rest.strip()
    if "\n" in command or "\r" in command:
        raise ValueError("--run holds a line break; pass one --run per command")
    problem = _run_problem(where, command)
    if problem:
        raise ValueError(f"--run {text!r} {problem}")
    return {"where": where, "text": command}


def expect_problem(text: str) -> Optional[str]:
    """Why `expect` does not say what success looks like, or None."""
    text = " ".join(str(text or "").split())
    problem = block.vague_text(text) or refers_elsewhere(text)
    if problem:
        return problem
    if _EMPTY_EXPECT.match(text) or len(text.split()) < _MIN_EXPECT_WORDS:
        return "says nothing the owner can see; name what appears, or what changes"
    return None


def parse_step(runs: Sequence[str], expect: str, try_text: str = "") -> List[Dict[str, str]]:
    """The parsed `run` lines of a complete step; ValueError for the first thing too vague."""
    missing = [flag for flag, value in (("--run", runs), ("--expect", (expect or "").strip())) if not value]
    if missing:
        raise ValueError(f"{' and '.join(missing)} missing: every owner step carries {USAGE}")
    problem = refers_elsewhere(try_text)
    if problem:
        raise ValueError(f"--try {problem}")
    parsed = [parse_run(text) for text in runs]
    problem = expect_problem(expect)
    if problem:
        raise ValueError(f"--expect {problem}")
    return parsed


_DRAFT_LINE = re.compile(r"^[ \t]*(?:[-*] )?(?:\*\*)?(run|expect)(?:\*\*)?:[ \t]*(.*?)[ \t]*$", re.IGNORECASE)
_DRAFT_TRY = re.compile(r"Owner validation:.*?\btry:[ \t]*(.+)$", re.IGNORECASE)


def draft_step(body: str) -> Dict[str, object]:
    """What an issue draft already says of the owner step: `try`, `run` lines and `expect`.

    Only the lines directly under the `Owner validation:` line are read, so a
    "run:" elsewhere in the body is never taken for the owner's.
    """
    found: Dict[str, object] = {"try": "", "run": [], "expect": ""}
    inside = False
    for line in (body or "").splitlines():
        if "owner validation:" in line.lower():
            inside = True
            m = _DRAFT_TRY.search(line)
            found["try"] = m.group(1).strip() if m else ""
            continue
        if not inside:
            continue
        m = _DRAFT_LINE.match(line)
        if not m:
            break
        value = block.unfence(m.group(2))
        if m.group(1).lower() == "run":
            found["run"].append(value)
        else:
            found["expect"] = value
    return found
