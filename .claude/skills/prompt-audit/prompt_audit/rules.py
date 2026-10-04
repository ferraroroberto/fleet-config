"""Parsing `rules.md` and deciding a rule's verdict for a file audience.

Part of the `prompt_audit` package (fleet-config#931); see `__init__.py`.
"""

from __future__ import annotations

import re
from typing import Dict, Optional

from .common import ALWAYS_ON_KINDS, NEUTRAL


# ---- rules ----------------------------------------------------------------------

_RULE_HEAD = re.compile(r"^### (R-\d{2}) (.+?)\s+tags:\s*(.+)$")
_URL = re.compile(r"https?://[^\s()·]+")


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
                       "detect": "", "assist": False, "consider_only": False, "fix": "", "sources": []}
            rules[current["id"]] = current
        elif line.startswith("#"):
            current = None  # any other heading ends the rule: the appendix's Source: is nobody's
        elif current and line.startswith("Detect:"):
            body = line.split(":", 1)[1].strip()
            current["detect"] = re.match(r"[a-z]+", body).group(0) if re.match(r"[a-z]+", body) else ""
            # "judgment, lint-assisted": the judgment pass decides, lint only nominates candidates.
            current["assist"] = body.startswith(f"{current['detect']}, lint-assisted")
            # A dated first-cycle noise guard: hits cap at consider until the sentence is deleted.
            current["consider_only"] = "Consider-only" in body
        elif current and line.startswith("Fix shape:"):
            current["fix"] = line.split(":", 1)[1].strip()
        elif current and line.startswith("Source:"):
            current["sources"] = _URL.findall(line)
    return rules


def rule_applies_to_kind(rule: dict, kind: str) -> bool:
    scope = rule.get("file", "any")
    if scope == "any":
        return kind != "skill-ref"  # a skill's reference files answer only to rules scoped to them
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
