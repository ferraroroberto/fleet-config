"""The `[upstream]` table in a repo's `.fleet.toml` and the rule that moves its watermark.

The table is the only record of what was brought in from the source: no provenance line
goes into commits, issues or PR bodies. So the watermark must never jump an item that was
neither merged nor deliberately skipped -- a gap there would be lost for good. It also lists
the local features handed back to the source as specs (`offered`), so a later forward sync
can recognise the source's own version of one.

stdlib only.
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Set, Tuple

_LIB = Path(__file__).resolve().parent.parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from fleet_core import fleet_toml  # noqa: E402

FILENAME = fleet_toml.FILENAME

# Header lines of the tables this module owns; every other line of the file is kept.
_OWN_HEADER = re.compile(r"^\s*(\[upstream\]|\[\[upstream\.(skipped|offered)\]\])\s*(#.*)?$")
_ANY_HEADER = re.compile(r"^\s*\[")


@dataclass(frozen=True)
class Skip:
    sha: str
    reason: str


@dataclass(frozen=True)
class Offer:
    """A local feature handed back to the source as a spec; `source_issue` is the
    source's issue number when it was filed there, None when handed over another way."""
    local_sha: str
    feature: str
    source_issue: Optional[int] = None


@dataclass
class UpstreamState:
    url: str
    branch: str
    synced_sha: str
    skipped: List[Skip] = field(default_factory=list)
    offered: List[Offer] = field(default_factory=list)

    def skipped_shas(self) -> Set[str]:
        return {s.sha for s in self.skipped}

    def offered_shas(self) -> Set[str]:
        return {o.local_sha for o in self.offered}


def advance_watermark(watermark: str, order: Sequence[str], merged: Iterable[str],
                      skipped: Iterable[str]) -> str:
    """The new watermark: the last item of the run, from the start of `order` (the
    source's items after `watermark`, oldest first), where every item is merged or
    skipped. The first item that is neither stops the walk, so a failed or unworked
    item holds the watermark before it whatever comes after."""
    done = set(merged) | set(skipped)
    new = watermark
    for sha in order:
        if sha not in done:
            break
        new = sha
    return new


def _parse(table: object) -> Optional[UpstreamState]:
    if not isinstance(table, dict):
        return None
    url, branch, synced = table.get("url"), table.get("branch", "main"), table.get("synced_sha")
    if not all(isinstance(v, str) and v for v in (url, branch, synced)):
        return None
    skipped: List[Skip] = []
    for entry in table.get("skipped", []):
        if not (isinstance(entry, dict) and isinstance(entry.get("sha"), str)
                and isinstance(entry.get("reason"), str)):
            return None
        skipped.append(Skip(sha=entry["sha"], reason=entry["reason"]))
    offered: List[Offer] = []
    for entry in table.get("offered", []):
        if not (isinstance(entry, dict) and isinstance(entry.get("local_sha"), str)
                and isinstance(entry.get("feature"), str)):
            return None
        issue = entry.get("source_issue")
        if issue is not None and (isinstance(issue, bool) or not isinstance(issue, int)):
            return None
        offered.append(Offer(local_sha=entry["local_sha"], feature=entry["feature"], source_issue=issue))
    return UpstreamState(url=url, branch=branch, synced_sha=synced, skipped=skipped, offered=offered)


def parse_text(text: Optional[str]) -> Tuple[Optional[UpstreamState], str]:
    """`(state, status)` from `.fleet.toml` text; status is `absent` (no file or no
    table), `invalid` (unparseable file or a table missing a field) or `ok`."""
    if text is None:
        return None, "absent"
    data = fleet_toml.parse(text)
    if data is None and text.strip():
        return None, "invalid"
    return _from_data(data)


def _from_data(data: Optional[dict]) -> Tuple[Optional[UpstreamState], str]:
    if not data or "upstream" not in data:
        return None, "absent"
    state = _parse(data["upstream"])
    return (state, "ok") if state else (None, "invalid")


def read_state(repo: Path) -> Tuple[Optional[UpstreamState], str]:
    """As `parse_text`, for the repo's working-tree `.fleet.toml`."""
    data, error = fleet_toml.load_checked(repo)
    return (None, "invalid") if error else _from_data(data)


def _quote(value: str) -> str:
    # A JSON string is a valid TOML basic string: same escapes, \uXXXX included.
    return json.dumps(value)


def render_table(state: UpstreamState) -> str:
    lines = [
        "[upstream]",
        "# Written by /upstream-sync and /downstream-sync (skills/_lib/upstream_sync); never by hand.",
        f"url = {_quote(state.url)}",
        f"branch = {_quote(state.branch)}",
        f"synced_sha = {_quote(state.synced_sha)}",
    ]
    for skip in state.skipped:
        lines += ["", "[[upstream.skipped]]", f"sha = {_quote(skip.sha)}", f"reason = {_quote(skip.reason)}"]
    for offer in state.offered:
        lines += ["", "[[upstream.offered]]", f"local_sha = {_quote(offer.local_sha)}",
                  f"feature = {_quote(offer.feature)}"]
        if offer.source_issue is not None:
            lines.append(f"source_issue = {offer.source_issue}")
    return "\n".join(lines) + "\n"


def _strip_own_tables(text: str) -> str:
    kept: List[str] = []
    inside = False
    for line in text.splitlines():
        if _OWN_HEADER.match(line):
            inside = True
            continue
        if inside and _ANY_HEADER.match(line):
            inside = False
        if not inside:
            kept.append(line)
    return "\n".join(kept).rstrip("\n")


def write_state(repo: Path, state: UpstreamState) -> None:
    """Replace the `[upstream]` tables at the end of `.fleet.toml`, keeping every other
    line; creates the file when absent. Raises ValueError, writing nothing, when the
    result would not parse back to `state`."""
    path = repo / FILENAME
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        text = ""
    rest = _strip_own_tables(text)
    new = (rest + "\n\n" if rest else "") + render_table(state)
    back, status = parse_text(new)
    if status != "ok" or back != state:
        raise ValueError(f"{FILENAME}: the rewritten [upstream] table does not read back ({status})")
    # runtime-write-exempt: the repo's own tracked .fleet.toml, shipped through a lane's PR
    path.write_text(new, encoding="utf-8", newline="\n")
