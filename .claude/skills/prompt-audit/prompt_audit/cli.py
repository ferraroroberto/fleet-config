"""argparse and the `cmd_*` subcommand handlers.

Part of the `prompt_audit` package (fleet-config#931); see `__init__.py`.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import datetime as dt
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .common import KIND, LEDGER_REPO, LITE_GLOBAL, LITE_REPO, MASTER_REPO, REPO_ROOT, RULES_MD, SOURCES_TOML, TITLE, VERDICTS, clean, ensure_utf8_stdio, fleet_repos, git_run, load_toml, rules_rubric
from .sources import diff_source
from .evals import fold as fold_evals, latest_two
from .leads import LeadsError, load_leads, mark as mark_lead, resolve as resolve_leads, suggestions_comment
from .coverage import (cache_page, coverage, coverage_comment, known_items, page_sections,
                       page_text, section_text)
from .inventory import Entry, _read, inventory, kind_of, sections
from .rules import parse_rules
from .lint import hit_detail, hits_line, lint_entry, mcp_vocabulary
from .dedup import dedup
from .state import load_state, save_state, source_due
from .ledger import _audit_issue, merge_ledger, plan_scan, read_ledger_issue, render_ledger_body
from .digest import partition_run, provisional_rules, render_digest, render_ping
from .drift import DRIFT_KIND, DRIFT_LABEL, DRIFT_LABEL_COLOR, DRIFT_LABEL_DESC, DRIFT_TITLE, OWNER, drift_items, merge_drift


# ---- CLI ------------------------------------------------------------------------

def cmd_drift(args: argparse.Namespace, cfg: dict) -> int:
    run = json.loads(Path(args.run).read_text(encoding="utf-8"))
    if not run.get("scan_ran"):
        print("DRIFT=none|reason=scan not run — nothing to file")
        return 0
    rules = parse_rules(RULES_MD.read_text(encoding="utf-8"))
    repos = _repos(args)
    master, lite = _master_and_lite(repos)
    parts = partition_run(run)
    all_findings = [dict(f, path=k) for k in parts["judged"] for f in (run.get("judgments") or {})[k]
                    if f.get("verdict") in ("violation", "consider")]
    # R-44 (#1131): eval regressions the fold-in found, filed like any judged finding.
    all_findings += [dict(f) for f in (run.get("evals") or {}).get("findings", [])
                     if f.get("verdict") in ("violation", "consider")]
    entries = {e.key: e.text for e in inventory(repos, cfg.get("audiences", {}))}
    provisional = provisional_rules(rules, cfg.get("sources", {}), run.get("sources", []))
    per_repo = drift_items(all_findings, rules, entries, master, lite, provisional)
    judged = set(parts["judged"])
    unmeasured = {(f["path"], f["rule"]) for f in parts["unmeasured_rules"]}
    date = args.date or dt.date.today().isoformat()
    rubric = run.get("rubric") or rules_rubric()
    targets = sorted(set(per_repo) | {k.split("/", 1)[0] for k in judged})
    failed = 0
    for repo in targets:
        slug = f"{OWNER}/{repo}"
        try:
            got = json.loads(_audit_issue("get", "--repo", slug, "--kind", DRIFT_KIND))
        except Exception as exc:  # one repo degrades, never the run
            print(f"DRIFT={repo}|error={clean(str(exc))[:200]}")
            failed += 1
            continue
        existing = got.get("body") or ""
        fresh = per_repo.get(repo, [])
        if not fresh and not existing:
            continue
        body, c = merge_drift(existing, fresh, judged, unmeasured, rules, date, rubric)
        if not (c["new"] or c["matched"] or c["not_resurfaced"]):
            continue  # nothing this run could say about the repo: leave the issue untouched
        line = (f"|tier={c['tier']}|open={c['open']}|new={c['new']}|matched={c['matched']}"
                f"|kept={c['kept']}|not_resurfaced={c['not_resurfaced']}")
        if args.dry_run:
            print(f"DRIFT={repo}|issue={'#' + str(got['number']) if got.get('number') else 'new'}{line}|dry-run")
            print(body)
            continue
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8", newline="") as fh:
            fh.write(body)
            tmp = fh.name
        try:
            git_run.run_gh(["label", "create", DRIFT_LABEL, "--repo", slug, "--color", DRIFT_LABEL_COLOR,
                            "--description", DRIFT_LABEL_DESC], timeout=60)  # exists already -> harmless failure
            url = _audit_issue("upsert", "--repo", slug, "--kind", DRIFT_KIND, "--label", DRIFT_LABEL,
                               "--title", DRIFT_TITLE, "--body-file", tmp).strip()
            print(f"DRIFT={repo}|issue={url}{line}")
        except Exception as exc:
            print(f"DRIFT={repo}|error={clean(str(exc))[:200]}")
            failed += 1
        finally:
            Path(tmp).unlink(missing_ok=True)
    print(f"DRIFT_RUN=repos={len(targets)}|failed={failed}", file=sys.stderr)
    return 1 if failed else 0



def _repos(args: argparse.Namespace) -> Dict[str, Path]:
    return fleet_repos(Path(args.projects_toml)) if getattr(args, "projects_toml", None) else fleet_repos()


def _master_and_lite(repos: Dict[str, Path]) -> Tuple[str, str]:
    root = REPO_ROOT.parent
    master = repos.get(MASTER_REPO, root / MASTER_REPO) / "CLAUDE.md"
    lite = repos.get(LITE_REPO, root / LITE_REPO) / LITE_GLOBAL
    return ((_read(master) or b"").decode("utf-8", "replace"),
            (_read(lite) or b"").decode("utf-8", "replace"))


def cmd_sources(cfg: dict) -> int:
    for sid, s in cfg.get("sources", {}).items():
        print(f"SOURCE={sid}|url={s['url']}|vendor={s.get('vendor', '')}|role={s.get('role', '')}"
              f"|marker_kind={s.get('marker_kind', 'none')}|baseline={s.get('baseline_sha', '')}"
              f"|marker={clean(str(s.get('baseline_marker') or 'none'))}")
    print(f"SOURCES={len(cfg.get('sources', {}))}")
    return 0


UPDATE_TITLE = "prompt-audit: vendor guidance changed — update rule-set"


def _update_issue() -> Tuple[Optional[int], str]:
    """(number, body + every comment) of the open rule-set update issue, or (None, "").

    Raises on a gh failure: a coverage post must never mistake "could not read" for "nothing posted yet".
    """
    res = git_run.run_gh(["issue", "list", "--repo", LEDGER_REPO, "--state", "open", "--search",
                          "prompt-audit: vendor guidance changed in:title", "--json", "number,title"], timeout=60)
    if res.returncode != 0:
        raise RuntimeError(f"gh issue list failed: {(res.stderr or res.stdout).strip()[:200]}")
    hit = next((i for i in json.loads(res.stdout or "[]") if i.get("title") == UPDATE_TITLE), None)
    if hit is None:
        return None, ""
    view = git_run.run_gh(["issue", "view", str(hit["number"]), "--repo", LEDGER_REPO, "--json", "body,comments"],
                          timeout=60)
    if view.returncode != 0:
        raise RuntimeError(f"gh issue view failed: {(view.stderr or view.stdout).strip()[:200]}")
    data = json.loads(view.stdout)
    return hit["number"], "\n".join([data.get("body") or ""] + [c.get("body") or "" for c in data.get("comments", [])])


def cmd_coverage(args: argparse.Namespace, cfg: dict) -> int:
    """Every tracked page's uncovered sections as JSON; `--extract` prints the new ones' text, `--post` files them."""
    scratch = Path(args.scratch) if args.scratch else None
    rules_text = RULES_MD.read_text(encoding="utf-8")
    known, number = "", None
    if args.post or not args.offline:
        try:
            number, known = _update_issue()
        except Exception as exc:
            if args.post:
                print(f"❌ coverage: cannot read the update issue, nothing posted — {exc}", file=sys.stderr)
                return 1
            print(f"⚠️ coverage: update issue unreadable, every uncovered section reported as new — {exc}",
                  file=sys.stderr)
    results = [coverage(sid, src, page_text(sid, scratch), rules_text, known_items(known))
               for sid, src in cfg.get("sources", {}).items()]
    if args.extract:
        for r in results:
            text = page_text(r["id"], scratch) or ""
            for sec in r["new"]:
                print(f"<section page=\"{r['id']}\" title=\"{sec}\">\n{section_text(text, sec)}</section>\n")
        return 0
    if args.post:
        drafts = Path(args.drafts).read_text(encoding="utf-8") if args.drafts else ""
        body = coverage_comment(results, drafts)
        if body is None:
            print("COVERAGE_POST=none|reason=no new uncovered sections")
            return 0
        if args.dry_run:
            print(body)
            print("COVERAGE_POST=dry-run")
            return 0
        return _post_update(number, body, "COVERAGE_POST")
    print(json.dumps(results, indent=2))
    return 0


def _post_update(number: Optional[int], body: str, key: str) -> int:
    """Comment `body` on the open update issue, or open it with `body` when none is open."""
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as fh:
        fh.write(body)
        tmp = fh.name
    try:
        if number is None:
            res = git_run.run_gh(["issue", "create", "--repo", LEDGER_REPO, "--title", UPDATE_TITLE,
                                  "--label", "enhancement", "--assignee", "@me", "--body-file", tmp], timeout=120)
        else:
            res = git_run.run_gh(["issue", "comment", str(number), "--repo", LEDGER_REPO, "--body-file", tmp],
                                 timeout=120)
    finally:
        Path(tmp).unlink(missing_ok=True)
    if res.returncode != 0:
        print(f"❌ {key} failed: {(res.stderr or res.stdout).strip()}", file=sys.stderr)
        return 1
    print(f"{key}={(res.stdout or '').strip()}")
    return 0


def cmd_evals(args: argparse.Namespace) -> int:
    """The newest skill-eval aggregate folded against the one before it, as JSON for `run.json`."""
    newest, previous = latest_two(Path(args.dir) if args.dir else None)
    today = dt.date.fromisoformat(args.date) if args.date else None
    folded = fold_evals(newest, previous, today)
    print(json.dumps(folded, indent=2))
    print(f"EVALS=status={folded['status']}|findings={len(folded['findings'])}", file=sys.stderr)
    return 0


def cmd_leads(args: argparse.Namespace, cfg: dict) -> int:
    """`leads.toml` with each claim's standing as JSON; `mark` records a trace result, `--post` files suggestions."""
    try:
        leads = load_leads(cfg.get("sources", {}))
    except LeadsError as exc:
        print(f"❌ leads.toml malformed — {exc}", file=sys.stderr)
        return 3  # distinct from a usage error (2): the file is broken, not the call
    state = load_state()
    if args.action == "mark":
        ids = {c["id"] for lead in leads for c in lead["claims"]}
        if args.claim not in ids or not args.result:
            print("❌ leads mark needs --claim <id from `audit.py leads`> and --result", file=sys.stderr)
            return 2
        try:
            mark_lead(state, args.claim, args.result, args.where or "")
        except ValueError as exc:
            print(f"❌ {exc}", file=sys.stderr)
            return 2
        save_state(state)
        print(f"✅ marked claim {args.claim} {args.result}")
        return 0
    resolved = resolve_leads(leads, state)
    if args.post:
        try:
            number, known = _update_issue()
        except Exception as exc:
            print(f"❌ leads: cannot read the update issue, nothing posted — {exc}", file=sys.stderr)
            return 1
        body = suggestions_comment(resolved, known)
        if body is None:
            print("LEADS_POST=none|reason=no new trace results")
            return 0
        if args.dry_run:
            print(body)
            print("LEADS_POST=dry-run")
            return 0
        return _post_update(number, body, "LEADS_POST")
    print(json.dumps(resolved, indent=2))
    print(f"LEADS={len(resolved['leads'])}|open={len(resolved['open'])}", file=sys.stderr)
    return 0


def cmd_diff_source(cfg: dict, sid: str, file: str, final_url: Optional[str], cache: bool = False) -> int:
    src = cfg.get("sources", {}).get(sid)
    if src is None:
        print(f"❌ unknown source id {sid!r} — not in {SOURCES_TOML.name}", file=sys.stderr)
        return 2
    p = Path(file)
    data = _read(p) if p.is_file() else None
    v = diff_source(src, data, final_url)
    print(f"VERDICT={v['verdict']}|id={sid}|sha={v['sha']}|marker={clean(v['marker'])}|reason={clean(v['reason'])}")
    if cache and data and v["verdict"] != "not-checked":
        print(f"PAGE_CACHED={cache_page(sid, data).as_posix()}")
    return 0


def _entries(args: argparse.Namespace, cfg: dict) -> List[Entry]:
    return inventory(_repos(args), cfg.get("audiences", {}), getattr(args, "only", None))


def cmd_inventory(args: argparse.Namespace, cfg: dict) -> int:
    entries = _entries(args, cfg)
    audiences = cfg.get("audiences", {})
    for e in entries:
        lines = str(len(e.text.splitlines())) if e.data is not None else "unmeasured"
        print(f"FILE={e.key}|audience={e.audience}|kind={e.kind}|sha={e.sha}|lines={lines}")
        for s in sections(e.text, e.audience, audiences):
            print(f"SECTION={e.key}#L{s['start']}-L{s['end']}|audience={s['audience']}|heading={clean(s['heading'])}")
    print(f"INVENTORY={len(entries)}|repos={len({e.repo for e in entries})}")
    return 0


def cmd_lint(args: argparse.Namespace, cfg: dict) -> int:
    rules = parse_rules(RULES_MD.read_text(encoding="utf-8"))
    audiences = cfg.get("audiences", {})
    # R-37's vocabulary is every qualified tool name in the fleet's skills, whatever --only narrows to.
    fleet = inventory(_repos(args), audiences)
    vocab = mcp_vocabulary(e.text for e in fleet if e.kind == "skill")
    if args.file:
        path = Path(args.file).resolve()
        entry = Entry(key=path.as_posix(), path=path, repo="", kind=kind_of(path.name) or "claude-md",
                      data=_read(path))
        if "/.claude/rules/" in path.as_posix():
            entry.kind = "rules"
        match = next((e for e in fleet if e.path.resolve() == path), None)
        if match is not None:
            entry = match
        entries = [entry]
    else:
        entries = _entries(args, cfg)
        if args.changed_only:
            ledger = read_ledger_issue()
            plan = plan_scan({e.key: e.sha for e in entries}, ledger,
                             rules_rubric(), args.rescan_all)
            entries = [e for e in entries if plan[e.key][0] == "scan"]
    total = 0
    for e in entries:
        if e.data is None:
            print(f"HITS={e.key}|audience={e.audience}|kind={e.kind}|lines=unmeasured|hits=unmeasured")
            continue
        res = lint_entry(e, rules, audiences, vocab)
        if args.detail or args.file:
            for line in hit_detail(res):
                print(line)
        print(hits_line(res))
        total += sum(res.counts().values())
    print(f"LINT={len(entries)} files|hits={total}")
    return 0


def cmd_dedup(args: argparse.Namespace) -> int:
    findings = json.loads(Path(args.findings).read_text(encoding="utf-8"))
    repos = _repos(args)
    master, lite = _master_and_lite(repos)
    out = dedup(findings, master, lite)
    print(json.dumps(out, indent=2))
    counts = {s: sum(1 for f in out if f["scope"] == s)
              for s in ("shared-with-scaffold", "shared-with-lite", "scaffold-master", "repo-local")}
    print("DEDUP=" + "|".join(f"{k}={v}" for k, v in counts.items()), file=sys.stderr)
    return 0


def cmd_state(args: argparse.Namespace, cfg: dict) -> int:
    today = dt.date.fromisoformat(args.date) if args.date else dt.date.today()
    state = load_state()
    if args.action == "show":
        every = int(cfg.get("check_every_days", 7))
        due = 0
        for sid in cfg.get("sources", {}):
            rec = state.get("sources", {}).get(sid)
            is_due, nxt = source_due(rec, every, today)
            due += is_due
            print(f"SOURCE_STATE={sid}|due={'yes' if is_due else 'no'}|last={(rec or {}).get('last', 'never')}"
                  f"|last_verdict={(rec or {}).get('verdict', 'none')}|next_due={nxt}")
            if not is_due:
                # Fresh means "last seen unchanged, within cadence": the digest line says so, cached.
                print(f"VERDICT=unchanged|id={sid}|sha=cached|marker=cached"
                      f"|reason=checked {rec['last']}, fresh until {nxt}")
        print(f"SCAN_STATE=last={state.get('last_scan', 'never')}")
        print(f"DUE={due}")
        return 0
    if args.scan:
        state["last_scan"] = today.isoformat()
        save_state(state)
        print(f"✅ marked scan {today.isoformat()}")
        return 0
    if not args.source or args.verdict not in VERDICTS or args.verdict == "not-checked":
        print("❌ state mark needs --scan, or --source ID with --verdict unchanged|changed|new-guide "
              "(a not-checked source was not checked and is never marked)", file=sys.stderr)
        return 2
    if args.source not in cfg.get("sources", {}):
        print(f"❌ unknown source id {args.source!r}", file=sys.stderr)
        return 2
    state.setdefault("sources", {})[args.source] = {"last": today.isoformat(), "verdict": args.verdict}
    save_state(state)
    print(f"✅ marked {args.source} {args.verdict} {today.isoformat()}")
    return 0


def cmd_ledger(args: argparse.Namespace, cfg: dict) -> int:
    rubric = rules_rubric()
    if args.action == "plan":
        entries = _entries(args, cfg)
        ledger = read_ledger_issue()
        plan = plan_scan({e.key: e.sha for e in entries}, ledger, rubric, args.rescan_all)
        for e in entries:
            action, reason = plan[e.key]
            print(f"PLAN={e.key}|action={action}|reason={reason}|sha={e.sha}")
        n = {a: sum(1 for v in plan.values() if v[0] == a) for a in ("scan", "skip", "unmeasured")}
        print(f"LEDGER=#{ledger['number'] or 'none'}|rubric={rubric[:12]}|ledger_rubric={(ledger['rubric'] or 'none')[:12]}"
              f"|scan={n['scan']}|skip={n['skip']}|unmeasured={n['unmeasured']}")
        return 0
    if args.action == "write":
        surface = {e.key for e in inventory(_repos(args), cfg.get("audiences", {}))}
        recorded = partition_run(json.loads(Path(args.run).read_text(encoding="utf-8")))["recorded"]
        outside = sorted(set(recorded) - surface)
        if outside:
            print(f"❌ not in the scan surface: {', '.join(outside)}", file=sys.stderr)
            return 2
        ledger = read_ledger_issue()
        files, dropped = merge_ledger(ledger, recorded, rubric)
        body = render_ledger_body(files, rubric, args.date or dt.date.today().isoformat(),
                                  cfg.get("sources", {}), dropped)
        if args.dry_run:
            print(body)
            print(f"LEDGER_WRITE=dry-run|recorded={len(recorded)}|entries={len(files)}")
            return 0
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as fh:
            fh.write(body)
            tmp = fh.name
        try:
            url = _audit_issue("upsert", "--repo", LEDGER_REPO, "--kind", KIND, "--label", "audit-meta",
                               "--title", TITLE, "--body-file", tmp).strip()
        finally:
            Path(tmp).unlink(missing_ok=True)
        print(f"LEDGER_WRITE={url}|recorded={len(recorded)}|entries={len(files)}|dropped={dropped}")
        return 0
    # comment
    number = read_ledger_issue()["number"]
    if number is None:
        print("❌ no prompt-audit ledger issue yet — run `ledger write` first", file=sys.stderr)
        return 2
    res = git_run.run_gh(["issue", "comment", str(number), "--repo", LEDGER_REPO,
                          "--body-file", args.body_file], timeout=120)
    if res.returncode != 0:
        print(f"❌ gh issue comment failed: {(res.stderr or res.stdout).strip()}", file=sys.stderr)
        return 1
    print(f"LEDGER_COMMENT={(res.stdout or '').strip()}")
    return 0


def cmd_digest(args: argparse.Namespace, cfg: dict) -> int:
    run = json.loads(Path(args.run).read_text(encoding="utf-8"))
    rules = parse_rules(RULES_MD.read_text(encoding="utf-8"))
    run.setdefault("rubric", rules_rubric())
    master, lite = _master_and_lite(_repos(args))
    provisional = provisional_rules(rules, cfg.get("sources", {}), run.get("sources", []))
    body, status = render_digest(run, rules, master, lite, provisional)
    print(body)
    print(f"DIGEST=status={status}", file=sys.stderr)
    return 0


def cmd_ping(args: argparse.Namespace, cfg: dict) -> int:
    run = json.loads(Path(args.run).read_text(encoding="utf-8"))
    if run.get("dry_run"):
        print("❌ ping: a dry run delivers nothing, so it sends no ping", file=sys.stderr)
        return 2
    rules = parse_rules(RULES_MD.read_text(encoding="utf-8"))
    print(render_ping(run, args.comment_url, provisional_rules(rules, cfg.get("sources", {}), run.get("sources", []))))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ensure_utf8_stdio()
    ap = argparse.ArgumentParser(description="Deterministic half of /prompt-audit.")
    ap.add_argument("--projects-toml", default=None, help="fleet membership file (tests)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("sources")
    d = sub.add_parser("diff-source")
    d.add_argument("--id", required=True)
    d.add_argument("--file", required=True)
    d.add_argument("--final-url", default=None)
    d.add_argument("--cache", action="store_true", help="keep the fetched bytes for the coverage check")
    evp = sub.add_parser("evals")
    evp.add_argument("--dir", default=None, help="aggregate directory (default ~/.claude/prompt-audit/evals)")
    evp.add_argument("--date", default=None, help="today, for the staleness check (tests)")
    ld = sub.add_parser("leads")
    ld.add_argument("action", nargs="?", choices=("list", "mark"), default="list")
    ld.add_argument("--claim")
    ld.add_argument("--result", help="traced | untracked-vendor | unverified")
    ld.add_argument("--where", help="the vendor section or page the claim traced to")
    ld.add_argument("--post", action="store_true", help="comment new trace results on the update issue")
    ld.add_argument("--dry-run", action="store_true", help="with --post: print the comment, post nothing")
    cv = sub.add_parser("coverage")
    cv.add_argument("--scratch", default=None, help="this run's fetched pages (<id>.md); the cache fills the rest")
    cv.add_argument("--extract", action="store_true", help="print the text of each new uncovered section")
    cv.add_argument("--post", action="store_true", help="comment the new uncovered sections on the update issue")
    cv.add_argument("--drafts", default=None, help="drafted entries to attach to the --post comment")
    cv.add_argument("--dry-run", action="store_true", help="with --post: print the comment, post nothing")
    cv.add_argument("--offline", action="store_true", help="skip reading the update issue (every uncovered is new)")
    inv = sub.add_parser("inventory")
    inv.add_argument("--only", default=None)
    li = sub.add_parser("lint")
    target = li.add_mutually_exclusive_group(required=True)
    target.add_argument("--file")
    target.add_argument("--all", action="store_true")
    li.add_argument("--only", default=None)
    li.add_argument("--changed-only", action="store_true", help="only files the ledger plan would scan")
    li.add_argument("--rescan-all", action="store_true")
    li.add_argument("--detail", action="store_true", help="print HIT= lines for --all too")
    dd = sub.add_parser("dedup")
    dd.add_argument("--findings", required=True)
    st = sub.add_parser("state")
    st.add_argument("action", choices=("show", "mark"))
    st.add_argument("--source")
    st.add_argument("--verdict")
    st.add_argument("--scan", action="store_true")
    st.add_argument("--date", default=None)
    lg = sub.add_parser("ledger")
    lg.add_argument("action", choices=("plan", "write", "comment"))
    lg.add_argument("--only", default=None)
    lg.add_argument("--rescan-all", action="store_true")
    lg.add_argument("--run", help="run.json: files judged with no unmeasured rule are recorded")
    lg.add_argument("--date", default=None)
    lg.add_argument("--dry-run", action="store_true")
    lg.add_argument("--body-file")
    dg = sub.add_parser("digest")
    dg.add_argument("--run", required=True)
    pg = sub.add_parser("ping")
    pg.add_argument("--run", required=True)
    pg.add_argument("--comment-url", required=True, help="the LEDGER_COMMENT= URL that proves delivery")
    dr = sub.add_parser("drift")
    dr.add_argument("--run", required=True)
    dr.add_argument("--date", default=None)
    dr.add_argument("--dry-run", action="store_true", help="read the existing issues, print the bodies, write nothing")
    args = ap.parse_args(argv)

    cfg = load_toml()
    if args.cmd == "sources":
        return cmd_sources(cfg)
    if args.cmd == "diff-source":
        return cmd_diff_source(cfg, args.id, args.file, args.final_url, args.cache)
    if args.cmd == "coverage":
        return cmd_coverage(args, cfg)
    if args.cmd == "leads":
        return cmd_leads(args, cfg)
    if args.cmd == "evals":
        return cmd_evals(args)
    if args.cmd == "inventory":
        return cmd_inventory(args, cfg)
    if args.cmd == "lint":
        return cmd_lint(args, cfg)
    if args.cmd == "dedup":
        return cmd_dedup(args)
    if args.cmd == "state":
        return cmd_state(args, cfg)
    if args.cmd == "ledger":
        if args.action == "write" and not args.run:
            ap.error("ledger write needs --run")
        if args.action == "comment" and not args.body_file:
            ap.error("ledger comment needs --body-file")
        return cmd_ledger(args, cfg)
    if args.cmd == "drift":
        return cmd_drift(args, cfg)
    if args.cmd == "ping":
        return cmd_ping(args, cfg)
    return cmd_digest(args, cfg)
