"""A stable signature per failed tool result, so one mistake counts the same across sessions.

The signature is `tool|class|template`. The template comes only from text a harness,
hook or ratchet writes itself, never from free model or user text: a fully fixed
line (`Exit code 2`) found anywhere in the result, a harness's own error wording on
the result's first line, or the hook or ratchet the text names. Paths, numbers, ids
and quoted strings in it are masked, so two errors that differ only there match.
Anything else falls back to the span the error class matched, which is pattern text.
stdlib only.
"""

from __future__ import annotations

import re
from typing import List, Optional, Pattern, Sequence

#: A template longer than this is cut: the head names the mistake, the tail is detail.
TEMPLATE_CHARS = 120

#: Lines a harness writes whole, with nothing but a number in them; safe wherever they sit.
FIXED_LINES: Sequence[Pattern[str]] = tuple(re.compile(p) for p in (
    r"^Exit code:? \d+$",
    r"^Process exited with code \d+$",
    r"^Command exited with code \d+$",
    r"^Command timed out after \d+ (?:seconds|ms|m)$",
    r"^Command aborted$",
))
#: A harness's own error wording, trusted on the result's first line only, where it opens.
HARNESS_FIRST_LINES: Sequence[Pattern[str]] = tuple(re.compile(p) for p in (
    r"^<tool_use_error>",
    r"^(?:Could not find|Found \d+ occurrences of|No changes made to|Path not found:|Not a directory:)",
    r"^(?:Could not edit file:|Offset \d+ is beyond|Working directory does not exist:|Unknown tool name:)",
    r"^(?:edits\[\d+\]|oldText must not be empty)",
    r"^(?:apply_patch verification failed|failed to parse function arguments|unsupported call)",
    r"^(?:File has not been read yet|File does not exist|String to replace not found|Found \d+ matches of the string)",
))
_RATCHET_HEAD = re.compile(r"^\s*([a-z][a-z ]*?) ratchet: ")
#: Headers that report no failure: a pass, and the notes the structure ratchet prints before its verdict.
_RATCHET_OK = re.compile(r"^\s*[a-z][a-z ]*? ratchet: (?:PASS\b|warning band\b|layout check not applicable\b"
                         r"|the .+? (?:is|are) not recorded in\b)")
_RULE = re.compile(r"\[([a-z][a-z0-9 _-]*)\]")
#: Order matters: quotes and paths go first so the digits inside them are not masked twice.
_MASKS: Sequence[tuple] = (
    (re.compile(r"'[^'\n]*'|\"[^\"\n]*\"|`[^`\n]*`"), "<q>"),
    # A path never ends in sentence punctuation or runs into a following tag.
    (re.compile(r"[A-Za-z]:[\\/](?:[^\s:,;()<>]*[^\s:,;()<>.])?"), "<path>"),
    (re.compile(r"(?<![\w<])(?:\.{1,2}/|/)?[\w.-]+(?:[\\/](?:[\w.-]*[\w-])?)+"), "<path>"),
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I), "<id>"),
    (re.compile(r"\b(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{7,}\b", re.I), "<id>"),
    (re.compile(r"\d+(?:\.\d+)?"), "<n>"),
)


def mask(line: str) -> str:
    """One line with its paths, ids, numbers and quoted strings replaced by placeholders."""
    for pattern, placeholder in _MASKS:
        line = pattern.sub(placeholder, line)
    return " ".join(line.split())[:TEMPLATE_CHARS]


def ratchet_rules(text: str) -> List[str]:
    """`<name>_ratchet:<rule>` for each rule a failing ratchet's output names, in order.

    A ratchet prints `<name> ratchet: ...` and then one line per finding carrying its
    rule id in brackets. A failing header with no rule line records rule `-`. A header
    that reports no failure closes the block, so its `[warning]` lines name no rule.
    """
    found: List[str] = []
    current: Optional[str] = None
    named = False
    for line in text.splitlines():
        ok = _RATCHET_OK.match(line)
        head = None if ok else _RATCHET_HEAD.match(line)
        if head or ok:
            if current and not named:
                found.append(f"{current}:-")
            current = f"{head.group(1).strip().replace(' ', '_')}_ratchet" if head else None
            named = False
            continue
        rule = _RULE.search(line) if current else None
        if rule:
            found.append(f"{current}:{rule.group(1).strip().replace(' ', '-').replace('_', '-')}")
            named = True
    if current and not named:
        found.append(f"{current}:-")
    return list(dict.fromkeys(found))


def template(text: str, class_span: str) -> str:
    """The masked own-text line that names this error, else the class's matched span, else `-`."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if lines and any(p.search(lines[0]) for p in HARNESS_FIRST_LINES):
        return mask(lines[0])
    for line in lines:
        if any(p.match(line) for p in FIXED_LINES):
            return mask(line)
    return mask(class_span) if class_span else "-"


def signature(tool: str, cls: str, text: str, class_span: str, hooks: Sequence[str] = ()) -> str:
    """`tool|class|template` for one failed tool result."""
    if cls == "hook_refusal":
        shown = "+".join(hooks) or "unknown"
    else:
        rules = ratchet_rules(text)
        shown = rules[0] if rules else template(text, class_span)
    return f"{tool}|{cls}|{shown}"
