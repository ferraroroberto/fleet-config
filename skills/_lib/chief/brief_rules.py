"""The standard dispatch brief's worker wording, read from its one home.

`.claude/skills/chief/dispatch-brief.md` holds the channel declaration as a
quote and the numbered rules every brief carries. A rule's first paragraph is
addressed to the worker; text starting `*For the chief:*` is not. A drained
lane's brief is built from these, so it cannot hold a second copy that drifts.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List

DISPATCH_BRIEF = Path(__file__).resolve().parents[3] / ".claude" / "skills" / "chief" / "dispatch-brief.md"

_CHIEF_ONLY = "*For the chief:*"
# The numbered list runs from "1." to the first bold paragraph after it.
_RULE_LIST = re.compile(r"^1\. .*?(?=^\*\*A )", re.MULTILINE | re.DOTALL)
_RULE_START = re.compile(r"^\d+\. ", re.MULTILINE)
_QUOTE = re.compile(r"(?:^>.*\n)+", re.MULTILINE)


def _one_line(paragraph: str) -> str:
    return " ".join(paragraph.split())


def channel_declaration(text: str) -> str:
    """The quoted "where further instructions come from" block, one line per paragraph."""
    quote = _QUOTE.search(text)
    if quote is None:
        raise ValueError("dispatch-brief.md has no quoted channel declaration")
    body = "\n".join(line[1:].strip() for line in quote.group(0).splitlines())
    return "\n\n".join(_one_line(p) for p in body.split("\n\n"))


def worker_rules(text: str) -> List[str]:
    """Each numbered rule's worker wording, in order; chief-only rules are left out."""
    block = _RULE_LIST.search(text)
    if block is None:
        raise ValueError("dispatch-brief.md has no numbered rule list")
    rules = [_one_line(item.split("\n\n")[0]) for item in _RULE_START.split(block.group(0)) if item.strip()]
    return [rule for rule in rules if not rule.startswith(_CHIEF_ONLY)]


def render(template: str, source: Path = DISPATCH_BRIEF) -> str:
    """`template` with `__CHANNEL__` and `__STANDARD_RULES__` filled from the standard brief."""
    text = source.read_text(encoding="utf-8")
    numbered = "\n".join(f"{n}. {rule}" for n, rule in enumerate(worker_rules(text), start=1))
    return template.replace("__CHANNEL__", channel_declaration(text)).replace("__STANDARD_RULES__", numbered)
