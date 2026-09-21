"""Parsing `rules.md` and deciding a rule's verdict for a file audience.

Part of the `prompt_audit` package (fleet-config#931); see `__init__.py`.
"""

from __future__ import annotations

import re
from typing import Dict, Optional

from .common import ALWAYS_ON_KINDS, NEUTRAL


# ---- rules ----------------------------------------------------------------------

_RULE_HEAD = re.compile(r"^### (R-\d{2}) (.+?)\s+tags:\s*(.+)$")


def parse_rules(text: str) -> Dict[str, dict]:
    rules: Dict[str, dict] = {}
    current = None
    for line in text.splitlines():
        m = _RULE_HEAD.match(line)
        if m:
            tags = re.findall(r"\[([^\]]+)\]", m.group(3))
            plain = [t for t in tags if ":" not in t]
            kv = dict(t.split(":", 1) for t in tags if ":" in t)
            current = {"id": m.group(1), "title": m.group(2).strip(),
                       "vendor": plain[0].strip() if plain else "",
                       "file": kv.get("file", "any").strip(), "tier": kv.get("tier", "").strip(),
                       "detect": "", "fix": ""}
            rules[current["id"]] = current
        elif current and line.startswith("Detect:"):
            current["detect"] = line.split(":", 1)[1].strip().split(" ", 1)[0]
        elif current and line.startswith("Fix shape:"):
            current["fix"] = line.split(":", 1)[1].strip()
    return rules


def rule_applies_to_kind(rule: dict, kind: str) -> bool:
    scope = rule.get("file", "any")
    if scope == "any":
        return True
    if scope == "claude-md":
        return kind in ALWAYS_ON_KINDS
    return scope == kind


def verdict_cap(vendor_tag: str, audience: str, audiences: Dict[str, dict]) -> Optional[str]:
    """Strongest verdict a rule can reach for a reader; None = does not apply."""
    if vendor_tag == "shared":
        return "violation"
    if vendor_tag == "conflict":
        return "violation" if audience == NEUTRAL else None
    if audience == NEUTRAL:
        return "consider"
    return "violation" if audiences.get(audience, {}).get("vendor") == vendor_tag else None
