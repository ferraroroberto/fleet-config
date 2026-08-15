"""Shared read of one field out of a SKILL.md YAML frontmatter block (fleet-config#615).

Three call sites parsed a `---`-delimited frontmatter block for a single field's
value independently: `skills/_lib/skill_description.py`'s `frontmatter_description()`
(description only), `.claude/skills/config-map/build_data.py`'s `_frontmatter_field()`
(any field, reads the file itself). One parser here; both now delegate to it —
`frontmatter_description()` for the description-specific '' contract that
`/context-audit` and `/context-purge` depend on, `_frontmatter_field()` for the
Path-reading, `Optional[str]`-returning contract `/config-map` depends on.

stdlib only, no I/O — callers own reading the file.
"""

from __future__ import annotations

import re
from typing import Optional


def frontmatter_field(text: str, field: str) -> Optional[str]:
    """The YAML `<field>:` value from a `---`-delimited frontmatter block, or None.

    Returns None for a file with no frontmatter or no `<field>:` key. The block
    is searched from just past the opening `---`, up to a closing `\\n---` if one
    exists — an unterminated frontmatter still yields whatever fields it has.
    """
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    block = text[3:end] if end != -1 else text[3:]
    m = re.search(rf"^{re.escape(field)}:\s*(.+)$", block, re.MULTILINE)
    return m.group(1).strip() if m else None
