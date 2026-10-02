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
import statistics
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
# A cold sample is an outlier when it sits more than twice the median AND at least this far above it:
# a first load right after a restart read 3072 ms on an app that otherwise loads in ~370 ms (#1140).
OUTLIER_FACTOR = 2.0
OUTLIER_MIN_MS = 500
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
    "warm.bytes_kb": "read the warm split first (`SPLIT` line). `/api/` bytes dominate: P12 slim an oversized payload / "
                     "P8 fetch only what's new (a data problem; cache fixes will not help). Other bytes dominate: "
                     "P3 ETag + 304 / P11 stamps / P2 compress",
    "warm.data_ms": "P4 serve hot reads from memory / P5 concurrent boot",
    "warm.ready_ms": "P6 paint the last good data first",
    "cold.bytes_kb": "name the largest response first (`TOP` lines): P12 slim an oversized payload / P2 compress responses / "
                     "P9 load heavy libraries on first use",
    "cold.ready_ms": "P2 compress responses / P9 load heavy libraries on first use",
    "cache.stamping": "P11 cache-busting stamps cover the import graph (one fleet hash, or a transitive graph hash)",
    "ready.selector": "fix the target's `[perf.review] ready_selector`: it must be visible on the landing view, "
                      "not a card on another tab (a config fix, not an app fix)",
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


def cold_ready(cold: dict) -> dict:
    """The scored cold "ready": the median of the cold loads that reached ready, and which loads were outliers.

    `load.py` takes several fresh-context cold loads and lists them in `ready_samples_ms` (None = never
    reached ready); a leg without the list scores its one reading. One slow first load must not fail an app.
    """
    if cold.get("status") != "ok":
        return {"value": None, "samples": [], "outliers": []}
    raw = list(cold.get("ready_samples_ms") or [cold.get("ready_ms")])
    reached = [s for s in raw if s is not None]
    if not reached:
        return {"value": None, "samples": raw, "outliers": []}
    median = statistics.median(reached)
    outliers = [s for s in reached if s > median * OUTLIER_FACTOR and s - median >= OUTLIER_MIN_MS]
    return {"value": round(median), "samples": raw, "outliers": outliers}


def top_responses(leg: dict) -> List[dict]:
    """The leg's largest responses, each with its `share` of the leg's transferred bytes (a percentage, 0-100)."""
    total = leg.get("bytes") or 0
    return [{**r, "share": round(100 * r["bytes"] / total) if total else None} for r in leg.get("top_responses") or []]


def bytes_split(leg: dict) -> Optional[dict]:
    """`{api_kb, asset_kb}` of a leg's transferred bytes, or None when the leg did not record the split.

    A relaunch that fails its transfer budget has two different problems: assets that missed the HTTP cache
    (a stamping / ETag fix) and live `/api/` data it must re-fetch (a payload fix). After task-os's fixes its
    warm transfer was all data, and a cache fix would have been the wrong advice (#1151).
    """
    if leg.get("api_bytes") is None or leg.get("asset_bytes") is None:
        return None
    return {"api_kb": round(leg["api_bytes"] / 1024), "asset_kb": round(leg["asset_bytes"] / 1024)}


def _split_line(label: str, s: dict) -> str:
    return (f"{label} transfer is {s['api_kb']} KB of `/api/` data re-fetched and {s['asset_kb']} KB of other "
            f"responses that missed the cache (assets, entry document): " +
            ("a data problem, not a cache problem." if s["api_kb"] > s["asset_kb"] else "a cache problem, not a data problem."))


def _top_line(r: dict) -> str:
    share = f", {r['share']}% of the transfer" if r.get("share") is not None else ""
    kind = f" {r['kind']}," if r.get("kind") else ""
    return f"`{r['path']}`:{kind} {_fmt_bytes(r['bytes'])} on the wire, {r.get('encoding') or 'identity'}{share}"


def _fmt_bytes(n: int) -> str:
    return f"{n / 1024 / 1024:.2f} MB" if n >= 1024 * 1024 else f"{n / 1024:.0f} KB"


def ready_selector_state(cold: dict, warm: dict) -> Optional[dict]:
    """`{measured, status}` for a declared `ready_selector`, or None when none was declared (ready = FCP).

    A selector that never shows leaves "ready" `unmeasured` on both legs, which on its own reads as a slow
    or unreachable app; this names the real cause (home-automation declared a card on another tab, #776).
    """
    if cold.get("status") != "ok" or cold.get("ready_by") not in ("selector", "selector-not-visible"):
        return None
    hidden = [name for name, leg in (("cold", cold), ("warm", warm))
              if leg.get("status") == "ok" and leg.get("ready_by") == "selector-not-visible"]
    if hidden:
        return {"measured": "not visible on " + " and ".join(hidden), "status": "fail"}
    return {"measured": "visible", "status": "pass"}


def verdict(probe: dict, load: dict, budgets: dict) -> dict:
    """`{"checks": [{id, label, budget, measured, status}], "endpoints": [...], "summary": {...}}`."""
    checks: List[dict] = []

    def add(cid: str, label: str, budget, measured, status: str) -> None:
        checks.append({"id": cid, "label": label, "budget": budget, "measured": measured, "status": status})

    legs = load.get("legs", {})
    cold, warm = legs.get("android_cold", {}), legs.get("android_warm", {})
    warm_ok = warm.get("status") == "ok" and warm.get("cache") == "trusted"
    kb = lambda leg: None if leg.get("status") != "ok" else round(leg.get("bytes", 0) / 1024)  # noqa: E731
    cold_sampled = cold_ready(cold)
    for cid, label, value, limit in (
        ("cold.ready_ms", "Cold launch: ready (ms)", cold_sampled["value"], budgets["cold"]["ready_ms"]),
        ("cold.bytes_kb", "Cold launch: transferred (KB)", kb(cold), budgets["cold"]["bytes_kb"]),
        ("warm.ready_ms", "Warm relaunch: ready (ms)", warm.get("ready_ms") if warm_ok else None,
         budgets["warm"]["ready_ms"]),
        ("warm.data_ms", "Warm relaunch: boot data landed (ms)", warm.get("data_ms") if warm_ok else None,
         budgets["warm"]["data_ms"]),
        ("warm.bytes_kb", "Warm relaunch: transferred (KB)", kb(warm) if warm_ok else None, budgets["warm"]["bytes_kb"]),
    ):
        add(cid, label, limit, value, _status(value, limit))
        if cid == "cold.ready_ms":
            checks[-1].update(samples=cold_sampled["samples"], outliers=cold_sampled["outliers"])
        if cid.endswith(".bytes_kb") and checks[-1]["status"] == "fail":
            leg = cold if cid.startswith("cold.") else warm
            checks[-1]["top_responses"] = top_responses(leg)
            if cid == "warm.bytes_kb":
                checks[-1]["split"] = bytes_split(leg)

    selector = ready_selector_state(cold, warm)
    if selector:
        add("ready.selector", "Declared ready selector", "visible", selector["measured"], selector["status"])

    strategy = (probe.get("stamping") or {}).get("strategy", "none")
    if strategy != "none":
        add("cache.stamping", "Cache-busting stamps cover the import graph", "fleet-hash or graph-hash", strategy,
            {"fleet-hash": "pass", "graph-hash": "pass", "per-file": "fail"}.get(strategy, "unmeasured"))

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
            "iphone_cold": info, "budgets_version": budgets.get("version"),
            "ready_by": cold.get("ready_by") if cold.get("status") == "ok" else None}


def _fmt(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        return f"{v:.0f}" if v >= 10 else f"{v:.1f}"
    return str(v)


_MARK = {"pass": "✅", "fail": "⚠️", "unmeasured": "❔"}
_BASIS = {"fcp": "first contentful paint", "selector": "the declared ready selector being visible"}


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
    if v.get("ready_by") == "fcp":
        lines += ["", "No `ready_selector` is declared, so \"ready\" is first contentful paint, which can go green on a "
                      "painted shell before any card has data. Declare the first card that needs live data, one that "
                      "is visible on the landing view."]
    moved = (v.get("diff") or {}).get("ready_baseline_changed")
    if moved:
        lines += ["", f"\"Ready\" changed from {_BASIS[moved['from']]} to {_BASIS[moved['to']]} since the previous run, so the "
                      "ready times do not compare with it. A rise is the stricter definition, not a regression."]
    cold = next((c for c in v["checks"] if c["id"] == "cold.ready_ms"), {})
    if len(cold.get("samples", [])) > 1:
        shown = ", ".join(_fmt(x) for x in cold["samples"])
        flagged = f"; outlier {', '.join(_fmt(x) for x in cold['outliers'])} ms, not what the app does at rest" if cold.get("outliers") else ""
        lines += ["", f"Cold launch ready is the median of {len(cold['samples'])} fresh loads ({shown} ms){flagged}."]
    for c in v["checks"]:
        if c.get("top_responses"):
            lines += ["", f"Largest responses behind \"{c['label']}\":", ""] + [f"- {_top_line(r)}" for r in c["top_responses"]]
        if c.get("split"):
            lines += ["", _split_line("Warm relaunch", c["split"])]
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
             "ready_by": v.get("ready_by"),
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


def _ready_basis(ready_by: Optional[str]) -> Optional[str]:
    """`fcp` or `selector` (a selector that never showed is still a selector baseline); None when unknown."""
    return {"fcp": "fcp", "selector": "selector", "selector-not-visible": "selector"}.get(ready_by or "")


def diff(current: dict, previous: Optional[dict]) -> dict:
    """Check and endpoint ids that went fail->pass (`fixed`) or pass->fail (`regressed`) since `previous`.

    `ready_baseline_changed` is `{"from", "to"}` when what "ready" means moved between first contentful paint and
    a declared selector. Declaring the first selector took task-os's cold ready from 516 to 1967 ms with no code
    change (a painted shell vs data visible), so the two runs' ready times must not be read against each other.
    A previous run that did not record `ready_by` leaves it unknown, so nothing is claimed.
    """
    out = {"previous_run": previous.get("run_id") if previous else None, "fixed": [], "regressed": [],
           "ready_baseline_changed": None}
    if not previous:
        return out
    before, now = _ready_basis(previous.get("ready_by")), _ready_basis(current.get("ready_by"))
    if before and now and before != now:
        out["ready_baseline_changed"] = {"from": before, "to": now}
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
