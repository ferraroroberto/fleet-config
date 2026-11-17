"""Stage 3c of /design-review — what the walk opened against the app's `data-surface` ids and its surface map.

Pure: reads a `metrics.json` document, returns one JSON-able record. The scaffold's UX surface ratchet checks ids
statically, so a dialog, drill-in or state built with no id at all is invisible to it; the walk and the e2e capture
open surfaces for real, which makes them the cross-check (project-scaffolding `docs/ux-surface-map.md`).

Two lists, both coverage findings and never part of the grade:

  * `no_id`       surfaces the run opened that carry no `data-surface`, or an id other than the one the test named
                  (`surface(page, "<name>")` takes the same name form as an id). An e2e capture is compared by its
                  name; any other screen contributes each open overlay root whose own or nearest ancestor carries
                  no id.
  * `never_walked` map entries with an element root whose id no screen showed and no capture named.

A root-less map entry (`*Root:* none — <reason>`) is exempt from both: it has no element to carry an id.
`status` is `unknown` when no measured screen carries the listing (metrics captured before it existed), and
`never_walked` is `None` when the app has no map, so a missing input never reads as "nothing missed".

Public surface:

    read_map(root)             -> {"file", "entries": [{id, rootless}], "error"}; `file` None = no map
    report(doc)                -> {"status", "no_id", "never_walked", "walked", "map"}
    report_items(rep)          -> [str]        (the report's method lines)
    summaries(rep)             -> [dict]       (filing summaries: SURF-01, SURF-02)
    carry_statuses(body, rep)  -> {id: status} (for lines already on the issue body)

stdlib only.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional

from . import measure

ID_RE = re.compile(r"[a-z0-9]+(?:[.-][a-z0-9]+)*\Z")
DEFAULT_MAP = "docs/ux-surface-map.md"
SAMPLE = 8            # ids named per line in the report and the issue; the counts stay true
SEVERITY = "P2"
CODES = ("SURF-01", "SURF-02")
STANDARD = "project-scaffolding docs/ux-surface-map.md (Code carries the id)"

# The scaffold ratchet's own entry grammar, so both read one map the same way.
_ENTRY = re.compile(r"^\s*(?:[-*]\s+)?\*\*`(?P<id>[^`]*)`")
_HEADING = re.compile(r"^#{1,6}\s")
_FENCE = re.compile(r"^\s*(```|~~~)")
_ROOT_NONE = re.compile(r"\*Root:?\*:?\s*none\b(?P<reason>[^.*]*)", re.IGNORECASE)


def parse_map(text: str) -> List[Dict[str, object]]:
    """`[{id, rootless}]` for every entry (a line opening with a bold backticked id, outside a fence).

    An entry with an invalid id is skipped: the ratchet reports it, this report only compares."""
    entries: List[Dict[str, object]] = []
    current: Optional[tuple] = None
    fenced = False

    def close() -> None:
        if current is None:
            return
        match = _ROOT_NONE.search(" ".join(current[1]))
        entries.append({"id": current[0], "rootless": bool(match and match.group("reason").strip(" \t—–-:(") != "")})

    for raw in text.splitlines():
        if _FENCE.match(raw):
            fenced = not fenced
            continue
        if fenced:
            continue
        entry = _ENTRY.match(raw)
        if entry or _HEADING.match(raw):
            close()
            current = None
        if entry and ID_RE.match(entry.group("id")):
            current = (entry.group("id"), [raw])
        elif current is not None:
            current[1].append(raw)
    close()
    return entries


def read_map(root: Optional[Path], rel: Optional[str] = None) -> Dict[str, object]:
    """The target's surface map as `{file, entries, error}`; `file` is None when it has none (not adopted: no refusal)."""
    path = Path(root or ".") / (rel or DEFAULT_MAP)
    if not root or not path.is_file():
        return {"file": None, "entries": [], "error": None}
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, ValueError) as exc:
        return {"file": (rel or DEFAULT_MAP), "entries": [], "error": f"{path.name}: {exc}"}
    return {"file": (rel or DEFAULT_MAP), "entries": parse_map(text), "error": None}


def _listed(s: dict) -> Optional[dict]:
    """A measured screen's `coverage` listing, or None when the screen carries none (never read as empty)."""
    if s.get("status") != "ok" or not isinstance(s.get("metrics"), dict):
        return None
    ids = measure.metric_value(s["metrics"], "coverage.surface_ids")
    roots = measure.metric_value(s["metrics"], "coverage.open_roots")
    if not isinstance(ids, list) or not isinstance(roots, list):
        return None
    return {"ids": {str(i) for i in ids}, "roots": [r for r in roots if isinstance(r, dict)]}


def _capture_miss(name: str, listing: dict) -> Optional[Dict[str, object]]:
    """The `no_id` record for an e2e capture whose page shows no `data-surface` equal to `name`, else None."""
    if name in listing["ids"]:
        return None
    roots = listing["roots"]
    root = roots[-1] if roots else {}   # the last open root in document order is the one the test just opened
    others = [r.get("id") for r in roots if r.get("id")]
    return {"surface": name, "root": root.get("key"), "found": (others[-1] if others else None)}


def report(doc: dict) -> Dict[str, object]:
    """The record for one metrics document (see the module docstring)."""
    no_id: Dict[str, Dict[str, object]] = {}
    walked: set = set()
    listed = False
    entries_by_id = {e["id"]: e for e in ((doc.get("surface_map") or {}).get("entries") or [])}
    for s in doc.get("screens") or []:
        if not isinstance(s, dict):
            continue
        listing = _listed(s)
        if listing is None:
            if s.get("source") == "e2e" and s.get("surface"):
                walked.add(str(s["surface"]))   # a capture that could not be listed still names a surface it opened
            continue
        listed = True
        walked |= listing["ids"]
        name = str(s["surface"]) if s.get("source") == "e2e" and s.get("surface") else None
        if name:
            walked.add(name)
            entry = entries_by_id.get(name)
            miss = _capture_miss(name, listing)
            if miss and not (entry and entry["rootless"]):
                no_id.setdefault(f"surface:{name}", miss)
            continue
        for root in listing["roots"]:
            if not root.get("id") and root.get("key"):
                no_id.setdefault(f"root:{root['key']}", {"surface": None, "root": root["key"], "found": None})
    smap = doc.get("surface_map") if isinstance(doc.get("surface_map"), dict) else None
    never: Optional[List[str]] = None
    if smap and smap.get("file") and not smap.get("error"):
        never = sorted(str(e["id"]) for e in smap["entries"] if not e["rootless"] and e["id"] not in walked)
    return {"status": "measured" if listed else "unknown",
            "map": ({"file": smap.get("file"), "entries": len(smap.get("entries") or []), "error": smap.get("error")}
                    if smap else None),
            "no_id": sorted(no_id.values(), key=lambda r: (str(r["surface"] or ""), str(r["root"] or ""))),
            "never_walked": never if listed else None,
            "walked": len(walked)}


def _name(r: dict) -> str:
    if r.get("surface"):
        return str(r["surface"]) + (f" (page shows `{r['found']}`)" if r.get("found") else "")
    return f"{r.get('root')} (open overlay)"


def _sample(items: List[str]) -> str:
    more = f" +{len(items) - SAMPLE} more" if len(items) > SAMPLE else ""
    return ", ".join(items[:SAMPLE]) + more


def report_items(rep: Optional[dict]) -> List[str]:
    """The report's method lines: what the surface-id comparison found, or why it could not."""
    if not isinstance(rep, dict) or rep.get("status") != "measured":
        return []   # a run with no `surface_map` key or no listing says nothing here: a plain walk is unchanged
    items: List[str] = []
    no_id = rep.get("no_id") or []
    if no_id:
        items.append(f"opened with no id: {len(no_id)} surface{'s' if len(no_id) != 1 else ''} the run opened carry no "
                     f"matching `data-surface` ({_sample([_name(r) for r in no_id])}); give the surface's root the id "
                     "its map entry and its `surface(page, name)` call use. Coverage only: the grade does not count it")
    smap = rep.get("map")
    if smap and smap.get("error"):
        items.append(f"surface map unreadable ({smap['error']}): never-walked entries are unknown, not none")
    elif not smap:
        pass    # a run without `--e2e` read no map, so it compared none
    elif rep.get("never_walked") is None:
        items.append("no surface map in this repo (docs/ux-surface-map.md): map entries the walk never opened are unknown")
    elif rep["never_walked"]:
        n = rep["never_walked"]
        items.append(f"never walked: {len(n)} map entr{'ies' if len(n) != 1 else 'y'} with an element root no screen showed "
                     f"and no capture named ({_sample(n)}); call surface(page, id) in the e2e test that opens each one. "
                     "Coverage only: the grade does not count it")
    return items


def summaries(rep: Optional[dict]) -> List[dict]:
    """Filing summaries for the app's managed issue; a list that is empty files nothing."""
    if not isinstance(rep, dict) or rep.get("status") != "measured":
        return []
    out = []
    no_id = rep.get("no_id") or []
    if no_id:
        out.append({"id": CODES[0], "severity": SEVERITY, "owner": "app", "standard": STANDARD, "apps": [],
                    "title": "surfaces opened by the run carry no matching data-surface id",
                    "detail": f"{len(no_id)}: {_sample([_name(r) for r in no_id])}"})
    never = rep.get("never_walked") or []
    if never:
        out.append({"id": CODES[1], "severity": SEVERITY, "owner": "app", "standard": STANDARD, "apps": [],
                    "title": "surface-map entries the walk never opened",
                    "detail": f"{len(never)}: {_sample(never)}"})
    return out


def carry_statuses(body_lines: List[str], rep: Optional[dict]) -> Dict[str, str]:
    """Statuses for the `SURF-*` lines already on the body that this run does not list again.

    `pass` = the comparison ran and the list is now empty (fixed); `unmeasured` = it could not run (carried)."""
    out: Dict[str, str] = {}
    measured = isinstance(rep, dict) and rep.get("status") == "measured"
    now = {s["id"] for s in summaries(rep)}
    for code in CODES:
        if code in now:
            continue
        if code == CODES[1] and measured and rep.get("never_walked") is None:
            out[code] = "unmeasured"       # no readable map this run, so the old list is neither confirmed nor cleared
        else:
            out[code] = "pass" if measured else "unmeasured"
    return {c: v for c, v in out.items() if any(f"**{c}**" in l for l in body_lines)}
