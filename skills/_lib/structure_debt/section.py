"""Week-over-week structure-debt section of the `/audit-fleet` digest.

`build` takes this run's per-repo reads and the previous digest-state body and
returns the lines to print, the lines to store back in the state, and the
headline flag. The state lives in the existing digest-state issue under
`structure:<repo>:` keys, so there is no second store.

A repo that grew is one whose files/fns/dirs count or excess rose against a
prior that was itself a reading; a repo with no prior reading (newly adopted,
last week `unknown`, no state at all) has nothing to grow from and is never
flagged. If the previous state could not be read at all, every delta is
withheld and the section says so rather than printing zero change.
"""

from __future__ import annotations

import re

STATE_PREFIX = "structure:"
LAST_RUN_KEY = "structure-last-run-at"
FIELDS = ("files", "fns", "dirs", "excess")
STATE_LINE = re.compile(r"^structure:(?P<name>[^:\s]+):\s*(?P<rest>.*?)\s*$")
READING = re.compile(r"\b(files|fns|dirs|excess)=(\d+)")
LARGEST = 3


def _total(reading: dict) -> int:
    return reading["files"] + reading["fns"] + reading["dirs"]


def parse_state(body: str) -> dict[str, dict]:
    """`{repo: reading}` from the digest-state body; a repo not adopted/unknown/malformed has no reading."""
    prior: dict[str, dict] = {}
    for line in body.splitlines():
        m = STATE_LINE.match(line.strip())
        if not m:
            continue
        found = dict((k, int(v)) for k, v in READING.findall(m["rest"]))
        if all(f in found for f in FIELDS):
            prior[m["name"]] = found
    return prior


def _delta(now: int, before: int | None) -> str:
    if before is None:
        return "(new)"
    diff = now - before
    return f"(+{diff})" if diff > 0 else f"(-{-diff})" if diff < 0 else "(±0)"


def _row(entry: dict, before: dict | None, deltas: bool) -> str:
    name, state = entry["repo"], entry["state"]
    if state == "not_adopted":
        return f"- {name}  not adopted"
    if state == "unknown":
        return f"- {name}  unknown ({entry.get('reason', 'not established')})"
    cells = []
    for field in FIELDS:
        cell = f"{field} {entry[field]}"
        if deltas:
            cell += " " + _delta(entry[field], before[field] if before else None)
        cells.append(cell)
    return f"- {name}  " + "  ".join(cells)


def _grew(entry: dict, before: dict | None) -> bool:
    return bool(before) and any(entry[f] > before[f] for f in FIELDS)


def _state_line(entry: dict) -> str:
    if entry["state"] == "adopted":
        return f"{STATE_PREFIX}{entry['repo']}: " + " ".join(f"{f}={entry[f]}" for f in FIELDS)
    return f"{STATE_PREFIX}{entry['repo']}: " + ("not adopted" if entry["state"] == "not_adopted" else "unknown")


def build(entries: list[dict], prior_body: str | None, today: str) -> dict:
    """The digest section for `entries` (one `baseline.read_repo` result plus `repo` each).

    `prior_body` is the digest-state issue body, `""` when no such issue exists yet, or
    `None` when it could not be read. Returns `lines` (markdown, one per row plus the
    total), `headline` (the "structure debt grew" sentence or None), `state_lines` (what the
    orchestrator stores back, date key first) and `grew` (the repos behind the headline).
    """
    entries = sorted(entries, key=lambda e: e["repo"])
    deltas = prior_body is not None
    prior = parse_state(prior_body or "")
    rows = [_row(e, prior.get(e["repo"]), deltas) for e in entries]
    adopted = [e for e in entries if e["state"] == "adopted"]
    grew = [e["repo"] for e in adopted if deltas and _grew(e, prior.get(e["repo"]))]
    unknown = [e["repo"] for e in entries if e["state"] == "unknown"]
    largest = sorted(adopted, key=lambda e: (-_total(e), e["repo"]))[:LARGEST]
    top = ", ".join(f"{e['repo']} ({_total(e)})" for e in largest)
    total = (
        f"- fleet total: {sum(_total(e) for e in adopted)} baselined entries "
        f"(excess {sum(e['excess'] for e in adopted)} lines) across {len(adopted)} adopted repos"
        + (f"; largest {top}" if top else "")
        + f"; {len(entries) - len(adopted) - len(unknown)} not adopted, {len(unknown)} unknown"
    )
    lines = list(rows) + [total]
    if not deltas:
        lines.append("- week-over-week: unknown (previous digest state could not be read), no deltas shown")
    headline = f"structure debt grew: {', '.join(grew)}" if grew else None
    state_lines = [f"{LAST_RUN_KEY}: {today}"] + [_state_line(e) for e in entries]
    return {"lines": lines, "headline": headline, "state_lines": state_lines, "grew": grew}
