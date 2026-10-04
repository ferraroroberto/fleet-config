"""Hand-fed leads: outside claims (videos, posts, talks) traced back to a vendor sentence (fleet-config#1129).

Part of the `prompt_audit` package (fleet-config#931); see `__init__.py`.

`leads.toml` is edited by hand through a PR, like `sources.toml`. A lead never
becomes a rule by itself: each claim either names the tracked section that says
the same thing (`trace = "<source id>#<section>"`, after which the coverage
check owns it) or is traced once by a worker, and the result is kept in
`state.json` so it is never traced again. An untraced claim is reported as
`unverified` and creates no rule and no `prompt-drift` item.
"""

from __future__ import annotations

import datetime as dt
import tomllib
from pathlib import Path
from typing import Dict, List, Optional

from .common import SKILL_DIR, sha12

LEADS_TOML = SKILL_DIR / "leads.toml"
KINDS = ("video", "post", "talk", "thread")
RESULTS = ("traced", "untracked-vendor", "unverified")
LEAD_MARKER = "prompt-audit-lead"


class LeadsError(ValueError):
    """`leads.toml` is missing or malformed: never read as "no leads"."""


def claim_id(lead: str, text: str) -> str:
    return sha12(f"{lead}\n{text}".encode("utf-8"))


def load_leads(sources: Dict[str, dict], path: Path = LEADS_TOML) -> List[dict]:
    """Parse and validate `leads.toml` into [{id, url, kind, added, claims: [{id, text, trace}]}]."""
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except OSError as exc:
        raise LeadsError(f"{path.name} unreadable: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise LeadsError(f"{path.name} is not valid TOML: {exc}") from exc
    leads = data.get("leads")
    if not isinstance(leads, dict) or not leads:
        raise LeadsError(f"{path.name} has no [leads.<id>] tables")
    out = []
    for lid, lead in leads.items():
        where = f"[leads.{lid}]"
        if not isinstance(lead, dict):
            raise LeadsError(f"{where} is not a table")
        url, kind, added, claims = lead.get("url"), lead.get("kind"), lead.get("added"), lead.get("claims")
        if not isinstance(url, str) or not url.startswith("https://"):
            raise LeadsError(f"{where} url must be an https:// string")
        if kind not in KINDS:
            raise LeadsError(f"{where} kind must be one of {', '.join(KINDS)}, got {kind!r}")
        if not isinstance(added, dt.date):
            raise LeadsError(f"{where} added must be a TOML date")
        if not isinstance(claims, list) or not claims:
            raise LeadsError(f"{where} claims must be a non-empty list")
        parsed = []
        for i, c in enumerate(claims, start=1):
            text, trace = (c.get("text"), c.get("trace", "")) if isinstance(c, dict) else (None, None)
            if not isinstance(text, str) or not text.strip() or not isinstance(trace, str):
                raise LeadsError(f"{where} claim {i} needs a text string and a trace string (\"\" when untraced)")
            if trace:
                sid, sep, section = trace.partition("#")
                if not sep or not section.strip() or sid not in sources:
                    raise LeadsError(f"{where} claim {i} trace {trace!r} is not <tracked source id>#<section>")
            parsed.append({"id": claim_id(lid, text), "text": text.strip(), "trace": trace})
        out.append({"id": lid, "url": url, "kind": kind, "added": added.isoformat(), "claims": parsed})
    return out


def resolve(leads: List[dict], state: dict) -> dict:
    """Each claim's standing: `trace` set in leads.toml, else the recorded trace result, else open.

    Only open claims go to a worker; a claim already resolved in `state.json`
    (whatever the result) is never traced again.
    """
    recorded = state.get("leads", {}) if isinstance(state.get("leads", {}), dict) else {}
    out, open_ids = [], []
    for lead in leads:
        claims = []
        for c in lead["claims"]:
            rec = recorded.get(c["id"]) or {}
            if c["trace"]:
                status, where = "traced", c["trace"]
            elif rec.get("result") in RESULTS:
                status, where = rec["result"], rec.get("where", "")
            else:
                status, where = "open", ""
                open_ids.append(c["id"])
            claims.append(dict(c, status=status, where=where))
        out.append(dict(lead, claims=claims))
    return {"leads": out, "open": open_ids}


def mark(state: dict, cid: str, result: str, where: str, today: Optional[dt.date] = None) -> dict:
    if result not in RESULTS:
        raise ValueError(f"result must be one of {', '.join(RESULTS)}")
    state.setdefault("leads", {})[cid] = {"result": result, "where": where,
                                          "date": (today or dt.date.today()).isoformat()}
    return state


def digest_lines(resolved: Optional[dict]) -> List[str]:
    """The digest's leads section: counts, then every claim that is not plainly traced."""
    if not resolved or not isinstance(resolved.get("leads"), list):
        return ["**Leads:** not-checked"]
    claims = [(lead, c) for lead in resolved["leads"] for c in lead["claims"]]
    n = {s: sum(1 for _, c in claims if c["status"] == s) for s in ("traced", "untracked-vendor", "unverified", "open")}
    out = [f"**Leads:** {len(resolved['leads'])} lead(s), {len(claims)} claim(s): traced {n['traced']}, "
           f"untracked vendor page {n['untracked-vendor']}, unverified {n['unverified']}, open {n['open']}"]
    for lead, c in claims:
        if c["status"] != "traced":
            note = f" ({c['where']})" if c["where"] else ""
            out.append(f"- `{lead['id']}` **{c['status']}**: {c['text']}{note}")
    return out


def suggestions_comment(resolved: dict, known: str = "") -> Optional[str]:
    """Update-issue comment: suggested `trace =` values and proposed new sources, once per claim."""
    items = []
    for lead in resolved.get("leads", []):
        for c in lead["claims"]:
            if c["status"] in ("traced", "untracked-vendor") and not c["trace"] and c["where"]:
                if f"<!-- {LEAD_MARKER}: id={c['id']} " in known:
                    continue
                what = (f"set `trace = \"{c['where']}\"`" if c["status"] == "traced"
                        else f"add a `[sources.*]` block for {c['where']}, then set the trace")
                items.append(f"- [ ] `{lead['id']}`: \"{c['text']}\": {what} <!-- {LEAD_MARKER}: id={c['id']} -->")
    if not items:
        return None
    return "\n".join(["**Leads: trace results to record in `leads.toml`** (a lead never becomes a rule by itself).",
                      ""] + items) + "\n"
