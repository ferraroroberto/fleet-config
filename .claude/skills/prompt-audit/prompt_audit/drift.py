"""The per-repo `audit: prompt-drift findings` issue merge/backlog engine.

Part of the `prompt_audit` package (fleet-config#931); see `__init__.py`.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from .common import HOME_REPO, LEDGER_REPO, MASTER_REPO, clean, sha12
from .lint import body_start
from .dedup import dedup, norm_line


# ---- prompt-drift cleanup issues (fleet-config#833) -----------------------------

OWNER = LEDGER_REPO.split("/", 1)[0]
DRIFT_KIND = "prompt-drift"
DRIFT_TITLE = "audit: prompt-drift findings"
DRIFT_LABEL = "prompt-drift"
DRIFT_LABEL_COLOR = "5319e7"
DRIFT_LABEL_DESC = "Instruction file drifts from current prompting guidance"
# Only violations become cleanup work. A `consider` is advisory by definition, and
# the first fleet run (#882) put 152 of its 255 considers on one rule: filed, they
# would be dozens of low-value rewrites of the instruction surface. They stay in the digest.
DRIFT_VERDICTS = ("violation",)
# Rewriting either file changes what every sister or every agent reads: always a human review.
ALWAYS_HARD_FILES = (f"{HOME_REPO}/global-CLAUDE.md", f"{MASTER_REPO}/CLAUDE.md")
# Rules whose fix edits description prose around the quoted triggers, never the
# triggers themselves; the lane's preservation gate proves the triggers survived.
DESCRIPTION_PROSE_RULES = ("R-15",)
_DRIFT_ITEM = re.compile(
    r"^- \[(?P<box>[ xX])\] (?P<content>.*?)<!-- prompt-drift: id=(?P<id>[0-9a-f]{12}) tier=(?P<tier>easy|hard)"
    r" path=(?P<path>\S+) rule=(?P<rule>R-\d{2}) propagate=(?P<propagate>\S*) last-seen=(?P<seen>\S+) -->[ \t]*$")
_NOT_RESURFACED = re.compile(r" _\(not re-surfaced[^)]*\)_$")
# The same block shape `/context-purge`'s check.py preserves byte-identical; keep the two in step.
_MARKED_BLOCK = re.compile(r"<!--\s*([\w-]+:[\w-]+):start\s*-->.*?<!--\s*\1:end\s*-->", re.S)


def in_protected_span(text: str, line: Optional[int], rule: str) -> bool:
    """True when a fix at `line` would edit a marked block or a frontmatter trigger."""
    if not line:
        return False
    for m in _MARKED_BLOCK.finditer(text):
        first = text.count("\n", 0, m.start()) + 1
        if first <= line <= first + m.group(0).count("\n"):
            return True
    return line < body_start(text) and rule not in DESCRIPTION_PROSE_RULES


def drift_tier(file_against: str, rule: str, line: Optional[int], rules: Dict[str, dict], file_text: str) -> str:
    if file_against in ALWAYS_HARD_FILES or rules.get(rule, {}).get("tier") != "easy":
        return "hard"
    return "hard" if in_protected_span(file_text, line, rule) else "easy"


def _master_line(master_text: str, norm: str) -> Optional[int]:
    return next((i for i, l in enumerate(master_text.splitlines(), start=1) if norm and norm_line(l) == norm), None)


def drift_items(findings: List[dict], rules: Dict[str, dict], texts: Dict[str, str],
                master_text: str, lite_text: str) -> Dict[str, List[dict]]:
    """Per target repo, the violations to file — shared text once, on the master.

    `findings` is every judged finding (considers included, so a shared line's
    `propagate to` names every repo carrying it); only a line whose strongest
    verdict is in DRIFT_VERDICTS is filed. `texts` maps a finding path to its bytes.
    """
    master_key = f"{MASTER_REPO}/CLAUDE.md"
    grouped: Dict[Tuple[str, str, str], dict] = {}
    for f in dedup(findings, master_text, lite_text):
        norm = norm_line(f.get("text", ""))
        key = (f["file_against"], f["rule"], norm)
        g = grouped.setdefault(key, {**f, "verdicts": set(), "propagate": set()})
        g["verdicts"].add(f["verdict"])
        g["propagate"].update(p for p in f.get("propagate_to", []) if p != MASTER_REPO)
        if f["path"] == f["file_against"]:
            g.update(line=f.get("line"), note=f.get("note", ""), text=f.get("text", ""))
    out: Dict[str, List[dict]] = {}
    for (against, rule, norm), g in sorted(grouped.items()):
        if not any(v in DRIFT_VERDICTS for v in g["verdicts"]):
            continue
        line = g.get("line")
        if against == master_key and g["path"] != master_key:
            line = _master_line(master_text, norm) or line
        text = master_text if against == master_key else texts.get(against, "")
        repo = against.split("/", 1)[0]
        out.setdefault(repo, []).append({
            "id": sha12(f"{against}|{rule}|{norm}".encode()), "path": against, "rule": rule, "line": line,
            "tier": drift_tier(against, rule, line, rules, text), "text": g.get("text", ""),
            "note": g.get("note", ""), "propagate": sorted(g["propagate"]),
        })
    return out


def render_drift_item(item: dict, rules: Dict[str, dict], date: str, box: str = " ") -> str:
    rel = item["path"].split("/", 1)[1]
    where = f"{rel}:{item['line']}" if item.get("line") else rel
    rule = rules.get(item["rule"], {})
    parts = [f"**`{where}`** {item['rule']} {rule.get('title', '')} · {item['tier']} — {clean(item.get('note') or '').rstrip('.') or 'see rule'}."]
    if item.get("text"):
        parts.append(f"Offending: `{clean(item['text']).replace('`', chr(39))[:160]}`.")
    if rule.get("fix"):
        parts.append(f"Fix: {rule['fix']}")
    if item.get("propagate"):
        parts.append(f"Propagate to: {', '.join(item['propagate'])}.")
    comment = _drift_comment(item["id"], item["tier"], item["path"], item["rule"],
                             ",".join(item.get("propagate", [])), date)
    return f"- [{box}] {' '.join(parts)}{comment}"


def _drift_comment(item_id: str, tier: str, path: str, rule: str, propagate: str, seen: str) -> str:
    return (f"<!-- prompt-drift: id={item_id} tier={tier} path={path} rule={rule} "
            f"propagate={propagate} last-seen={seen} -->")


def parse_drift_body(body: str) -> Tuple[List[dict], List[str]]:
    """(checklist items, run-log lines) of an existing prompt-drift issue."""
    items = []
    for line in (body or "").splitlines():
        m = _DRIFT_ITEM.match(line)
        if m:
            items.append(dict(m.groupdict(), raw=line.rstrip()))
    log = (body or "").split("## Audit run log", 1)
    runs = [l.rstrip() for l in log[1].splitlines() if l.startswith("- ")] if len(log) == 2 else []
    return items, runs


def merge_drift(existing: str, fresh: List[dict], judged: set, unmeasured: set, rules: Dict[str, dict],
                date: str, rubric: str) -> Tuple[str, dict]:
    """The living-backlog merge: ticks survive, re-matched items refresh, gone items are tagged.

    `judged`: paths judged this run (a finding absent from a judged file is gone);
    `unmeasured`: (path, rule) pairs not established this run (their items are kept as-is).
    Items for files not judged this run (unchanged, so their verdicts still stand) are kept verbatim.
    """
    old, runs = parse_drift_body(existing)
    fresh_by_id = {i["id"]: i for i in fresh}
    judged_repos = {p.split("/", 1)[0] for p in judged}
    counts = {"new": 0, "matched": 0, "kept": 0, "not_resurfaced": 0}
    lines, seen = [], set()
    for o in old:
        seen.add(o["id"])
        f = fresh_by_id.get(o["id"])
        if o["box"] != " ":
            lines.append(o["raw"])  # a ticked item is the user's record — never rewritten
            counts["matched" if f else "kept"] += 1
            continue
        if f:
            # A shared line keeps the sisters this run did not rescan: their copies still stand.
            keep = {p for p in o["propagate"].split(",") if p and p not in judged_repos}
            lines.append(render_drift_item({**f, "propagate": sorted(set(f["propagate"]) | keep)}, rules, date))
            counts["matched"] += 1
        elif o["path"] in judged and (o["path"], o["rule"]) not in unmeasured:
            tag = f" _(not re-surfaced {date}: the file was rescanned and this finding is gone — tick it once confirmed)_"
            content = _NOT_RESURFACED.sub("", o["content"].rstrip())
            lines.append(f"- [ ] {content}{tag}{_drift_comment(o['id'], o['tier'], o['path'], o['rule'], o['propagate'], o['seen'])}")
            counts["not_resurfaced"] += 1
        else:
            lines.append(o["raw"])
            counts["kept"] += 1
    for f in fresh:
        if f["id"] not in seen:
            lines.append(render_drift_item(f, rules, date))
            counts["new"] += 1
    open_tiers = [m.group("tier") for m in map(_DRIFT_ITEM.match, lines)
                  if m and m.group("box") == " " and not _NOT_RESURFACED.search(m.group("content").rstrip())]
    counts["open"] = len(open_tiers)
    counts["tier"] = "none" if not open_tiers else ("hard" if "hard" in open_tiers else "easy")
    hard = open_tiers.count("hard")
    runs.append(f"- {date} @ rubric `{rubric[:12]}`: +{counts['new']} new, {counts['matched']} re-matched, "
                f"{counts['kept']} kept (file not rescanned), {counts['not_resurfaced']} not re-surfaced")
    body = "\n".join([
        "Surfaced by `/prompt-audit` (fleet-config#831), kept up to date across runs. Only `violation` verdicts are "
        "filed here; `consider` verdicts stay advisory in the prompt-audit ledger digest. Rules, reasons and fix "
        "shapes: `.claude/skills/prompt-audit/rules.md` in fleet-config.",
        "",
        f"**Tier (`/cleanup-fleet` prompt-drift rule): {counts['tier']}** — {counts['open']} open item(s): "
        f"{counts['open'] - hard} easy, {hard} hard.",
        "",
        "## Findings",
        "",
        *lines,
        "",
        "## Context",
        "",
        "Each item names the offending line and the smallest fix. A cleanup lane must prove every edited instruction "
        "file kept its marked blocks and quoted triggers (`.claude/skills/context-purge/check.py --base <default branch>` "
        "in fleet-config) and walk the file's directive inventory before shipping. A `Propagate to:` item is fixed on "
        "the scaffolding master first, then carried to the named repos. Never tick an item by hand without the fix.",
        "",
        "## Audit run log",
        "",
        *runs,
    ]) + "\n"
    return body, counts
