"""Relevance scoring for /inspiration-scan through the local LLM hub.

Each batch of new items goes to the hub's Anthropic-shape `POST /v1/messages`
with a short profile of the fleet (every repo's `.fleet.toml` description) plus
the owner's "what I like" note. The model returns, per item, a 0-10 score, why
it fits, an experiment to try and a one-line issue idea for `/issue-add`.

A batch the hub could not score leaves its items unscored, a state of its own:
they are shown as unscored and are not marked seen, so tomorrow's run scores
them again.

stdlib only.
"""

from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from feeds import Item

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "skills" / "_lib"))
from fleet_core import fleet_toml  # noqa: E402
from worktree_residue import WT_SEP  # noqa: E402

HUB_URL = "http://127.0.0.1:8000/v1/messages"
# A subscription-backed CLI call for one 20-item batch takes tens of seconds;
# three minutes is past any healthy answer.
HUB_TIMEOUT_S = 180
BATCH_SIZE = 20
MAX_TOKENS = 4000

_SYSTEM = (
    "You rank items from newsletters, YouTube and X for one person who runs a fleet of small "
    "automation repos with coding agents. Score how likely each item gives them an idea worth "
    "trying in their own workflows. Be strict: generic news, promotion and hot takes score low. "
    "Reply with only a JSON array, one object per item, keys: id, score (integer 0-10), "
    "why_fits (one line), experiment (one line), issue_idea (one line, an issue title they could file), "
    "repo (the name of the one fleet repo from the list that should take the idea)."
)


@dataclass
class Scored:
    item: Item
    score: Optional[int]
    why_fits: str = ""
    experiment: str = ""
    issue_idea: str = ""
    suggested_repo: str = ""
    model: str = ""


def fleet_profile(automation_root: Path) -> str:
    """One line per fleet repo: its name and `.fleet.toml` description."""
    lines = []
    for card in sorted(automation_root.glob(f"*/{fleet_toml.FILENAME}")):
        repo = card.parent
        if WT_SEP in repo.name:
            continue
        data, _error = fleet_toml.load_checked(repo)
        description = str((data or {}).get("description", "")).strip()
        if description:
            lines.append(f"- {repo.name}: {description}")
    return "\n".join(lines)


def build_prompt(items: List[Item], profile: str, likes: str) -> str:
    rows = [{"id": i.id, "type": i.kind, "title": i.title, "text": i.text} for i in items]
    return (f"The fleet (one line per repo):\n{profile or '(no repo descriptions found)'}\n\n"
            f"What they like and want to try:\n{likes or '(not given)'}\n\n"
            f"Items:\n{json.dumps(rows, ensure_ascii=False)}")


def parse_reply(text: str) -> Dict[str, Dict[str, object]]:
    """Map item id to its scored fields; an unparseable reply is `ValueError`."""
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if not match:
        raise ValueError("reply holds no JSON array")
    rows = json.loads(match.group(0))
    scored: Dict[str, Dict[str, object]] = {}
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict) and isinstance(row.get("id"), str):
            try:
                row["score"] = max(0, min(10, int(row.get("score", 0))))
            except (TypeError, ValueError):
                continue
            scored[row["id"]] = row
    return scored


def call_hub(prompt: str, model: str) -> str:
    payload = {"model": model, "max_tokens": MAX_TOKENS, "system": _SYSTEM,
               "messages": [{"role": "user", "content": prompt}]}
    request = urllib.request.Request(
        HUB_URL, data=json.dumps(payload).encode("utf-8"), method="POST",
        headers={"content-type": "application/json", "x-api-key": "local-dummy",
                 "anthropic-version": "2023-06-01"})
    with urllib.request.urlopen(request, timeout=HUB_TIMEOUT_S) as response:  # noqa: S310 - fixed loopback hub URL
        body = json.loads(response.read().decode("utf-8"))
    return "".join(block.get("text", "") for block in body.get("content", []) if block.get("type") == "text")


def score_items(items: List[Item], profile: str, likes: str, model: str,
                caller: Optional[Callable[[str, str], str]] = None) -> Tuple[List[Scored], List[str]]:
    """Score every item in batches; returns the results and one error line per failed batch."""
    results: List[Scored] = []
    errors: List[str] = []
    for start in range(0, len(items), BATCH_SIZE):
        batch = items[start:start + BATCH_SIZE]
        try:
            scored = parse_reply((caller or call_hub)(build_prompt(batch, profile, likes), model))
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            errors.append(f"batch {start // BATCH_SIZE + 1}: {type(exc).__name__}")
            results.extend(Scored(item, None) for item in batch)
            continue
        for item in batch:
            row = scored.get(item.id)
            if row is None:
                results.append(Scored(item, None))
                continue
            results.append(Scored(item, int(row["score"]), str(row.get("why_fits", "")),
                                  str(row.get("experiment", "")), str(row.get("issue_idea", "")),
                                  str(row.get("repo", "")).strip(), model))
    return results, errors
