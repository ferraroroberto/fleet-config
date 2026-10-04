"""Run fleet skills' eval cases through `claude plugin eval` on each model tier (fleet-config#1130).

A skill opts in by carrying `evals/<case>/` (a `prompt.md` plus `graders/*.md`, plugin
eval's own case format) next to its `SKILL.md`. For each picked skill this builds a
throwaway plugin wrapper (`.claude-plugin/plugin.json` + `skills/<name>/` + `evals/`),
runs `claude plugin eval` once per model (it has no multi-model flag), and writes one
aggregate: skill x model x case -> pass / fail / error, notional cost, seconds.

- Every run carries `--no-publish` (the report would otherwise publish) and
  `--max-cost-usd` (a hard notional ceiling per run).
- A run that errors, or whose result file is missing or unreadable, is `error`; a
  case never run is `not-run`. Neither is ever `pass`.
- Verdicts: a failing case on the `sonnet` or `opus` tier is a `failure`; a failure
  on `haiku` alone is advice (`consider`).
- Credentials: by default the child sessions use the CLI's own credential, which is
  plugin eval's design. `--hub URL` routes them through a local Anthropic-shape
  gateway instead (thinking off, a dummy key); tier ids then come from its
  `/v1/models`.

stdlib only. Run: `<venv python> .claude/skills/prompt-audit/eval_rotation.py --skills issue-start,quick,perf-review`
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "skills" / "_lib"))
from no_window import NO_WINDOW  # noqa: E402

SKILL_TIERS = ("skills", ".claude/skills")
TIERS = ("haiku", "sonnet", "opus")
JUDGE_TIER = "sonnet"
ADVISORY_TIERS = {"haiku"}          # a failure only here is advice, never a violation
MAX_COST_USD = 10
RUN_TIMEOUT_S = 3600                # one plugin-eval invocation (all cases of one skill on one model)
SKIP_COPY = shutil.ignore_patterns("evals", "__pycache__", "conversations")


def out_root() -> Path:
    override = os.environ.get("PROMPT_AUDIT_STATE_DIR")
    return (Path(override) if override else Path.home() / ".claude" / "prompt-audit") / "evals"


def eval_skills(repo_root: Path = REPO_ROOT) -> Dict[str, Path]:
    """Skill name -> directory, for every skill in this repo's two tiers that carries at least one case."""
    found: Dict[str, Path] = {}
    for tier in SKILL_TIERS:
        for skill_md in sorted((repo_root / tier).glob("*/SKILL.md")):
            cases = skill_md.parent / "evals"
            if any((c / "prompt.md").is_file() for c in cases.glob("*")):
                found.setdefault(skill_md.parent.name, skill_md.parent)
    return found


def build_wrapper(skill_dir: Path, dest: Path) -> Path:
    """A minimal plugin around one skill: the manifest, the skill (cases excluded) and its cases."""
    name = skill_dir.name
    (dest / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (dest / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": f"fleet-{name}", "version": "0.0.0",
                    "description": f"eval wrapper for the {name} skill"}) + "\n", encoding="utf-8")
    shutil.copytree(skill_dir, dest / "skills" / name, ignore=SKIP_COPY)
    shutil.copytree(skill_dir / "evals", dest / "evals", ignore=shutil.ignore_patterns("results", "__pycache__"))
    return dest


def ablation_for(skill_dir: Path) -> str:
    """`none` when every case is slash-invoked (no trigger to test); `with-without` otherwise."""
    bodies = [_body(p.read_text(encoding="utf-8")) for p in (skill_dir / "evals").glob("*/prompt.md")]
    return "none" if bodies and all(b.lstrip().startswith("/") for b in bodies) else "with-without"


def _body(text: str) -> str:
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[end + 4:]
    return text


def eval_argv(claude: str, wrapper: Path, model: str, judge: str, out_dir: Path, ablation: str,
              runs: Optional[int] = None, concurrency: int = 1) -> List[str]:
    argv = [claude, "plugin", "eval", str(wrapper), "--no-publish", "--trust-plugin",
            "--ablation", ablation, "--model", model, "--judge-model", judge,
            "--max-cost-usd", str(MAX_COST_USD), "--output-dir", str(out_dir), "-j", str(concurrency)]
    if runs:
        argv += ["--runs", str(runs)]
    return argv


def hub_models(base_url: str) -> Dict[str, str]:
    """Tier -> the gateway's own model id (first `claude-<tier>-…` id it lists); a tier it lacks is absent."""
    with urllib.request.urlopen(f"{base_url.rstrip('/')}/v1/models", timeout=10) as resp:
        ids = [m.get("id", "") for m in json.loads(resp.read()).get("data", [])]
    return {t: next(i for i in ids if i.startswith(f"claude-{t}-")) for t in TIERS
            if any(i.startswith(f"claude-{t}-") for i in ids)}


def hub_env(base_url: str) -> Dict[str, str]:
    env = dict(os.environ)
    env.update(ANTHROPIC_BASE_URL=base_url, ANTHROPIC_API_KEY="local-dummy", MAX_THINKING_TOKENS="0")
    return env


def weight_of(tier: str) -> str:
    """How a row counts: `scored` (a failure is a violation), `advisory` (a consider) or `extra` (reported only)."""
    if tier not in TIERS:
        return "extra"
    return "advisory" if tier in ADVISORY_TIERS else "scored"


def case_rows(result: Optional[dict], skill: str, tier: str, model: str, cases: List[str],
              error: str = "") -> List[dict]:
    """One row per case from a plugin-eval aggregate. Missing result -> every case `error`; absent case -> `not-run`."""
    by_name = {c.get("name"): c for c in (result or {}).get("cases", [])}
    rows = []
    for name in cases:
        c = by_name.get(name)
        base = {"skill": skill, "tier": tier, "weight": weight_of(tier), "model": model, "case": name,
                "cost_usd": 0.0, "seconds": 0}
        if result is None:
            rows.append(dict(base, status="error", reason=error or "no result file"))
            continue
        if c is None:
            rows.append(dict(base, status="not-run", reason="case absent from the result (cost ceiling or filter)"))
            continue
        runs = (c.get("arms") or {}).get("with") or []
        errors = [r.get("error") for r in runs if r.get("error")]
        cost = round(sum(float(r.get("costUsd") or 0) for r in runs), 4)
        secs = int(sum(float(r.get("durationSeconds") or 0) for r in runs))
        if not runs:
            status, reason = "not-run", "no runs recorded"
        elif errors:
            status, reason = "error", str(errors[0])[:300]
        else:
            passed = sum(1 for r in runs if r.get("passed"))
            status = "pass" if passed * 2 > len(runs) else "fail"
            reason = f"{passed}/{len(runs)} runs passed"
        rows.append(dict(base, status=status, reason=reason, cost_usd=cost, seconds=secs))
    return rows


def verdicts(rows: List[dict]) -> List[dict]:
    """Per skill x case: `failure` (a scored tier failed), `consider` (only an advisory tier failed),
    `unmeasured` (a scored tier errored or never ran), else `pass`."""
    out = []
    keys = sorted({(r["skill"], r["case"]) for r in rows})
    for skill, case in keys:
        mine = [r for r in rows if r["skill"] == skill and r["case"] == case and r["tier"] in TIERS]
        failed = {r["tier"] for r in mine if r["status"] == "fail"}
        unknown = {r["tier"] for r in mine if r["status"] in ("error", "not-run")}
        if failed - ADVISORY_TIERS:
            verdict = "failure"
        elif unknown - ADVISORY_TIERS or len({r["tier"] for r in mine}) < len(TIERS):
            verdict = "unmeasured"
        elif failed:
            verdict = "consider"
        else:
            verdict = "pass"
        out.append({"skill": skill, "case": case, "verdict": verdict, "failed_tiers": sorted(failed),
                    "unknown_tiers": sorted(unknown)})
    return out


def run_skill(claude: str, skill: str, skill_dir: Path, models: Dict[str, str], judge: str,
              env: Dict[str, str], runs: Optional[int], work: Path) -> List[dict]:
    cases = sorted(p.parent.name for p in (skill_dir / "evals").glob("*/prompt.md"))
    wrapper = build_wrapper(skill_dir, work / f"plugin-{skill}")
    ablation = ablation_for(skill_dir)
    rows: List[dict] = []
    for tier, model in models.items():
        out_dir = work / f"out-{skill}-{tier}"
        argv = eval_argv(claude, wrapper, model, judge, out_dir, ablation, runs)
        print(f"ℹ️ eval {skill} on {tier} ({model}): {len(cases)} case(s)", flush=True)
        try:
            proc = subprocess.run(argv, cwd=wrapper, env=env, capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=RUN_TIMEOUT_S, creationflags=NO_WINDOW)
            drop_kept_temps(proc.stdout or "")
            tail = (proc.stdout or proc.stderr or "").strip().splitlines()[-1:] or [""]
            error = f"plugin eval exit {proc.returncode}: {tail[0][:200]}"
        except (OSError, subprocess.TimeoutExpired) as exc:
            error = f"plugin eval did not finish: {exc}"
        result = _read_result(out_dir / "aggregate-result.json")
        rows += case_rows(result, skill, tier, model, cases, error)
    return rows


def drop_kept_temps(output: str) -> List[Path]:
    """Remove the sandboxes plugin eval keeps after a failed run (`kept temp (run failed): <dir>`).

    A weekly job would otherwise leave one per failed run in the temp dir. Only a
    `claude-eval-*` directory directly under the system temp dir is removed.
    """
    removed = []
    tmp_root = Path(tempfile.gettempdir()).resolve()
    for line in output.splitlines():
        if "kept temp" not in line or ":" not in line:
            continue
        cand = Path(line.split("):", 1)[-1].strip())
        try:
            ok = cand.resolve().parent == tmp_root and cand.name.startswith("claude-eval-") and cand.is_dir()
        except OSError:
            ok = False
        if ok:
            shutil.rmtree(cand, ignore_errors=True)
            removed.append(cand)
    return removed


def _read_result(path: Path) -> Optional[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("cases"), list) else None


def write_aggregate(rows: List[dict], meta: dict, root: Optional[Path] = None) -> Path:
    root = root or out_root()
    root.mkdir(parents=True, exist_ok=True)
    body = dict(meta, rows=rows, verdicts=verdicts(rows),
                cost_usd=round(sum(r["cost_usd"] for r in rows), 4))
    stamp = meta["started"].replace(":", "").replace("-", "")[:15]
    path = root / f"{stamp}.json"
    for p in (path, root / "latest.json"):
        p.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    return path


def table(rows: List[dict]) -> str:
    lines = ["| skill | case | tier | model | status | cost (notional $) | s |", "|---|---|---|---|---|---|---|"]
    lines += [f"| {r['skill']} | {r['case']} | {r['tier']} | {r['model']} | {r['status']} | {r['cost_usd']:.3f} "
              f"| {r['seconds']} |" for r in rows]
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--skills", required=True, help="comma-separated skill names that carry evals/")
    ap.add_argument("--hub", default=None, help="route child sessions through this local gateway URL")
    ap.add_argument("--extra-model", action="append", default=[],
                    help="also run this model id, reported but never part of a verdict")
    ap.add_argument("--runs", type=int, default=None, help="runs per case (plugin eval default: 3)")
    ap.add_argument("--out-dir", default=None, help="aggregate directory (default ~/.claude/prompt-audit/evals)")
    ap.add_argument("--dry-run", action="store_true", help="print the plan and each argv, run nothing")
    args = ap.parse_args(argv)

    known = eval_skills()
    picked = [s.strip() for s in args.skills.split(",") if s.strip()]
    missing = [s for s in picked if s not in known]
    if missing:
        print(f"❌ no evals/ cases for: {', '.join(missing)}", file=sys.stderr)
        return 2
    if args.hub:
        try:
            models = hub_models(args.hub)
        except (OSError, ValueError) as exc:
            print(f"❌ gateway {args.hub} unreachable or unreadable: {exc}", file=sys.stderr)
            return 1
        env = hub_env(args.hub)
        judge = models.get(JUDGE_TIER, JUDGE_TIER)
    else:
        models, env, judge = {t: t for t in TIERS}, dict(os.environ), JUDGE_TIER
    models.update({m: m for m in args.extra_model})
    claude = shutil.which("claude") or "claude"
    meta = {"started": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "skills": picked,
            "models": models, "judge": judge, "route": "gateway" if args.hub else "cli-credential",
            "max_cost_usd_per_run": MAX_COST_USD,
            "paths": {s: f"fleet-config/{known[s].relative_to(REPO_ROOT).as_posix()}/SKILL.md" for s in picked}}
    if args.dry_run:
        for s in picked:
            for tier, model in models.items():
                print(" ".join(eval_argv(claude, Path(f"<wrapper-{s}>"), model, judge, Path("<out>"),
                                         ablation_for(known[s]), args.runs)))
        return 0
    work = Path(tempfile.mkdtemp(prefix="skill-evals-"))
    try:
        rows = [r for s in picked for r in run_skill(claude, s, known[s], models, judge, env, args.runs, work)]
    finally:
        shutil.rmtree(work, ignore_errors=True)
    path = write_aggregate(rows, meta, Path(args.out_dir) if args.out_dir else None)
    print(table(rows))
    counts = {v: sum(1 for x in verdicts(rows) if x["verdict"] == v) for v in ("pass", "consider", "failure", "unmeasured")}
    print(f"EVALS={path.as_posix()}|" + "|".join(f"{k}={v}" for k, v in counts.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
