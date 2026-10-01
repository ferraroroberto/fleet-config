"""Budgets, verdict, the managed-issue body and the local ledger of /perf-review (fleet-config#1121).

Pure stdlib, no browser, no network. Every check is `pass`, `fail` or
`unmeasured` — a number the run could not establish is never folded into a
pass (global "unknown is its own state"). The issue body carries numbers and
URL paths only: no host, no IP, no page text, no screenshot.
"""
from __future__ import annotations

import copy
import json
import os
import re
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import audit_issue  # noqa: E402
import hooks_state  # noqa: E402

BUDGETS_PATH = Path(__file__).resolve().parent / "budgets.toml"
LEDGER_KEEP = 20
TITLE = audit_issue.PERF_REVIEW_TITLE
KIND = "perf-review"
LABEL = "perf-review"
PLAYBOOK = "fleet-config `skills/perf-review/playbook.md`"

# Failing check -> the playbook pattern that fixes it, cheapest first.
REMEDY = {
    "index.compressed": "P2 compress responses",
    "index.revalidates": "P3 ETag + 304 on the entry document",
    "endpoints.index_p95_ms": "P3 cache the stamped entry document in memory",
    "endpoints.api_p95_ms": "P4 serve hot reads from memory",
    "warm.bytes_kb": "P2 compress responses / P3 ETag + 304",
    "warm.data_ms": "P4 serve hot reads from memory / P5 concurrent boot",
    "warm.ready_ms": "P6 paint the last good data first",
    "cold.bytes_kb": "P2 compress responses / P9 load heavy libraries on first use",
    "cold.ready_ms": "P2 compress responses / P9 load heavy libraries on first use",
}


def load_budgets(override: Optional[dict] = None) -> dict:
    """The shipped budgets, with a target's `[perf.review.budgets]` merged over them key by key."""
    budgets = tomllib.loads(BUDGETS_PATH.read_text(encoding="utf-8"))
    for section, values in (override or {}).items():
        if isinstance(values, dict):
            budgets.setdefault(section, {}).update(values)
    return budgets


def load_review_block(root: Optional[Path]) -> dict:
    """The target's optional `[perf.review]` table; `{"error": ...}` when its .fleet.toml does not parse."""
    path = Path(root) / ".fleet.toml" if root else None
    if path is None or not path.is_file():
        return {}
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8", errors="replace"))
    except tomllib.TOMLDecodeError as exc:
        return {"error": f"unparseable .fleet.toml: {exc}"}
    block = data.get("perf", {}).get("review", {}) if isinstance(data.get("perf"), dict) else {}
    return dict(block) if isinstance(block, dict) else {}


def _status(value: Optional[float], limit: float) -> str:
    return "unmeasured" if value is None else ("pass" if value <= limit else "fail")


def verdict(probe: dict, load: dict, budgets: dict) -> dict:
    """`{"checks": [{id, label, budget, measured, status}], "endpoints": [...], "summary": {...}}`."""
    checks: List[dict] = []

    def add(cid: str, label: str, budget, measured, status: str) -> None:
        checks.append({"id": cid, "label": label, "budget": budget, "measured": measured, "status": status})

    legs = load.get("legs", {})
    cold, warm = legs.get("android_cold", {}), legs.get("android_warm", {})
    warm_ok = warm.get("status") == "ok" and warm.get("cache") == "trusted"
    kb = lambda leg: None if leg.get("status") != "ok" else round(leg.get("bytes", 0) / 1024)  # noqa: E731
    for cid, label, value, limit in (
        ("cold.ready_ms", "Cold launch: ready (ms)", cold.get("ready_ms") if cold.get("status") == "ok" else None,
         budgets["cold"]["ready_ms"]),
        ("cold.bytes_kb", "Cold launch: transferred (KB)", kb(cold), budgets["cold"]["bytes_kb"]),
        ("warm.ready_ms", "Warm relaunch: ready (ms)", warm.get("ready_ms") if warm_ok else None,
         budgets["warm"]["ready_ms"]),
        ("warm.data_ms", "Warm relaunch: boot data landed (ms)", warm.get("data_ms") if warm_ok else None,
         budgets["warm"]["data_ms"]),
        ("warm.bytes_kb", "Warm relaunch: transferred (KB)", kb(warm) if warm_ok else None, budgets["warm"]["bytes_kb"]),
    ):
        add(cid, label, limit, value, _status(value, limit))

    index = probe.get("index", {})
    for cid, label in (("index.compressed", "Entry document compressed"),
                       ("index.revalidates", "Entry document answers a repeat with 304")):
        want = budgets["index"][cid.split(".")[1]]
        got = index.get(cid.split(".")[1])
        add(cid, label, want, got, "unmeasured" if got is None else ("pass" if got == want or not want else "fail"))

    endpoints = []
    for path, row in sorted(probe.get("endpoints", {}).items()):
        limit = budgets["endpoints"]["index_p95_ms" if path == "/" else "api_p95_ms"]
        ok_codes = all(c in ("200", "304") for c in row.get("codes", []))
        status = _status(row.get("p95"), limit) if ok_codes else "unmeasured"
        endpoints.append({"path": path, **row, "budget": limit, "status": status})
    for cid in ("endpoints.index_p95_ms", "endpoints.api_p95_ms"):
        rows = [e for e in endpoints if (e["path"] == "/") == (cid == "endpoints.index_p95_ms")]
        statuses = {e["status"] for e in rows}
        status = "fail" if "fail" in statuses else ("pass" if statuses == {"pass"} else "unmeasured")
        over = [e["path"] for e in rows if e["status"] == "fail"]
        add(cid, "Entry document p95 (ms)" if cid.endswith("index_p95_ms") else "API endpoints p95 (ms)",
            budgets["endpoints"][cid.split(".")[1]], ", ".join(over) if over else ("all within" if rows else None), status)

    counts = {s: sum(1 for c in checks if c["status"] == s) for s in ("pass", "fail", "unmeasured")}
    overall = "over-budget" if counts["fail"] else ("unmeasured" if counts["unmeasured"] else "pass")
    # WebKit cannot be throttled, so the iPhone leg is reported, never scored.
    iphone = legs.get("iphone_cold", {})
    info = {k: iphone.get(k) for k in ("ready_ms", "data_ms", "requests")} if iphone.get("status") == "ok" else None
    if info is not None:
        info["kb"] = kb(iphone)
    return {"checks": checks, "endpoints": endpoints, "summary": {**counts, "overall": overall},
            "iphone_cold": info, "budgets_version": budgets.get("version")}


def _fmt(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        return f"{v:.0f}" if v >= 10 else f"{v:.1f}"
    return str(v)


_MARK = {"pass": "✅", "fail": "⚠️", "unmeasured": "❔"}


def render_body(v: dict, run_id: str, build: Optional[str]) -> str:
    """The measured half of the managed issue body (the Fixes and Run log sections are merged in by `merge_body`)."""
    lines = ["## Phone-load budgets", "",
             f"_Run `{run_id}` · build `{build or 'unknown'}` · budgets v{v.get('budgets_version')} · "
             f"playbook: {PLAYBOOK}. Measured read-only: GETs at phone-poll spacing, nothing restarted._", "",
             "| Check | Budget | Measured | |", "|---|---|---|---|"]
    lines += [f"| {c['label']} | {_fmt(c['budget'])} | {_fmt(c['measured'])} | {_MARK[c['status']]} |" for c in v["checks"]]
    ios = v.get("iphone_cold")
    if ios:
        lines += ["", f"iPhone (WebKit, cold, unthrottled loopback; reported, not scored): ready {_fmt(ios['ready_ms'])} ms, "
                      f"boot data {_fmt(ios['data_ms'])} ms, {_fmt(ios['requests'])} requests, {_fmt(ios['kb'])} KB."]
    lines += ["", "## Endpoints", "", "| Endpoint | n | p50 ms | p95 ms | cold ms | Budget | |", "|---|---|---|---|---|---|---|"]
    lines += [f"| `{e['path']}` | {e.get('n')} | {_fmt(e.get('p50'))} | {_fmt(e.get('p95'))} | {_fmt(e.get('cold_ms'))} "
              f"| {e['budget']} | {_MARK[e['status']]} |" for e in v["endpoints"]]
    return "\n".join(lines)


def fixes_section(v: dict) -> str:
    """A seed checklist from the failing checks — only used when the issue has no Fixes section yet."""
    items = [f"- [ ] {c['label']}: {REMEDY.get(c['id'], 'see the playbook')}" for c in v["checks"] if c["status"] == "fail"]
    return "\n".join(["## Fixes", ""] + (items or ["_Nothing over budget._"]))


def _section(body: str, name: str) -> Optional[str]:
    m = re.search(rf"^## {re.escape(name)}\n.*?(?=^## |\Z)", body or "", re.S | re.M)
    return m.group(0).rstrip() if m else None


def merge_body(existing: str, v: dict, run_id: str, build: Optional[str], date: str) -> str:
    """Fresh tables; the existing Fixes section kept verbatim (it is curated by hand); one run-log line appended."""
    fixes = _section(existing, "Fixes") or fixes_section(v)
    log = _section(existing, "Run log") or "## Run log\n"
    s = v["summary"]
    line = f"- {date} `{run_id}` build `{build or 'unknown'}`: {s['pass']} pass, {s['fail']} over budget, {s['unmeasured']} unmeasured"
    if line not in log:
        log = log.rstrip() + "\n" + line
    return "\n\n".join([render_body(v, run_id, build), fixes, log.strip()]) + "\n"


# ---- the local ledger -----------------------------------------------------

def ledger_path(target: str) -> Path:
    return hooks_state.state_dir() / "perf-review" / target / "ledger.json"


def load_ledger(target: str) -> List[dict]:
    try:
        return json.loads(ledger_path(target).read_text(encoding="utf-8")).get("entries", [])
    except (OSError, ValueError):
        return []


def record(target: str, run_id: str, v: dict, commit: Optional[str], build: Optional[str]) -> dict:
    """Append this run's few-hundred-byte entry (ids, statuses, numbers — nothing captured); keep the last `LEDGER_KEEP`."""
    entry = {"run_id": run_id, "commit": commit, "live_build": build, "budgets_version": v.get("budgets_version"),
             "checks": {c["id"]: {"status": c["status"], "measured": c["measured"]} for c in v["checks"]},
             "endpoints": {e["path"]: {"status": e["status"], "p95": e.get("p95")} for e in v["endpoints"]}}
    entries = [e for e in load_ledger(target) if e.get("run_id") != run_id] + [entry]
    path = ledger_path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".ledger-", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump({"schema_version": 1, "keep": LEDGER_KEEP, "entries": entries[-LEDGER_KEEP:]}, fh, indent=1)
    os.replace(tmp, path)
    return entry


def diff(current: dict, previous: Optional[dict]) -> dict:
    """Check and endpoint ids that went fail->pass (`fixed`) or pass->fail (`regressed`) since `previous`."""
    out = {"previous_run": previous.get("run_id") if previous else None, "fixed": [], "regressed": []}
    if not previous:
        return out
    for key in ("checks", "endpoints"):
        before, now = previous.get(key, {}), current.get(key, {})
        for k in sorted(set(before) & set(now)):
            pair = (before[k]["status"], now[k]["status"])
            if pair == ("fail", "pass"):
                out["fixed"].append(k)
            elif pair == ("pass", "fail"):
                out["regressed"].append(k)
    return out


def previous_entry(target: str, run_id: str) -> Optional[dict]:
    earlier = [e for e in load_ledger(target) if e.get("run_id") != run_id]
    return copy.deepcopy(earlier[-1]) if earlier else None
