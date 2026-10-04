"""`python -m perf_review measure|file <repo>` — the deterministic half of /perf-review (fleet-config#1121).

measure  resolve the target's loopback URL, refuse a dead port (never start
         anything), run the Playwright phone-load leg under the target's own
         `.venv`, time the hot endpoints it saw at their own poll spacing,
         score everything against the budgets, record the local ledger.
         Exit: 0 all within budget, 1 something over budget, 3 unmeasured
         (dead port, no Playwright, an unreadable leg).
file     merge the latest run into the repo's one managed `perf-review`
         issue — a dry run unless `--apply`.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import socket
import ssl
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, Optional
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import audit_issue  # noqa: E402
import git_run  # noqa: E402
import hooks_state  # noqa: E402
import service_probe  # noqa: E402
from design_review.capture import probe_listening, resolve_interpreter, target_commit  # noqa: E402
from design_review.filing import repo_slug  # noqa: E402
from design_review.plan import PlanError, resolve_target  # noqa: E402
from no_window import NO_WINDOW  # noqa: E402

from . import http_probe, report, stamping  # noqa: E402

LOAD_PY = Path(__file__).resolve().parent / "load.py"
DEFAULT_SPACING_S = 30.0
MIN_SPACING_S = 5.0
LOAD_TIMEOUT_S = 600.0
EXIT = {"pass": 0, "over-budget": 1, "unmeasured": 3}


def run_dir_for(target: str) -> Path:
    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = hooks_state.state_dir() / "perf-review" / target / stamp
    path.mkdir(parents=True, exist_ok=True)
    return path


def tls_name(base_url: str) -> str:
    """The first DNS name the app's certificate is issued for, or "" — read from the cert, never configured.

    Lets the Chromium leg reach loopback under the name the cert verifies for,
    so the warm leg's HTTP cache behaves as it does on the phone. Stays in the
    child's argv and the local run dir; never written to an issue.
    """
    parts = urlsplit(base_url)
    if parts.scheme != "https" or not parts.port:
        return ""
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
        with socket.create_connection((parts.hostname, parts.port), timeout=5) as raw, \
                ctx.wrap_socket(raw) as tls:
            der = tls.getpeercert(binary_form=True)
        with tempfile.TemporaryDirectory() as tmp:
            pem = Path(tmp) / "peer.pem"
            pem.write_text(ssl.DER_cert_to_PEM_cert(der), encoding="ascii")
            decoded = ssl._ssl._test_decode_cert(str(pem))  # type: ignore[attr-defined]  # stdlib, no third-party parser
    except (OSError, ssl.SSLError, ValueError, AttributeError):
        return ""
    names = [v for k, v in decoded.get("subjectAltName", ()) if k == "DNS" and not v.startswith("*")]
    return names[0] if names else ""


def run_load(python: str, base_url: str, run_dir: Path, block: dict, budgets: dict, settle_s: int) -> dict:
    """Spawn `load.py` under the target's interpreter; its `load.json`, or an error leg."""
    phone = budgets["phone"]
    argv = [python, str(LOAD_PY), "--url", base_url, "--out", str(run_dir),
            "--tls-name", tls_name(base_url), "--ready-selector", str(block.get("ready_selector", "")),
            "--rtt-ms", str(phone["rtt_ms"]), "--down-mbps", str(phone["down_mbps"]),
            "--up-mbps", str(phone["up_mbps"]), "--settle-s", str(settle_s)]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=LOAD_TIMEOUT_S, creationflags=NO_WINDOW)
        (run_dir / "load.log").write_text((proc.stdout or "") + (proc.stderr or ""), encoding="utf-8")
        return json.loads((run_dir / "load.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return {"legs": {"android_cold": {"status": "error", "error": f"load leg failed: {exc}"[:300]}}, "api": {}}


def deploy_state(root: Optional[Path], live: Optional[str]) -> dict:
    """Whether the build the app serves is the checkout's HEAD (fleet-config#1180, facilitation-suite#164).

    A fix that merged while a session held the app was never restarted, so the re-run measured the old build and
    `DIFF` could not tell "not deployed" from "not fixed". `live` is the `git_sha` the app reports; `state` is
    `live` (it is HEAD), `behind` (an ancestor of HEAD, `ahead` commits back), `differs` (not an ancestor, or no
    commit of this checkout) or `unknown` (the app serves no build id, or HEAD cannot be read), never folded
    into `live`.
    """
    out: dict = {"state": "unknown", "live": None, "head": None, "ahead": None, "reason": None}
    if not live:
        out["reason"] = "the app serves no build id (`[perf.review] api_version_path`)"
        return out
    out["live"] = str(live)[:7]
    if root is None:
        out["reason"] = "no repo checkout to compare against"
        return out

    def git(*args: str):
        return git_run.run_git(["-C", str(root), *args])

    head = git("rev-parse", "HEAD")
    if head.returncode != 0 or not head.stdout.strip():
        out["reason"] = "the checkout's HEAD could not be read"
        return out
    head_sha = head.stdout.strip()
    out["head"] = head_sha[:7]
    served = git("rev-parse", "--verify", "--quiet", f"{live}^{{commit}}")
    if served.returncode != 0 or not served.stdout.strip():
        out.update(state="differs", reason="the served build is not a commit of this checkout")
        return out
    served_sha = served.stdout.strip()
    if served_sha == head_sha:
        out["state"] = "live"
        return out
    if git("merge-base", "--is-ancestor", served_sha, head_sha).returncode != 0:
        out.update(state="differs", reason="the served build is not an ancestor of HEAD")
        return out
    count = git("rev-list", "--count", f"{served_sha}..{head_sha}")
    out.update(state="behind", ahead=int(count.stdout.strip()) if count.returncode == 0 and count.stdout.strip().isdigit() else None)
    return out


def schedule(load: dict, block: dict, min_spacing: float) -> Dict[str, float]:
    """Which paths to time and how far apart: `/` plus every query-less `/api/` GET the page made, at its own poll interval."""
    exclude = [str(p) for p in block.get("exclude", [])]
    cadence = {str(k): float(v) for k, v in dict(block.get("cadence", {})).items()}
    plan: Dict[str, float] = {"/": max(min_spacing, 10.0)}
    for path, info in load.get("api", {}).items():
        if info.get("query") or any(path.startswith(p) for p in exclude):
            continue
        plan[path] = max(min_spacing, info.get("interval_s") or DEFAULT_SPACING_S)
    for path in block.get("endpoints", []):
        plan.setdefault(str(path), DEFAULT_SPACING_S)
    for path, every in cadence.items():
        if path in plan:
            plan[path] = max(min_spacing, every)
    return plan


def cmd_measure(a: argparse.Namespace) -> int:
    try:
        target = resolve_target(a.repo, url_override=a.url)
    except PlanError as exc:
        print(f"PERF=unmeasured reason=BAD_TARGET detail={exc}")
        return EXIT["unmeasured"]
    block = report.load_review_block(target.root)
    if "error" in block:
        print(f"PERF=unmeasured reason=BAD_FLEET_TOML detail={block['error']}")
        return EXIT["unmeasured"]
    budgets = report.load_budgets(block.get("budgets"))
    listening = probe_listening(target.base_url)
    if listening["status"] != "listening":
        print(f"PERF=unmeasured reason={listening['status']} detail={listening['detail']} (never started)")
        return EXIT["unmeasured"]
    run_dir = run_dir_for(target.name)
    print(f"RUN_DIR={run_dir}")
    load: dict = {"legs": {}, "api": {}}
    if not a.no_load:
        interp = resolve_interpreter(target, a.python)
        if interp["status"] != "ok":
            load["legs"]["android_cold"] = {"status": "error", "error": f"{interp['status']}: {interp['detail']}"}
        else:
            load = run_load(str(interp["python"]), target.base_url, run_dir, block, budgets, a.settle)
    plan = schedule(load, block, a.min_spacing)
    probe = {"index": http_probe.index_checks(target.base_url), "stamping": stamping.classify(target.root),
             "endpoints": http_probe.time_endpoints(target.base_url, plan, a.duration)}
    v = report.verdict(probe, load, budgets)
    build = service_probe.running_sha(urlsplit(target.base_url).port or 0, str(block.get("api_version_path", "/api/version")))
    v["deploy"] = deploy_state(target.root, build)
    run_id = run_dir.name
    entry = report.record(target.name, run_id, v, target_commit(target), build)
    d = report.diff(entry, report.previous_entry(target.name, run_id))
    for name, doc in (("probe.json", probe), ("verdict.json", {**v, "run_id": run_id, "live_build": build, "diff": d})):
        (run_dir / name).write_text(json.dumps(doc, indent=1), encoding="utf-8")
    (run_dir / "issue-body.md").write_text(report.merge_body("", {**v, "diff": d}, run_id, build, _dt.date.today().isoformat()), encoding="utf-8")
    for c in v["checks"]:
        sampled = f" samples={','.join(report._fmt(x) for x in c['samples'])} outliers={','.join(map(str, c['outliers'])) or 'none'}" if c.get("samples") else ""
        print(f"CHECK {c['status']:<10} {c['id']:<24} measured={report._fmt(c['measured'])} budget={report._fmt(c['budget'])}{sampled}")
        if c.get("split"):
            print(f"SPLIT {c['id']:<24} api={c['split']['api_kb']} KB other={c['split']['asset_kb']} KB")
        if c.get("polls"):
            print(f"POLLS {c['id']:<24} poll_requests={c['polls']['requests']} poll_bytes_kb={c['polls']['kb']} (left out of the boot figures)")
        for r in c.get("top_responses", []):
            print(f"TOP {c['id']:<24} {report._top_line(r)}")
    for e in v["endpoints"]:
        print(f"ENDPOINT {e['status']:<10} {e['path']:<28} n={e.get('n')} p50={report._fmt(e.get('p50'))} p95={report._fmt(e.get('p95'))}")
    moved = d["ready_baseline_changed"]
    baseline = f" ready_by={moved['from']}->{moved['to']} (baselines do not compare)" if moved else ""
    slower = ",".join(f"{e['path']}:{report._fmt(e['from'])}->{report._fmt(e['to'])}" for e in d["slower"]) or "none"
    print(f"DIFF previous={d['previous_run']} fixed={','.join(d['fixed']) or 'none'} regressed={','.join(d['regressed']) or 'none'} "
          f"slower_p95_ms={slower}{baseline}")
    dep = v["deploy"]
    print(f"BUILD state={dep['state']} live={dep['live'] or 'unknown'} head={dep['head'] or 'unknown'}"
          + (f" behind_by={dep['ahead']}" if dep["ahead"] is not None else "") + (f" ({dep['reason']})" if dep["reason"] else ""))
    if dep["state"] in ("behind", "differs"):
        print("BUILD_WARNING the app serves an older build than the checkout's HEAD: a fix merged since is NOT live in this run. "
              "Restart it per the repo's CLAUDE.md (never from this skill), then re-run; do not read DIFF as 'not fixed'.")
    print(f"PERF={v['summary']['overall']} pass={v['summary']['pass']} fail={v['summary']['fail']} unmeasured={v['summary']['unmeasured']}")
    return EXIT[v["summary"]["overall"]]


def cmd_file(a: argparse.Namespace) -> int:
    try:
        target = resolve_target(a.repo, url_override=a.url)
    except PlanError as exc:
        print(f"FILE=refused detail={exc}")
        return 2
    runs = sorted(p for p in (hooks_state.state_dir() / "perf-review" / target.name).glob("*T*Z") if p.is_dir())
    run_dir = Path(a.run) if a.run else (runs[-1] if runs else None)
    if run_dir is None or not (run_dir / "verdict.json").is_file():
        print(f"FILE=refused detail=no measured run for {target.name}; run `measure` first")
        return 2
    v = json.loads((run_dir / "verdict.json").read_text(encoding="utf-8"))
    slug = a.slug or repo_slug(target.root)
    if not slug:
        print("FILE=refused detail=no origin remote; pass --slug owner/name")
        return 2
    existing = audit_issue.get_managed(slug, report.KIND)["body"]
    body = report.merge_body(existing, v, v["run_id"], v.get("live_build"), _dt.date.today().isoformat())
    if not a.apply:
        print("\n".join(audit_issue.dry_run_lines(slug, report.KIND, report.TITLE, body)))
        return 0
    print(audit_issue.upsert_issue(slug, report.KIND, report.TITLE, body, report.LABEL))
    return 0


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(prog="perf_review", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("measure", help="measure a running app against the budgets (read-only)")
    m.add_argument("repo")
    m.add_argument("--url", help="base URL override (tests, a non-fleet target)")
    m.add_argument("--python", help="interpreter with Playwright (default: the target's .venv)")
    m.add_argument("--duration", type=float, default=300.0, help="seconds of endpoint timing (default 300)")
    m.add_argument("--min-spacing", type=float, default=MIN_SPACING_S, help="floor between two GETs of one path")
    m.add_argument("--settle", type=int, default=40, help="seconds the cold page is watched to learn poll intervals")
    m.add_argument("--no-load", action="store_true", help="skip the browser leg (endpoint timing of / only)")
    f = sub.add_parser("file", help="merge the latest run into the managed perf-review issue (dry run unless --apply)")
    f.add_argument("repo")
    f.add_argument("--url")
    f.add_argument("--run", help="a run directory (default: the latest)")
    f.add_argument("--slug", help="owner/name override")
    f.add_argument("--apply", action="store_true")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    return cmd_measure(a) if a.cmd == "measure" else cmd_file(a)
