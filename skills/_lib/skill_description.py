"""Shared measurement of a SKILL.md frontmatter `description:` (fleet-config#626).

A skill's `description:` is always-on in every session of its repo — only the
body loads on invocation — which makes the ~50-prose-word cap (#137) the single
highest-leverage context control the fleet has. Two skills need the same
primitives over that one line and had drifted apart:

  * `/context-audit`'s `audit.py` counts the prose words against the cap, with
    quoted trigger phrases exempt (they are the routing surface and must stay
    verbatim).
  * `/context-purge`'s `check.py` asserts every quoted trigger phrase survives a
    rewrite, for exactly the same reason.

Both carried their own `description:` parser and their own "quoted phrase"
regex, and the two regexes disagreed: `audit.py` used a `["']` character class,
so a plain apostrophe opened a span that ran to the next apostrophe and swallowed
the prose between them. `chief` (`Board's … launcher's`) measured 29 words
against a real 58, and the cap gate reported green across a fleet where 21 of 49
descriptions were over. One implementation here, imported by both, so a third
copy cannot diverge a third time.

Trigger phrases are double-quoted by fleet convention (verified across every
fleet skill description); apostrophes are possessives, never quoting.

stdlib only, no I/O — callers own reading the file.
"""

from __future__ import annotations

import re
from typing import List

QUOTED = re.compile(r"\"[^\"]+\"")
_WORD = re.compile(r"\S+")


def quoted_phrases(description: str) -> List[str]:
    """Every double-quoted trigger phrase, in order, quotes included."""
    return QUOTED.findall(description)


def strip_quoted(description: str) -> str:
    """The description with quoted trigger phrases blanked out."""
    return QUOTED.sub(" ", description)


def word_count(text: str) -> int:
    """Whitespace-separated token count."""
    return len(_WORD.findall(text))


def prose_words(description: str) -> int:
    """Word count with quoted trigger phrases removed — the cap-bearing half."""
    return word_count(strip_quoted(description))


def frontmatter_description(text: str) -> str:
    """The YAML `description:` value from a SKILL.md frontmatter, or ''.

    Returns '' for a file with no frontmatter, an unterminated frontmatter, or
    no `description:` key. Callers that must not treat "no description" as
    "compliant" check the empty return and report it as unmeasured.
    """
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    front = text[3 : end if end != -1 else len(text)]
    for line in front.splitlines():
        if line.startswith("description:"):
            return line[len("description:") :].strip()
    return ""
