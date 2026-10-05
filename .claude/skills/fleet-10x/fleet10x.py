"""Deterministic halves of /fleet-10x (fleet-config#1253).

The skill asks every active fleet repo "how could this be 10x better, even at
twice the effort?". The judgment (research, synthesis) is the model's; the
parts that must not drift between runs live here, so two dated runs can be
diffed:

- `select`   -- which repos are active: `.fleet.toml` repos with at least
                `--min-commits` commits on their default branch in the last
                `--days` days, or an explicit `--repos` list. The rest of the
                `.fleet.toml` fleet is the quiet set, one line each.
- `rundir`   -- the run's dated, machine-local folder under the hooks state
                dir (`fleet-10x/<YYYY-MM-DD>/`), never inside a repo, with a
                `run.json` start stamp for the report's wall time.
- `brief`    -- one rendered `research-brief.md` per active repo, so every
                researcher gets the same read-only brief, filled the same way.
- `validate` -- one researcher's JSON against the recommendation schema.
- `rank`     -- every valid researcher JSON in a run dir, ranked by impact
                over cost into `ranked.md` (the report's cross-fleet table),
                plus the run's wall time and Claude quota start -> now.
- `footer`   -- appends the run footer (selection rule, wall time, quota,
                sub-agent count and tokens) to the finished `report.md`.

stdlib + the `git` CLI only (via `skills/_lib/git_run`).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

_REPO = Path(__file__).resolve().parents[3]
_LIB = _REPO / "skills" / "_lib"
BRIEF_TEMPLATE = Path(__file__).resolve().parent / "research-brief.md"
sys.path.insert(0, str(_LIB))
import fleet_repo_scan  # noqa: E402
import fleet_toml  # noqa: E402
import git_run  # noqa: E402
import quota_snapshot  # noqa: E402
from hooks_state import state_dir  # noqa: E402
from utf8_stdio import ensure_utf8_stdio  # noqa: E402

GITHUB = "https://github.com/ferraroroberto"
DEFAULT_ROOT = Path("E:/automation")
DEFAULT_MIN_COMMITS = 20
DEFAULT_DAYS = 60
# A quiet repo gets one line in the report; .fleet.toml descriptions run to
# 1000+ characters (fleet-config#1250), so the first sentence, capped.
ONE_LINE_MAX = 160

IMPACT = {"H": 3, "M": 2, "L": 1}
COST = {"S": 1, "M": 2, "L": 3, "XL": 4}
MIN_RECS, MAX_RECS = 3, 7
REC_TEXT_FIELDS = ("title", "impact_reason", "what_changes", "why_10x", "first_step")


@dataclass(frozen=True)
class RepoFacts:
    name: str
    path: Path
    commits: int
    has_fleet_toml: bool
    description: str
    default_branch: str


@dataclass
class Selection:
    active: List[RepoFacts] = field(default_factory=list)
    quiet: List[RepoFacts] = field(default_factory=list)
    override: bool = False


# ---- selection ----

def select(fleet: Sequence[RepoFacts], min_commits: int, only: Optional[Sequence[str]] = None) -> Selection:
    """Split the `.fleet.toml` fleet into active and quiet repos.

    Active = commits >= `min_commits`, or exactly the `only` names when given
    (an unknown name raises). Active is ordered by commits descending, then
    name; quiet by name. A repo without `.fleet.toml` is never selected.
    """
    carded = [r for r in fleet if r.has_fleet_toml]
    if only:
        known = {r.name for r in carded}
        unknown = sorted(set(only) - known)
        if unknown:
            raise ValueError(f"not a .fleet.toml fleet repo: {', '.join(unknown)}")
        wanted = set(only)
        active = [r for r in carded if r.name in wanted]
    else:
        active = [r for r in carded if r.commits >= min_commits]
    picked = {r.name for r in active}
    return Selection(
        active=sorted(active, key=lambda r: (-r.commits, r.name)),
        quiet=sorted((r for r in carded if r.name not in picked), key=lambda r: r.name),
        override=bool(only),
    )


def one_line(description: str) -> str:
    """The first sentence of a description, capped at ONE_LINE_MAX."""
    text = " ".join((description or "").split())
    if not text:
        return "(no description)"
    m = re.match(r"(.+?[.!?])(\s|$)", text)
    first = m.group(1) if m else text
    return first if len(first) <= ONE_LINE_MAX else first[: ONE_LINE_MAX - 1].rstrip() + "…"


def count_commits(repo: Path, ref: str, since: dt.date) -> int:
    """Commits reachable from `ref` committed on or after `since`."""
    res = git_run.run_git(["-C", str(repo), "rev-list", "--count", f"--since={since.isoformat()}", ref])
    return int(res.stdout.strip()) if res.returncode == 0 and res.stdout.strip().isdigit() else 0


def gather(root: Path, days: int, today: dt.date) -> List[RepoFacts]:
    """Facts for every real fleet repo directly under `root` (worktrees excluded)."""
    since = today - dt.timedelta(days=days)
    out = []
    for d in fleet_repo_scan.iter_fleet_repos(root):
        ref = fleet_repo_scan.default_ref(d)
        card = fleet_toml.load(d)
        out.append(RepoFacts(
            name=d.name, path=d,
            commits=count_commits(d, ref, since) if ref else 0,
            has_fleet_toml=(d / fleet_toml.FILENAME).is_file(),
            description=str((card or {}).get("description", "")),
            default_branch=(ref or "origin/main").split("/", 1)[-1],
        ))
    return out


# ---- recommendation schema ----

def validate(doc: object) -> List[str]:
    """Schema errors in one researcher's JSON; empty when valid."""
    if not isinstance(doc, dict):
        return ["document is not a JSON object"]
    errors = []
    if not isinstance(doc.get("repo"), str) or not doc["repo"].strip():
        errors.append("repo: missing or blank")
    if not isinstance(doc.get("summary"), str):
        errors.append("summary: missing")
    recs = doc.get("recommendations")
    if not isinstance(recs, list) or not MIN_RECS <= len(recs) <= MAX_RECS:
        errors.append(f"recommendations: need {MIN_RECS}-{MAX_RECS}, got "
                      f"{len(recs) if isinstance(recs, list) else 'none'}")
        return errors
    for i, rec in enumerate(recs, 1):
        if not isinstance(rec, dict):
            errors.append(f"rec {i}: not an object")
            continue
        for key in REC_TEXT_FIELDS:
            if not isinstance(rec.get(key), str) or not rec[key].strip():
                errors.append(f"rec {i}: {key} missing or blank")
        if rec.get("impact") not in IMPACT:
            errors.append(f"rec {i}: impact must be one of {'/'.join(IMPACT)}")
        if rec.get("cost") not in COST:
            errors.append(f"rec {i}: cost must be one of {'/'.join(COST)}")
        ev = rec.get("evidence")
        if not isinstance(ev, list) or not ev or not all(isinstance(e, str) and e.strip() for e in ev):
            errors.append(f"rec {i}: evidence needs at least one file:line, issue or URL")
    return errors


# ---- ranking ----

def score(rec: dict) -> float:
    return IMPACT[rec["impact"]] / COST[rec["cost"]]


def rank(docs: Sequence[dict]) -> List[dict]:
    """Every recommendation across `docs`, highest impact-per-cost first.

    Ties break by impact, then repo name, then the researcher's own order.
    """
    flat = [{**rec, "repo": doc["repo"], "_pos": i}
            for doc in docs for i, rec in enumerate(doc["recommendations"])]
    return sorted(flat, key=lambda r: (-score(r), -IMPACT[r["impact"]], r["repo"], r["_pos"]))


_FILE_LINE = re.compile(r"^(?P<path>[\w./@+-]+?)(?::(?P<line>\d+)(?:-\d+)?)?$")
_ISSUE = re.compile(r"^(?P<repo>[\w.-]+)?#(?P<n>\d+)$")


def link_evidence(ev: str, repo: str, branch: str) -> str:
    """One evidence string as a markdown link: URL, `#N`, `repo#N` or `path[:line]`."""
    ev = ev.strip()
    if ev.startswith(("http://", "https://")):
        return f"[{ev}]({ev})"
    m = _ISSUE.match(ev)
    if m:
        target = m.group("repo") or repo
        return f"[{target}#{m.group('n')}]({GITHUB}/{target}/issues/{m.group('n')})"
    m = _FILE_LINE.match(ev)
    if m and ("/" in m.group("path") or "." in m.group("path")):
        anchor = f"#L{m.group('line')}" if m.group("line") else ""
        return f"[{ev}]({GITHUB}/{repo}/blob/{branch}/{m.group('path')}{anchor})"
    return ev


def _cell(text: str) -> str:
    return " ".join(text.split()).replace("|", "\\|")


def ranked_table(ranked: Sequence[dict], branches: Dict[str, str]) -> str:
    """The cross-fleet impact-vs-cost table, one row per recommendation."""
    rows = ["| # | Repo | Recommendation | Impact | Cost | Score | Evidence |",
            "|---|------|----------------|--------|------|-------|----------|"]
    for i, r in enumerate(ranked, 1):
        branch = branches.get(r["repo"], "main")
        ev = ", ".join(link_evidence(e, r["repo"], branch) for e in r["evidence"][:3])
        rows.append(f"| {i} | [{r['repo']}]({GITHUB}/{r['repo']}) | {_cell(r['title'])} | "
                    f"{r['impact']} | {r['cost']} | {score(r):.2f} | {ev} |")
    return "\n".join(rows)


# ---- run dir ----

def run_dir(date: dt.date) -> Path:
    return state_dir() / "fleet-10x" / date.isoformat()


def quota_reading() -> Optional[Dict[str, float]]:
    """Fresh Claude quota windows ({"five_hour": pct, "seven_day": pct}), or
    `None` when no available statusline observation exists. Never estimated:
    a missing reading is reported as unknown in the footer."""
    for source in quota_snapshot.read_snapshot()["sources"]:
        if source["producer"] != "claude-statusline":
            continue
        for obs in source["observations"]:
            if obs["state"] != "available":
                continue
            windows = {w["id"]: w["used_percentage"] for w in obs["windows"] if w["state"] == "available"}
            if windows:
                return windows
    return None


def _quota_line(start: object, end: Optional[Dict[str, float]]) -> str:
    if not isinstance(start, dict) or not end:
        return "unknown"
    return ",".join(f"{k}:{start[k]:g}%->{end[k]:g}%" for k in sorted(end) if k in start) or "unknown"


def _wall(started: str) -> str:
    secs = int((dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(started)).total_seconds())
    return f"{secs // 3600}h{(secs % 3600) // 60:02d}m"


def render_brief(template: str, repo: dict, run: Path, root: Path) -> str:
    """The research brief for one `selection.json` active entry."""
    return template.format(
        repo=repo["name"], path=repo["path"], branch=repo["branch"], commits=repo["commits"],
        out_file=(run / "repos" / f"{repo['name']}.json").as_posix(),
        helper=Path(__file__).resolve().as_posix(), python=Path(sys.executable).as_posix(),
        root=root.as_posix(), state=state_dir().as_posix(),
        global_claude=(Path.home() / ".claude" / "CLAUDE.md").as_posix(),
        system_map=(_REPO / "architecture" / "system-map.mmd").as_posix(),
    )


# ---- CLI ----

def _cmd_select(a: argparse.Namespace) -> int:
    today = dt.date.fromisoformat(a.today) if a.today else dt.date.today()
    fleet = gather(Path(a.root), a.days, today)
    try:
        sel = select(fleet, a.min_commits, [n.strip() for n in a.repos.split(",")] if a.repos else None)
    except ValueError as e:
        print(f"ERROR={e}")
        return 2
    for r in sel.active:
        print(f"ACTIVE={r.name}|commits={r.commits}|branch={r.default_branch}|path={r.path}")
    for r in sel.quiet:
        print(f"QUIET={r.name}|commits={r.commits}|{one_line(r.description)}")
    rule = "override" if sel.override else f">={a.min_commits} commits in {a.days}d to {today}"
    print(f"SELECTED={len(sel.active)}|QUIET_COUNT={len(sel.quiet)}|RULE={rule}")
    if a.run_dir:
        payload = {
            "rule": rule,
            "active": [{"name": r.name, "commits": r.commits, "branch": r.default_branch,
                        "path": str(r.path)} for r in sel.active],
            "quiet": [{"name": r.name, "commits": r.commits, "line": one_line(r.description)}
                      for r in sel.quiet],
        }
        (Path(a.run_dir) / "selection.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return 0


def _cmd_rundir(a: argparse.Namespace) -> int:
    d = run_dir(dt.date.fromisoformat(a.date) if a.date else dt.date.today())
    (d / "repos").mkdir(parents=True, exist_ok=True)
    stamp = d / "run.json"
    if not stamp.is_file():
        stamp.write_text(json.dumps({"started_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                                     "quota_start": quota_reading()}), encoding="utf-8")
    print(f"RUN_DIR={d}")
    return 0


def _cmd_brief(a: argparse.Namespace) -> int:
    run = Path(a.run_dir)
    sel = _load(run / "selection.json")
    if not isinstance(sel, dict):
        print(f"ERROR=no selection.json in {run}; run select --run-dir first")
        return 2
    template = BRIEF_TEMPLATE.read_text(encoding="utf-8")
    (run / "briefs").mkdir(exist_ok=True)
    for repo in sel["active"]:
        out = run / "briefs" / f"{repo['name']}.md"
        out.write_text(render_brief(template, repo, run, Path(a.root)), encoding="utf-8")
        print(f"BRIEF={repo['name']}|{out}")
    return 0


def _load(path: Path) -> object:
    """Parsed JSON, or `None` when the file is absent, unreadable or not JSON."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _cmd_validate(a: argparse.Namespace) -> int:
    path = Path(a.file)
    doc = _load(path)
    errors = validate(doc) if doc is not None else ["file is absent, unreadable or not JSON"]
    name = doc.get("repo") if isinstance(doc, dict) and doc.get("repo") else path.stem
    if errors:
        print(f"INVALID={name}")
        for e in errors:
            print(f"  - {e}")
        return 1
    print(f"VALID={name}|recs={len(doc['recommendations'])}")
    return 0


def footer_text(rule: str, wall: str, quota: str, agents: int, tokens: Optional[int]) -> str:
    """The report's closing section: how the run was selected and what it cost."""
    tok = f"{tokens:,}" if tokens is not None else "unknown"
    return ("\n\n---\n\n## Run\n\n"
            f"- Selection: {rule}\n"
            f"- Wall time: {wall}\n"
            f"- Claude quota used (statusline windows, start -> end): {quota}\n"
            f"- Sub-agents: {agents}, reported tokens: {tok}\n"
            "- Generated by `/fleet-10x` (fleet-config#1253). Machine-local; never committed.\n")


def _run_stats(run: Path) -> Tuple[str, str]:
    """`(wall time, quota line)` for a run dir, each `unknown` when unestablished."""
    started = _load(run / "run.json")
    wall = _wall(started["started_at"]) if isinstance(started, dict) else "unknown"
    quota = _quota_line(started.get("quota_start") if isinstance(started, dict) else None, quota_reading())
    return wall, quota


def _cmd_footer(a: argparse.Namespace) -> int:
    run = Path(a.run_dir)
    report = run / "report.md"
    if not report.is_file():
        print(f"ERROR=no report.md in {run}")
        return 2
    sel = _load(run / "selection.json")
    rule = sel.get("rule", "unknown") if isinstance(sel, dict) else "unknown"
    wall, quota = _run_stats(run)
    with report.open("a", encoding="utf-8") as fh:
        fh.write(footer_text(rule, wall, quota, a.agents, a.tokens))
    print(f"FOOTER=appended|WALL={wall}|QUOTA={quota}")
    return 0


def _cmd_rank(a: argparse.Namespace) -> int:
    d = Path(a.run_dir)
    docs, bad = [], []
    for p in sorted((d / "repos").glob("*.json")):
        doc = _load(p)
        if validate(doc):
            bad.append(p.stem)
        else:
            docs.append(doc)
    branches = {}
    sel = _load(d / "selection.json")
    if isinstance(sel, dict):
        branches = {r["name"]: r["branch"] for r in sel.get("active", [])}
    ranked = rank(docs)
    (d / "ranked.md").write_text(ranked_table(ranked, branches) + "\n", encoding="utf-8")
    wall, quota = _run_stats(d)
    print(f"RANKED={len(ranked)}|REPOS={len(docs)}|INVALID={','.join(bad) or 'none'}|WALL={wall}|QUOTA={quota}")
    print(f"TABLE={d / 'ranked.md'}")
    return 1 if bad else 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    ensure_utf8_stdio()
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("select", help="active vs quiet repos")
    s.add_argument("--root", default=str(DEFAULT_ROOT))
    s.add_argument("--min-commits", type=int, default=DEFAULT_MIN_COMMITS)
    s.add_argument("--days", type=int, default=DEFAULT_DAYS)
    s.add_argument("--today", help="YYYY-MM-DD; default today")
    s.add_argument("--repos", help="comma-separated override list")
    s.add_argument("--run-dir", help="also write selection.json here")
    s.set_defaults(fn=_cmd_select)
    r = sub.add_parser("rundir", help="create the dated run folder")
    r.add_argument("--date", help="YYYY-MM-DD; default today")
    r.set_defaults(fn=_cmd_rundir)
    b = sub.add_parser("brief", help="render one research brief per active repo")
    b.add_argument("run_dir")
    b.add_argument("--root", default=str(DEFAULT_ROOT))
    b.set_defaults(fn=_cmd_brief)
    v = sub.add_parser("validate", help="check one researcher JSON")
    v.add_argument("file")
    v.set_defaults(fn=_cmd_validate)
    k = sub.add_parser("rank", help="rank a run's researcher JSONs into ranked.md")
    k.add_argument("run_dir")
    k.set_defaults(fn=_cmd_rank)
    f = sub.add_parser("footer", help="append the run footer to report.md")
    f.add_argument("run_dir")
    f.add_argument("--agents", type=int, required=True)
    f.add_argument("--tokens", type=int, help="sum of sub-agent reported tokens, if known")
    f.set_defaults(fn=_cmd_footer)
    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
