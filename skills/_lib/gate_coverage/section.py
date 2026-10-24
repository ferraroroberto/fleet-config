"""Week-over-week gate-coverage section of the `/audit-fleet` digest.

`build` takes this run's per-repo reads and the previous digest-state body and
returns the lines to print and the lines to store back. The state lives in the
existing digest-state issue under `gate:<repo>:` keys, beside the `structure:`
ones, so there is no second store.

A repo with no prior reading (newly adopted, last week `unknown`, no state at all)
shows `(new)`. If the previous state could not be read at all, every delta is
withheld and the section says so rather than printing zero change.
"""

from __future__ import annotations

import re
from typing import Optional

STATE_PREFIX = "gate:"
LAST_RUN_KEY = "gate-last-run-at"
FIELDS = ("ratchets", "rules", "hits")
STATE_LINE = re.compile(r"^gate:(?P<name>[^:\s]+):\s*(?P<rest>.*?)\s*$")
READING = re.compile(r"\b(ratchets|rules|hits)=(\d+)")


def short_name(table: str) -> str:
    """`dead_code_ratchet` -> `dead-code`, the name the digest row prints."""
    return table.removesuffix("_ratchet").replace("_", "-")


def totals(entry: dict) -> dict:
    """`{ratchets, rules, hits}` for one adopted reading."""
    return {"ratchets": len(entry["hits"]), "rules": len(entry["rules"]), "hits": sum(entry["hits"].values())}


def parse_state(body: str) -> dict[str, dict]:
    """`{repo: {ratchets, rules, hits}}` from the digest-state body; a non-reading line has no entry."""
    prior: dict[str, dict] = {}
    for line in body.splitlines():
        m = STATE_LINE.match(line.strip())
        if not m:
            continue
        found = {k: int(v) for k, v in READING.findall(m["rest"])}
        if all(f in found for f in FIELDS):
            prior[m["name"]] = found
    return prior


def _change(now: int, before: Optional[dict], field: str) -> str:
    if before is None:
        return "(new)"
    diff = now - before[field]
    return f"(+{diff})" if diff > 0 else f"(-{-diff})" if diff < 0 else "(±0)"


def _row(entry: dict, before: Optional[dict], deltas: bool) -> str:
    name = entry["repo"]
    if entry["state"] == "not_adopted":
        return f"- {name}  not adopted"
    if entry["state"] == "unknown":
        return f"- {name}  unknown ({entry.get('reason', 'not established')})"
    now = totals(entry)
    cells = "  ".join(f"{f} {now[f]}" + (f" {_change(now[f], before, f)}" if deltas else "") for f in FIELDS)
    per = ", ".join(f"{short_name(t)} {n}" for t, n in sorted(entry["hits"].items()))
    rules = f"; rules: {', '.join(entry['rules'])}" if entry["rules"] else ""
    return f"- {name}  {cells}  [{per}{rules}]"


def _state_line(entry: dict) -> str:
    head = f"{STATE_PREFIX}{entry['repo']}: "
    if entry["state"] == "adopted":
        return head + " ".join(f"{f}={v}" for f, v in totals(entry).items())
    return head + ("not adopted" if entry["state"] == "not_adopted" else "unknown")


def build(entries: list[dict], prior_body: Optional[str], today: str) -> dict:
    """The digest section for `entries` (one `reader.read_repo` result plus `repo` each).

    `prior_body` is the digest-state issue body, `""` when no such issue exists yet, or
    `None` when it could not be read. Returns `lines` (markdown, one per repo plus the
    fleet total) and `state_lines` (what the orchestrator stores back, date key first).
    """
    entries = sorted(entries, key=lambda e: e["repo"])
    deltas = prior_body is not None
    prior = parse_state(prior_body or "")
    lines = [_row(e, prior.get(e["repo"]), deltas) for e in entries]
    adopted = [totals(e) for e in entries if e["state"] == "adopted"]
    unknown = sum(1 for e in entries if e["state"] == "unknown")
    lines.append(
        f"- fleet total: {sum(t['ratchets'] for t in adopted)} ratchets and {sum(t['rules'] for t in adopted)} "
        f"conventions rules enabled across {len(adopted)} adopted repos; "
        f"{sum(t['hits'] for t in adopted)} baselined hits; "
        f"{len(entries) - len(adopted) - unknown} not adopted, {unknown} unknown"
    )
    if not deltas:
        lines.append("- week-over-week: unknown (previous digest state could not be read), no deltas shown")
    state_lines = [f"{LAST_RUN_KEY}: {today}"] + [_state_line(e) for e in entries]
    return {"lines": lines, "state_lines": state_lines}
