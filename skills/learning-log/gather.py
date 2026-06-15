"""Gather + bucket + stat the fleet's GitHub work stream for /learning-log.

The deterministic half of the redesigned skill. It reads the fleet's merged PRs
and closed issues **per repo** (REST `gh pr list` / `gh issue list` — not the
rate-limited cross-repo search, so the full window since the last run is covered
with no cap), buckets each item by work type, computes exact productivity stats
(no LLM), and partitions the items into per-bucket files for the Sonnet
sub-agents to mine for insight.

It does NOT narrate — that's the orchestrator's scatter-gather over Sonnet
sub-agents (see SKILL.md). It reads **no source code**, only the GitHub work
stream. Stdlib + `gh` only.

Subcommands:

  gather   --since YYYY-MM-DD (optional; else ledger last-run-at; else 7 days)
           Writes <out-dir>/stats.md, <out-dir>/bucket-<slug>.md per non-empty
           bucket, and <out-dir>/prior-horizon.md; prints a parseable manifest
           (SINCE / OUT_DIR / STATS_FILE / TOTALS / one BUCKET= line per bucket)
           the orchestrator dispatches sub-agents from.

  assemble-ledger --repo OWNER/NAME --horizon-file F --discoveries-file F --out F
           Reads the prior kind=learning ledger, preserves its durable archive,
           prepends this run's dated discoveries, swaps in the new horizon, and
           stamps last-run-at — emitting the ledger body for audit_issue upsert.

Window anchors to the ledger's last-run-at so a missed run widens the next.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):  # pragma: no cover
    pass

HELPER = Path(__file__).resolve().parents[1] / "_lib" / "audit_issue.py"
ARCHIVE_HEADER = "## Decision / discovery archive"
HORIZON_HEADER = "## Horizon → next week"

# Canonical work-type buckets, in display order. PR titles are conventional-
# commit prefixed; issues carry type labels. Both map onto the same set so a
# bucket shows PRs and issues together.
BUCKETS = [
    "Features & enhancements",
    "Bug fixes",
    "Chores & maintenance",
    "Documentation",
    "Refactors",
    "Tooling, CI & perf",
    "Other",
]
_PREFIX_BUCKET = {
    "feat": "Features & enhancements", "feature": "Features & enhancements",
    "fix": "Bug fixes", "bug": "Bug fixes", "hotfix": "Bug fixes",
    "chore": "Chores & maintenance", "build": "Chores & maintenance", "deps": "Chores & maintenance",
    "docs": "Documentation", "doc": "Documentation",
    "refactor": "Refactors",
    "perf": "Tooling, CI & perf", "test": "Tooling, CI & perf",
    "ci": "Tooling, CI & perf", "style": "Tooling, CI & perf",
}
_LABEL_BUCKET = {
    "bug": "Bug fixes", "enhancement": "Features & enhancements",
    "chore": "Chores & maintenance", "documentation": "Documentation",
    "claude-md-drift": "Chores & maintenance", "stale": "Chores & maintenance",
    "maintainability": "Refactors", "duplication": "Refactors",
}


# ---- pure helpers (unit-tested without gh) --------------------------------

def parse_last_run(body: str) -> str | None:
    m = re.search(r"last-run-at:\s*(\d{4}-\d{2}-\d{2})", body or "")
    return m.group(1) if m else None


def resolve_since(arg: str | None, prior_body: str, today: _dt.date) -> tuple[str, str]:
    """(since, source) — explicit arg → ledger last-run-at → trailing 7 days."""
    if arg:
        return arg, "arg"
    last = parse_last_run(prior_body)
    if last:
        return last, "ledger"
    return (today - _dt.timedelta(days=7)).isoformat(), "default"


def slice_section(text: str, header: str) -> str:
    idx = (text or "").find(header)
    if idx == -1:
        return ""
    rest = text[idx + len(header):]
    end = rest.find("\n## ")
    return (rest if end == -1 else rest[:end]).strip()


def _bullet_lines(section: str) -> list[str]:
    return [ln.strip() for ln in (section or "").splitlines() if ln.strip().startswith(("-", "*"))]


def dated_discovery_bullets(discoveries: str, today: str, cap: int = 12) -> list[str]:
    out: list[str] = []
    for ln in _bullet_lines(discoveries)[:cap]:
        content = ln.lstrip("-*").strip()
        if content:
            out.append(f"- {today}: {content}")
    return out


def extract_archive_bullets(prior_body: str) -> list[str]:
    return _bullet_lines(slice_section(prior_body, ARCHIVE_HEADER))


def build_ledger_body(prior_body: str, today: str, horizon: str, discoveries: str) -> str:
    horizon_md = "\n".join(_bullet_lines(horizon)) or "- [ ] (none captured this run)"
    archive = dated_discovery_bullets(discoveries, today) + extract_archive_bullets(prior_body)
    archive_md = "\n".join(archive) if archive else "- (nothing archived yet)"
    return (
        "<!-- learning-log-state -->\n"
        f"last-run-at: {today}\n\n"
        "# Learning log — fleet\n\n"
        "The fleet's weekly learning journal — what shipped, what we learned, and "
        "what's next — from merged PRs + closed issues, mined per work-type bucket by "
        "`/learning-log`. The week-by-week narrative + productivity tables live in this "
        "issue's comments; this body is the durable archive + the live horizon. One "
        "canonical issue (durable knowledge lands here, not in `docs/`).\n\n"
        f"## Horizon → next week (set {today})\n{horizon_md}\n\n"
        f"{ARCHIVE_HEADER}\n{archive_md}\n"
    )


def _type_prefix(title: str) -> str:
    m = re.match(r"\s*([a-z]+)(?:\([^)]*\))?!?:", title or "", re.I)
    return m.group(1).lower() if m else ""


def pr_bucket(title: str) -> str:
    return _PREFIX_BUCKET.get(_type_prefix(title), "Other")


def issue_bucket(labels: list[str]) -> str:
    for lb in labels:
        if lb in _LABEL_BUCKET:
            return _LABEL_BUCKET[lb]
    return "Other"


def _slug(bucket: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", bucket.lower()).strip("-")


def compute_stats(prs: list[dict], issues: list[dict]) -> dict:
    """Per-repo and per-bucket counts + LOC, plus grand totals. Pure."""
    repos: dict[str, dict] = {}
    buckets: dict[str, dict] = {b: {"prs": 0, "issues": 0, "add": 0, "del": 0} for b in BUCKETS}

    def repo_row(name: str) -> dict:
        return repos.setdefault(name, {"prs": 0, "issues": 0, "add": 0, "del": 0})

    for p in prs:
        r = repo_row(p["repo"]); b = buckets[p["bucket"]]
        add, dele = int(p.get("additions") or 0), int(p.get("deletions") or 0)
        r["prs"] += 1; r["add"] += add; r["del"] += dele
        b["prs"] += 1; b["add"] += add; b["del"] += dele
    for i in issues:
        repo_row(i["repo"])["issues"] += 1
        buckets[i["bucket"]]["issues"] += 1

    total = {"prs": len(prs), "issues": len(issues),
             "add": sum(int(p.get("additions") or 0) for p in prs),
             "del": sum(int(p.get("deletions") or 0) for p in prs)}
    return {"repos": repos, "buckets": buckets, "total": total}


def _fmt_loc(n: int) -> str:
    return f"{n/1000:.1f}k" if n >= 1000 else str(n)


def render_stats(stats: dict, since: str, today: str) -> str:
    total = stats["total"]
    lines = [
        f"## \U0001f4ca Productivity — {since} → {today}",
        "",
        f"**Grand total:** {total['prs']} merged PRs · {total['issues']} closed issues "
        f"· +{_fmt_loc(total['add'])} / −{_fmt_loc(total['del'])} LOC across "
        f"{sum(1 for r in stats['repos'].values() if r['prs'] or r['issues'])} active repos.",
        "",
        "### By project (most active first)",
        "",
        "| Project | PRs | Issues | +LOC | −LOC |",
        "|---|--:|--:|--:|--:|",
        f"| **TOTAL** | **{total['prs']}** | **{total['issues']}** | **+{_fmt_loc(total['add'])}** | **−{_fmt_loc(total['del'])}** |",
    ]
    for name, r in sorted(stats["repos"].items(), key=lambda kv: (-kv[1]["prs"], -kv[1]["issues"], kv[0])):
        if not (r["prs"] or r["issues"]):
            continue
        lines.append(f"| {name} | {r['prs']} | {r['issues']} | +{_fmt_loc(r['add'])} | −{_fmt_loc(r['del'])} |")

    lines += ["", "### By work type", "", "| Bucket | PRs | Issues | +LOC | −LOC |", "|---|--:|--:|--:|--:|"]
    for b in BUCKETS:
        d = stats["buckets"][b]
        if not (d["prs"] or d["issues"]):
            continue
        lines.append(f"| {b} | {d['prs']} | {d['issues']} | +{_fmt_loc(d['add'])} | −{_fmt_loc(d['del'])} |")
    return "\n".join(lines) + "\n"


# ---- gh plumbing ----------------------------------------------------------

def _gh_json(args: list[str]) -> list | dict:
    try:
        proc = subprocess.run(["gh", *args], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"gh {' '.join(args[:3])}… failed: {exc}", file=sys.stderr)
        return []
    if proc.returncode != 0:
        print(f"gh {' '.join(args[:3])}… exit {proc.returncode}: {proc.stderr.strip()[:160]}", file=sys.stderr)
        return []
    try:
        return json.loads(proc.stdout or "[]")
    except ValueError:
        return []


def list_repos(owner: str) -> list[str]:
    data = _gh_json(["repo", "list", owner, "--no-archived", "--source", "--limit", "200", "--json", "name"])
    return [r["name"] for r in data] if isinstance(data, list) else []


def gather_repo(owner: str, repo: str, since: str) -> tuple[list[dict], list[dict]]:
    full = f"{owner}/{repo}"
    prs_raw = _gh_json(["pr", "list", "--repo", full, "--state", "merged", "--limit", "400",
                        "--json", "number,title,additions,deletions,labels,mergedAt,url"])
    issues_raw = _gh_json(["issue", "list", "--repo", full, "--state", "closed", "--limit", "400",
                           "--json", "number,title,labels,closedAt,url"])
    prs = []
    for p in (prs_raw if isinstance(prs_raw, list) else []):
        if (p.get("mergedAt") or "")[:10] < since:
            continue
        prs.append({"repo": repo, "number": p["number"], "title": p.get("title", ""),
                    "additions": p.get("additions"), "deletions": p.get("deletions"),
                    "url": p.get("url"), "bucket": pr_bucket(p.get("title", ""))})
    issues = []
    for i in (issues_raw if isinstance(issues_raw, list) else []):
        if (i.get("closedAt") or "")[:10] < since:
            continue
        labels = [l.get("name", "") for l in (i.get("labels") or [])]
        issues.append({"repo": repo, "number": i["number"], "title": i.get("title", ""),
                       "labels": labels, "url": i.get("url"), "bucket": issue_bucket(labels)})
    return prs, issues


def write_bucket_files(out_dir: Path, prs: list[dict], issues: list[dict]) -> list[tuple[str, str, int, int, Path]]:
    """One file per non-empty bucket; returns (bucket, slug, n_prs, n_issues, path)."""
    manifest = []
    for bucket in BUCKETS:
        bp = [p for p in prs if p["bucket"] == bucket]
        bi = [i for i in issues if i["bucket"] == bucket]
        if not (bp or bi):
            continue
        slug = _slug(bucket)
        path = out_dir / f"bucket-{slug}.md"
        lines = [f"# Bucket: {bucket}", f"_{len(bp)} merged PRs, {len(bi)} closed issues_", "", "## Merged PRs"]
        for p in sorted(bp, key=lambda x: -((x.get("additions") or 0) + (x.get("deletions") or 0))):
            lines.append(f"- [{p['repo']}#{p['number']}] {p['title']} (+{p.get('additions') or 0}/-{p.get('deletions') or 0})")
        lines += ["", "## Closed issues"]
        for i in bi:
            tag = f" [{','.join(i['labels'])}]" if i["labels"] else ""
            lines.append(f"- [{i['repo']}#{i['number']}] {i['title']}{tag}")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        manifest.append((bucket, slug, len(bp), len(bi), path))
    return manifest


def read_ledger_body(repo: str) -> str:
    try:
        proc = subprocess.run([sys.executable, str(HELPER), "get", "--repo", repo, "--kind", "learning"],
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        if proc.returncode == 0:
            return json.loads(proc.stdout or "{}").get("body") or ""
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        print(f"ledger get failed: {exc}", file=sys.stderr)
    return ""


def cmd_gather(args) -> int:
    today = _dt.date.today().isoformat()
    prior_body = read_ledger_body(args.repo)
    since, source = resolve_since(args.since, prior_body, _dt.date.today())

    out_dir = Path(args.out_dir or (Path(__import__("tempfile").gettempdir()) / f"learning-log-{today}"))
    out_dir.mkdir(parents=True, exist_ok=True)

    repos = list_repos(args.owner)
    all_prs: list[dict] = []
    all_issues: list[dict] = []
    for repo in repos:
        prs, issues = gather_repo(args.owner, repo, since)
        all_prs += prs
        all_issues += issues
        if prs or issues:
            print(f"  {repo}: {len(prs)} PRs, {len(issues)} issues", file=sys.stderr)

    stats = compute_stats(all_prs, all_issues)
    stats_md = render_stats(stats, since, today)
    stats_file = out_dir / "stats.md"
    stats_file.write_text(stats_md, encoding="utf-8")

    (out_dir / "prior-horizon.md").write_text(
        slice_section(prior_body, HORIZON_HEADER) or "(none — first run, no prior horizon to grade)",
        encoding="utf-8")

    manifest = write_bucket_files(out_dir, all_prs, all_issues)

    t = stats["total"]
    print(f"SINCE={since}")
    print(f"SINCE_SOURCE={source}")
    print(f"OUT_DIR={out_dir}")
    print(f"STATS_FILE={stats_file}")
    print(f"PRIOR_HORIZON_FILE={out_dir / 'prior-horizon.md'}")
    print(f"TOTALS=PRs={t['prs']} issues={t['issues']} add={t['add']} del={t['del']} repos={len(repos)}")
    for bucket, slug, npr, nis, path in manifest:
        print(f"BUCKET={slug}|{bucket}|prs={npr}|issues={nis}|file={path}")
    print()
    print(stats_md)
    return 0


def cmd_assemble_ledger(args) -> int:
    today = _dt.date.today().isoformat()
    prior_body = read_ledger_body(args.repo)
    horizon = Path(args.horizon_file).read_text(encoding="utf-8") if args.horizon_file else ""
    discoveries = Path(args.discoveries_file).read_text(encoding="utf-8") if args.discoveries_file else ""
    body = build_ledger_body(prior_body, today, horizon, discoveries)
    Path(args.out).write_text(body, encoding="utf-8")
    print(args.out)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Gather + bucket + stat the fleet work stream for /learning-log.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("gather")
    g.add_argument("--since")
    g.add_argument("--owner", default="ferraroroberto")
    g.add_argument("--repo", default="ferraroroberto/claude-config")
    g.add_argument("--out-dir", dest="out_dir", default=None)

    a = sub.add_parser("assemble-ledger")
    a.add_argument("--repo", default="ferraroroberto/claude-config")
    a.add_argument("--horizon-file", dest="horizon_file")
    a.add_argument("--discoveries-file", dest="discoveries_file")
    a.add_argument("--out", required=True)

    args = ap.parse_args(argv)
    if args.cmd == "gather":
        return cmd_gather(args)
    if args.cmd == "assemble-ledger":
        return cmd_assemble_ledger(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
