"""Run digest markdown and its one-line chat ping.

Part of the `prompt_audit` package (fleet-config#931); see `__init__.py`.
"""

from __future__ import annotations

from typing import Dict, Tuple

from .common import COMMENT_CHAR_CAP, DIGEST_FINDINGS_CAP, STAMP_PREFIX, VERDICTS, clean
from .dedup import dedup, norm_line


# ---- digest ---------------------------------------------------------------------

def _kv(line: str) -> dict:
    head, _, rest = line.partition("|")
    key, _, value = head.partition("=")
    out = {key: value}
    for part in rest.split("|"):
        k, sep, v = part.partition("=")
        if sep:
            out[k] = v
    return out


def partition_run(run: dict) -> dict:
    """Split a run into what was judged, skipped and not established.

    `run`: {date, dry_run, sources: [VERDICT= lines], update_issue, scan_ran,
    plan: [PLAN= lines], judgments: {path: [finding, ...] | null}, rubric}.
    A planned-scan file with no judgment (absent or null) is an unmeasured file; a
    judged file carrying any per-rule `unmeasured` verdict is only partly
    established. Only a file judged with no unmeasured rule is `recorded` (its
    PLAN sha goes into the ledger) — anything less is rescanned next run rather
    than skipped as if it had been fully assessed.
    """
    plan = [_kv(l) for l in run.get("plan", [])]
    sha_of = {p["PLAN"]: p.get("sha", "") for p in plan}
    scan = [p["PLAN"] for p in plan if p.get("action") == "scan"]
    unreadable = [p["PLAN"] for p in plan if p.get("action") == "unmeasured"]
    judgments = run.get("judgments") or {}
    scan_ran = bool(run.get("scan_ran"))
    judged = [k for k in scan if judgments.get(k) is not None] if scan_ran else []
    unmeasured_rules = [dict(f, path=k) for k in judged for f in judgments[k] if f.get("verdict") == "unmeasured"]
    partly = {f["path"] for f in unmeasured_rules}
    return {
        "plan": plan,
        "skip": [p["PLAN"] for p in plan if p.get("action") == "skip"],
        "judged": judged,
        "unmeasured": (unreadable + [k for k in scan if judgments.get(k) is None]) if scan_ran else [],
        "unmeasured_rules": unmeasured_rules,
        "findings": [dict(f, path=k) for k in judged for f in judgments[k]
                     if f.get("verdict") in ("violation", "consider")],
        "recorded": {k: sha_of[k] for k in judged if k not in partly and sha_of.get(k) not in ("", "unmeasured")},
    }


def summarize_run(run: dict) -> dict:
    """The verdicts both the digest and the ping report, computed once from `run`."""
    srcs = [_kv(l) for l in run.get("sources", [])]
    by_verdict = {v: [s for s in srcs if s.get("VERDICT") == v] for v in VERDICTS}
    stale = by_verdict["changed"] + by_verdict["new-guide"]
    parts = partition_run(run)
    return {
        "srcs": srcs, "by_verdict": by_verdict, "stale": stale, "parts": parts,
        "guides": "changed" if stale else ("not-checked" if by_verdict["not-checked"] or not srcs else "unchanged"),
        "status": "partial" if parts["unmeasured"] or parts["unmeasured_rules"] else "complete",
        "violation": sum(1 for f in parts["findings"] if f["verdict"] == "violation"),
        "consider": sum(1 for f in parts["findings"] if f["verdict"] == "consider"),
    }


def render_ping(run: dict, comment_url: str) -> str:
    """One pure-ASCII chat line for a delivered run. Pure: every value comes from `run`.

    ASCII separators only: a non-ASCII character in a Windows command line reaches
    the chat as `??` (fleet-config#507).
    """
    s = summarize_run(run)
    parts = s["parts"]
    head = [f"prompt-audit {run.get('date') or 'unknown'}", f"status={s['status']}", f"guides={s['guides']}"]
    if run.get("scan_ran"):
        body = [f"scanned {len(parts['judged'])}, skipped {len(parts['skip'])}, unmeasured {len(parts['unmeasured'])}",
                f"{s['violation']} violation, {s['consider']} consider"]
    else:
        body = [f"scan not run, rule-set update issue {run.get('update_issue') or 'not filed'}"]
    line = " - ".join(head + body + [f"ledger {comment_url}"])
    return line.encode("ascii", "replace").decode("ascii")


def render_digest(run: dict, rules: Dict[str, dict], master_text: str = "", lite_text: str = "") -> Tuple[str, str]:
    """(markdown, status). Pure: every count comes from `run` (see `partition_run`), nothing is inferred.

    Unmeasured files and unmeasured rules are listed as such and make the run
    `partial` — never folded into compliant; skipped files are listed as skipped.
    """
    summary = summarize_run(run)
    srcs, by_verdict, stale = summary["srcs"], summary["by_verdict"], summary["stale"]
    guides, status, parts = summary["guides"], summary["status"], summary["parts"]
    plan, skip, judged = parts["plan"], parts["skip"], parts["judged"]
    unmeasured, unmeasured_rules, findings = parts["unmeasured"], parts["unmeasured_rules"], parts["findings"]
    scan_ran = bool(run.get("scan_ran"))
    scan = "dry-run" if run.get("dry_run") else ("posted" if scan_ran else "not-run")
    # Machine-readable, ASCII, near the top so the comment-size cap never cuts it:
    # the scheduled job's delivery_check.py reads it (fleet-config#834).
    stamp = (f"<!-- {STAMP_PREFIX} run={run.get('date') or 'unknown'} status={status} scan={scan} "
             f"update-issue={run.get('update_issue') or 'none'} -->")

    out = [f"## prompt-audit digest — {run.get('date', '')}", stamp, "",
           f"`status={status}` · `guides={guides}` · `rubric={str(run.get('rubric', ''))[:12]}`"
           + (" · **dry run — nothing written**" if run.get("dry_run") else ""), ""]
    out.append(f"**Guides:** {len(srcs)} sources — " + ", ".join(
        f"{v} {len(by_verdict[v])}" for v in VERDICTS))
    for s in stale + by_verdict["not-checked"]:
        out.append(f"- `{s.get('id')}` **{s.get('VERDICT')}** — {s.get('reason', '')}")
    out.append("")
    if not scan_ran:
        ref = run.get("update_issue") or "(update issue not filed)"
        out += [f"**Scan:** not run — rule-set stale, see {ref}", ""]
        return "\n".join(out) + "\n", status

    out.append(f"**Scan:** {len(plan)} files — scanned {len(judged)}, skipped {len(skip)} (unchanged), "
               f"unmeasured {len(unmeasured)}, rule verdicts not established {len(unmeasured_rules)}")
    tally: Dict[str, Dict[str, int]] = {}
    for f in findings:
        tally.setdefault(f["rule"], {"violation": 0, "consider": 0})[f["verdict"]] += 1
    carried = (" — skipped files keep the findings of the digest that last scanned them, "
               "so this is not a fleet total") if skip else ""
    out += ["", f"**Findings in scanned files:** {summary['violation']} violation, "
                f"{summary['consider']} consider, across "
                f"{len({f['path'] for f in findings})} files{carried}", ""]
    if tally:
        out += ["| rule | violation | consider |", "|---|---|---|"]
        out += [f"| {r} {rules.get(r, {}).get('title', '')} | {t['violation']} | {t['consider']} |"
                for r, t in sorted(tally.items())]
        out.append("")

    annotated = dedup(findings, master_text, lite_text)
    shared: Dict[Tuple[str, str], dict] = {}
    local = []
    for f in annotated:
        if f["scope"] == "shared-with-scaffold":
            key = (f["rule"], norm_line(f.get("text", "")))
            # One entry per shared line; it carries the strongest verdict any copy received.
            if key not in shared or f["verdict"] == "violation":
                shared[key] = f
        else:
            local.append(f)
    if shared:
        out += ["### Shared with the scaffolding master (file once there)", ""]
        for f in shared.values():
            out.append(f"- **{f['rule']}** {f['verdict']} — `{clean(f.get('text', ''))[:100]}` "
                       f"— propagate to: {', '.join(f.get('propagate_to', []))}")
        out.append("")
    if local:
        out += ["### Findings", ""]
        for f in local[:DIGEST_FINDINGS_CAP]:
            where = f"{f['path']}:{f['line']}" if f.get("line") else f["path"]
            extra = f" — propagate to: {', '.join(f['propagate_to'])}" if f.get("propagate_to") else ""
            out.append(f"- `{where}` **{f['rule']}** {f['verdict']} — {clean(f.get('note') or f.get('text', ''))[:160]}{extra}")
        if len(local) > DIGEST_FINDINGS_CAP:
            out.append(f"- … {len(local) - DIGEST_FINDINGS_CAP} more not listed (cap {DIGEST_FINDINGS_CAP})")
        out.append("")
    if unmeasured:
        out += ["### Unmeasured (not established — not compliant)", ""]
        out += [f"- `{k}`" for k in unmeasured] + [""]
    if unmeasured_rules:
        out += ["### Rules not established (file judged in part — rescanned next run)", ""]
        out += [f"- `{f['path']}` **{f['rule']}** — {clean(f.get('note', ''))[:160]}" for f in unmeasured_rules] + [""]
    if skip:
        out += ["<details><summary>Skipped — unchanged since the last scan under this rubric "
                f"({len(skip)})</summary>", ""]
        out += [f"- `{k}`" for k in skip] + ["", "</details>", ""]
    body = "\n".join(out) + "\n"
    if len(body) > COMMENT_CHAR_CAP:
        body = body[:COMMENT_CHAR_CAP] + "\n\n… digest truncated at the comment size cap.\n"
    return body, status
