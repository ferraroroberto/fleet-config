"""prompt-audit skill evals and contracts: verdict tiers and honest errors, slash-invoked cases, weekly eval rotation and fold-in, and the rules.md / sources.toml / vendor-neutrality contracts.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/prompt_audit/test_evals_and_contracts.py`  (also invoked by tests/run_acceptance.py)
"""
from __future__ import annotations

import datetime as dt
import json
import re
import shutil
import tempfile
import tomllib
from pathlib import Path

from _suite import AUD, CFG, REPO, RULES, SKILL, SKILL_GOOD, _h, check, pa, write  # noqa: E402


# ---- skill evals (#1130): argv, verdict tiers, honest errors, wrapper, the 9 pilot cases ----

import eval_rotation as er  # noqa: E402

ev_argv = er.eval_argv("claude", Path("w"), "m", "j", Path("o"), "none", runs=2)
check("--no-publish" in ev_argv and ev_argv[ev_argv.index("--max-cost-usd") + 1] == "10",
      f"every plugin-eval argv carries --no-publish and --max-cost-usd 10 (got {ev_argv})")


def ev_row(tier, status, case="c1", skill="s"):
    return {"skill": skill, "tier": tier, "model": tier, "case": case, "status": status, "cost_usd": 0.0, "seconds": 0}


def ev_verdict(rows):
    return er.verdicts(rows)[0]["verdict"]


check(ev_verdict([ev_row("haiku", "fail"), ev_row("sonnet", "pass"), ev_row("opus", "pass")]) == "consider",
      "a haiku-only failure is advice (consider)")
check(ev_verdict([ev_row("haiku", "pass"), ev_row("sonnet", "fail"), ev_row("opus", "pass")]) == "failure",
      "a sonnet failure is a failure")
check(ev_verdict([ev_row("haiku", "fail"), ev_row("sonnet", "pass"), ev_row("opus", "fail")]) == "failure",
      "an opus failure is a failure even with haiku failing too")
check(ev_verdict([ev_row("haiku", "pass"), ev_row("sonnet", "error"), ev_row("opus", "pass")]) == "unmeasured",
      "an errored scored tier is unmeasured, never pass")
check(ev_verdict([ev_row("haiku", "pass"), ev_row("sonnet", "pass")]) == "unmeasured",
      "a tier that never ran leaves the case unmeasured")
check(ev_verdict([ev_row(t, "pass") for t in er.TIERS] + [ev_row("extra-model", "fail")]) == "pass",
      "an extra (non-tier) model never changes a verdict")

ev_result = {"cases": [
    {"name": "ok", "arms": {"with": [{"passed": True, "error": None, "costUsd": 0.1, "durationSeconds": 5}] * 3}},
    {"name": "bad", "arms": {"with": [{"passed": False, "error": "exit 1: API Error: 400", "costUsd": 0, "durationSeconds": 1}]}},
    {"name": "mixed", "arms": {"with": [{"passed": True, "error": None}, {"passed": True, "error": "timeout"}]}},
]}
ev_rows = {r["case"]: r for r in er.case_rows(ev_result, "s", "sonnet", "m", ["ok", "bad", "mixed", "gone"])}
check(ev_rows["ok"]["status"] == "pass" and abs(ev_rows["ok"]["cost_usd"] - 0.3) < 1e-9, "a clean passing case is pass with its cost")
check(ev_rows["bad"]["status"] == "error" and "400" in ev_rows["bad"]["reason"], "an errored run is recorded as error with its reason")
check(ev_rows["mixed"]["status"] == "error", "a case with any errored run is error, never pass")
check(ev_rows["gone"]["status"] == "not-run", "a case missing from the result is not-run")
check([er.weight_of(t) for t in ("haiku", "sonnet", "opus", "qwen-x")] == ["advisory", "scored", "scored", "extra"],
      "each row records how it counts: scored, advisory (haiku) or extra (a non-tier model)")
check(all(r["status"] == "error" for r in er.case_rows(None, "s", "opus", "m", ["a", "b"], "exit 2")),
      "no result file -> every case error, never pass")

# ---- slash-invoked cases: the `tool_used: Skill` grader is informational under --ablation none (#1238) ----
# Shape of a real `quick` / `typo-lands-verified` run: both regex graders and the llm judge PASS, `skill-loaded`
# reads "Skill called 0x" because a slash command expands inline and never calls the Skill tool.
SL_GRADERS = [{"name": "ephemeral-branch", "type": "regex"}, {"name": "order", "type": "llm"},
              {"name": "skill-loaded", "type": "tool_used", "config": {"tool": "Skill"}}]


def sl_run(content_ok=True, loaded=False, drop_content=False):
    graders = [{"name": "skill-loaded", "passed": loaded, "weight": 1}]
    if not drop_content:
        graders = [{"name": "ephemeral-branch", "passed": True, "weight": 1},
                   {"name": "order", "passed": content_ok, "weight": 1}] + graders
    return {"passed": False, "error": None, "costUsd": 0.1, "durationSeconds": 5, "score": 0.75, "graders": graders}


def sl_rows(runs, ablation, graders=SL_GRADERS, threshold=1):
    result = {"suite": {"threshold": threshold}, "cases": [{"name": "c", "graders": graders, "arms": {"with": runs}}]}
    return er.case_rows(result, "s", "sonnet", "m", ["c"], ablation=ablation)[0]


sl = sl_rows([sl_run()] * 3, "none")
check(sl["status"] == "pass", f"under --ablation none a run whose content graders all pass is pass though skill-loaded read 0x (got {sl})")
check("skill-loaded (informational): 0/3" in sl["reason"], f"...and the informational grader is still reported, not hidden (got {sl['reason']})")
check(sl_rows([sl_run()] * 3, "with-without")["status"] == "fail",
      "under with-without nothing changes: the run's own passed flag decides (the CLI excludes with-only graders itself)")
check(sl_rows([sl_run()] * 3, "none", graders=[])["status"] == "fail" and er.case_rows(
    {"cases": [{"name": "c", "graders": SL_GRADERS, "arms": {"with": [sl_run()] * 3}}]}, "s", "sonnet", "m", ["c"])[0]["status"] == "fail",
      "the default ablation (and a case that names no informational grader) keeps the pre-#1238 behaviour")
check(sl_rows([sl_run(content_ok=False)] * 3, "none")["status"] == "fail",
      "a failing content grader is still a fail under --ablation none")
check(sl_rows([sl_run(content_ok=False), sl_run(), sl_run()], "none")["status"] == "pass"
      and sl_rows([sl_run(content_ok=False), sl_run(content_ok=False), sl_run()], "none")["status"] == "fail",
      "the majority rule over runs is unchanged")
check(sl_rows([sl_run(drop_content=True)] * 3, "none")["status"] == "error",
      "a run with no scored grader left is error (unknown), never pass")
nolist = sl_rows([{"passed": True, "error": None}] * 3, "none")
check(nolist["status"] == "pass" and sl_rows([{"passed": False, "error": None}] * 3, "none")["status"] == "fail",
      "a run with no per-run grader list falls back to its own passed flag")
check("skill-loaded (informational): 3/3" in sl_rows([sl_run(loaded=True)] * 3, "none")["reason"],
      "an informational grader that did pass is reported as such")

ev_tmp = Path(tempfile.mkdtemp(prefix="skill-evals-test-"))
try:
    write(ev_tmp / "skills" / "has" / "SKILL.md", SKILL_GOOD)
    write(ev_tmp / "skills" / "has" / "ref.md", "# ref\n")
    write(ev_tmp / "skills" / "has" / "evals" / "c1" / "prompt.md", "---\nmax_turns: 2\n---\n\n/has go\n")
    write(ev_tmp / "skills" / "has" / "evals" / "c1" / "graders" / "g.md", "---\ntype: llm\n---\n\nPASS if ok.\n")
    write(ev_tmp / "skills" / "none" / "SKILL.md", SKILL_GOOD)
    write(ev_tmp / ".claude" / "skills" / "proj" / "SKILL.md", SKILL_GOOD)
    write(ev_tmp / ".claude" / "skills" / "proj" / "evals" / "c2" / "prompt.md", "Check the thing in plain words.\n")
    kept = Path(tempfile.mkdtemp(prefix="claude-eval-"))
    elsewhere = ev_tmp / "claude-eval-notmine"
    elsewhere.mkdir()
    gone = er.drop_kept_temps(f"  kept temp (run failed): {kept}\n  kept temp (run failed): {elsewhere}\nother line\n")
    check(gone == [kept] and not kept.exists() and elsewhere.exists(),
          "only a claude-eval-* sandbox directly under the temp dir is removed")
    found = er.eval_skills(ev_tmp)
    check(sorted(found) == ["has", "proj"], f"only skills carrying evals/<case>/prompt.md are eligible (got {sorted(found)})")
    wrap = er.build_wrapper(found["has"], ev_tmp / "wrap")
    check((wrap / ".claude-plugin" / "plugin.json").is_file() and (wrap / "skills" / "has" / "ref.md").is_file()
          and not (wrap / "skills" / "has" / "evals").exists() and (wrap / "evals" / "c1" / "prompt.md").is_file(),
          "the wrapper carries the manifest and the skill without its cases, and the cases at the eval root")
    check(er.ablation_for(found["has"]) == "none" and er.ablation_for(found["proj"]) == "with-without",
          "slash-invoked cases run without a baseline arm; natural-language ones with it")
    ev_out = er.write_aggregate([ev_row(t, "pass") for t in er.TIERS], {"started": "2026-10-04T12:00:00+00:00"},
                                ev_tmp / "agg")
    agg = json.loads((ev_tmp / "agg" / "latest.json").read_text(encoding="utf-8"))
    check(ev_out.is_file() and agg["verdicts"][0]["verdict"] == "pass" and len(agg["rows"]) == 3,
          "the aggregate is written with rows and verdicts, plus latest.json")
finally:
    shutil.rmtree(ev_tmp, ignore_errors=True)

pilot = {s: sorted(p.parent.name for p in (REPO / "skills" / s / "evals").glob("*/prompt.md"))
         for s in ("issue-start", "quick", "perf-review")}
check(all(len(v) == 3 for v in pilot.values()), f"three pilot cases per skill (got {pilot})")
shell_grants = [p for s in pilot for p in (REPO / "skills" / s / "evals").glob("*/prompt.md")
                if re.search(r"^allowed_tools:.*\b(?:Bash|PowerShell)\b", p.read_text(encoding="utf-8"), re.M)]
check(shell_grants == [], f"no pilot case grants a shell tool (got {shell_grants})")
check(all(list((p.parent / "graders").glob("*.md")) for s in pilot
          for p in (REPO / "skills" / s / "evals").glob("*/prompt.md")), "every pilot case has graders")

# ---- weekly evals (#1131): rotation, fold-in honesty, R-44 into prompt-drift, delivery untouched ----

rot = er.pick_rotation({"a": "1", "b": "2", "c": "3", "d": "4", "e": "5", "f": "6", "g": "7", "h": "8"},
                       {"a": {"last": "2026-09-01", "sha": "1"}, "b": {"last": "2026-09-08", "sha": "0"},
                        "c": {"last": "2026-09-08", "sha": "3"}, "d": {"last": "2026-09-15", "sha": "x"},
                        "e": {"last": "2026-09-15", "sha": "y"}, "f": {"last": "2026-09-15", "sha": "z"},
                        "g": {"last": "2026-09-22", "sha": "w"}, "h": {"last": "2026-09-22", "sha": "8"}})
check(rot == ["a", "b", "c", "d", "e", "f"],
      f"rotation: the 3 least recently evaluated plus at most 3 changed since their last eval (got {rot})")
check(er.pick_rotation({"new": "1", "old": "2"}, {"old": {"last": "2026-09-01", "sha": "2"}}) == ["new", "old"],
      "rotation: a never-evaluated skill goes first; only eligible skills are picked")
check(er.pick_rotation({}, {"gone": {"last": "2026-09-01", "sha": "1"}}) == [],
      "rotation: a skill without evals/ is never picked, even if it was evaluated before")


def agg(started, statuses):
    return {"started": started, "paths": {"s": "fleet-config/skills/s/SKILL.md"},
            "rows": [{"skill": "s", "tier": t, "weight": w, "case": c, "status": st}
                     for (t, w, c), st in statuses.items()]}


T = {("haiku", "advisory", "c1"): "pass", ("sonnet", "scored", "c1"): "pass", ("opus", "scored", "c1"): "pass",
     ("haiku", "advisory", "c2"): "pass", ("x", "extra", "c1"): "pass"}
prev_agg = agg("2026-09-29T21:00:00+00:00", T)
now_agg = agg("2026-10-04T21:00:00+00:00", T | ({("sonnet", "scored", "c1"): "fail", ("haiku", "advisory", "c2"): "fail",
                                                      ("x", "extra", "c1"): "fail"}))
folded = pa.fold_evals(now_agg, prev_agg, dt.date(2026, 10, 5))
check([(f["verdict"], f["note"]) for f in folded["findings"]] ==
      [("consider", "eval case c2 passed last run and fails now on haiku"),
       ("violation", "eval case c1 passed last run and fails now on sonnet")],
      f"fold: a scored pass->fail is a violation, an advisory one a consider, an extra model nothing (got {folded['findings']})")
check(pa.fold_evals(None, prev_agg)["status"] == "not-checked", "fold: no aggregate is not-checked")
ev_dir = Path(tempfile.mkdtemp(prefix="evals-fold-"))
try:
    (ev_dir / "20260929T210000.json").write_text(json.dumps(prev_agg), encoding="utf-8")
    (ev_dir / "20261004T210000.json").write_text(json.dumps(now_agg), encoding="utf-8")
    (ev_dir / "latest.json").write_text(json.dumps(now_agg), encoding="utf-8")
    (ev_dir / "rotation.json").write_text(json.dumps({"s": {"last": "2026-10-04", "sha": "x"}}), encoding="utf-8")
    newest_e, prev_e = pa.latest_two(ev_dir)
    check(newest_e == now_agg and prev_e == prev_agg,
          "latest_two reads only the timestamped aggregates; latest.json and rotation.json are not runs")
    # an aggregate marked invalidated (#1238) is kept on disk but is not a run: it can be neither newest nor previous
    (ev_dir / "20261005T000000.json").write_text(json.dumps(dict(now_agg, invalidated="fleet-config#1238")), encoding="utf-8")
    newest_i, prev_i = pa.latest_two(ev_dir)
    check(newest_i == now_agg and prev_i == prev_agg and (ev_dir / "20261005T000000.json").is_file(),
          "latest_two skips an invalidated aggregate (kept on disk) instead of reading its rows as a run")
finally:
    shutil.rmtree(ev_dir, ignore_errors=True)
check(pa.fold_evals(now_agg, prev_agg, dt.date(2026, 10, 20))["status"] == "not-checked", "fold: a stale aggregate is not-checked")
check(any("evals: not-checked" in l for l in pa.eval_digest_lines(pa.fold_evals(None, None))),
      "digest: a missing aggregate prints evals: not-checked")
check(any("evals: not-checked" in l for l in pa.eval_digest_lines(None)), "digest: a run without the fold-in prints not-checked")
sc = {(x["skill"], x["tier"]): x for x in folded["scores"]}
check(sc[("s", "sonnet")]["passed"] == 0 and sc[("s", "sonnet")]["delta"] == -1, "digest scores carry the pass count and its delta")

drift_run_findings = [dict(f) for f in folded["findings"] if f["verdict"] in ("violation", "consider")]
eval_items = pa.drift_items(drift_run_findings, RULES, {"fleet-config/skills/s/SKILL.md": "x\n"}, "", "")
check([(i["rule"], i["path"]) for i in eval_items.get("fleet-config", [])] == [("R-44", "fleet-config/skills/s/SKILL.md")],
      f"a scored pass->fail becomes exactly one R-44 prompt-drift item; the consider is not filed (got {eval_items})")
eval_body, _ = pa.merge_drift("", eval_items["fleet-config"], set(), set(), RULES, "2026-10-05", "r" * 12)
check(len([l for l in eval_body.splitlines() if l.startswith("- [ ]") and " R-44 " in l]) == 1 and "· hard" in eval_body, "the R-44 item renders once, hard tier")

dc = __import__("delivery_check")
base_run = {"date": "2026-10-05", "dry_run": False, "sources": [], "update_issue": None, "scan_ran": True,
            "plan": [], "judgments": {}, "coverage": [], "leads": None}
stamps = []
for ev in (folded, pa.fold_evals(None, None), {"status": "error-garbage"}):
    body_e, status_e = pa.render_digest(dict(base_run, evals=ev), RULES)
    stamps.append((status_e, re.search(r"<!-- prompt-audit-digest [^>]*-->", body_e).group(0)))
check(len({st for st in stamps}) == 1 and stamps[0][0] == "complete",
      "an eval-job failure or a missing aggregate leaves the digest status and stamp unchanged")
check(dc.delivered({"scan": "posted", "guides": "unchanged", "update-issue": "none"}) is None,
      "the delivery check passes on the stamp the eval section cannot change")

bat = (SKILL / "evals" / "run-weekly.bat").read_text(encoding="utf-8")
bat_cmds = [l.strip() for l in bat.splitlines() if l.strip() and not l.strip().upper().startswith(("REM", "@ECHO"))]
check(bat_cmds[0] == r"cd /d E:\automation\fleet-config"
      and bat_cmds[1].startswith(r"E:\automation\fleet-config\.venv\Scripts\python.exe ")
      and "eval_rotation.py --rotate" in bat_cmds[1] and len(bat_cmds) == 2 and "start " not in bat.lower(),
      f"the eval job launcher is cd /d + this repo's venv Python running eval_rotation.py --rotate in the foreground (got {bat_cmds})")

# ---- contracts: rules.md, sources.toml, vendor neutrality ----

check(sorted(r for r, v in RULES.items() if v["detect"] == "lint") == pa.LINT_RULES,
      "every audit.py lint rule is a `Detect: lint` rule in rules.md and vice versa")
check(all(v["detect"] in ("lint", "judgment", "eval") for v in RULES.values()), "every rule declares lint, judgment or eval")
check(sorted(r for r, v in RULES.items() if v["assist"]) == pa.ASSIST_RULES,
      "every lint-assisted judgment rule in rules.md has a lint assist in audit.py and vice versa")
_page_sections = {"Avoid time-sensitive information", "Naming conventions", "Token budgets", "Writing effective descriptions",
                  "YAML frontmatter requirements", "Core quality", "Avoid deeply nested references",
                  "Structure longer reference files with a table of contents", "Avoid Windows-style paths",
                  "Runtime environment", "MCP tool references", "Set appropriate degrees of freedom",
                  "Use workflows for complex tasks", "Implement feedback loops", "Create verifiable intermediate outputs",
                  "Use consistent terminology", "Avoid offering too many options", "Provide utility scripts",
                  "Package dependencies", "Avoid assuming tools are installed", "Solve, don't defer", "Code and scripts",
                  "Concise is key", "Progressive disclosure patterns", "Visual overview: From simple to complex",
                  "Pattern 1: High-level guide with references", "Pattern 2: Domain-specific organization",
                  "Pattern 3: Conditional details", "Template pattern", "Examples pattern",
                  "Test with all models you plan to use", "Build evaluations first",
                  "Develop Skills iteratively with Claude", "Observe how Claude navigates Skills", "Testing",
                  "Conditional workflow pattern", "Use visual analysis", "Next steps"}
_cited = set()
for _l in pa.RULES_MD.read_text(encoding="utf-8").splitlines():
    if _l.startswith("Source:"):
        for _m in re.finditer(r"agent-skills/best-practices \(([^)]*)\)", _l):
            _cited.update(s.strip() for s in _m.group(1).split(";"))
check(_page_sections <= _cited, f"every leaf section of the skill-authoring page has a home (missing {sorted(_page_sections - _cited)})")
vendors = {c["vendor"] for c in AUD.values()} | {"shared", "conflict"}
check(all(v["vendor"] in vendors and v["file"] in ("any", "claude-md", "skill", "skill-ref") and v["tier"] in ("easy", "hard")
          for v in RULES.values()), "every rule carries a known vendor tag, file scope and tier")
check(list(RULES) == [f"R-{i:02d}" for i in range(1, len(RULES) + 1)], "rule ids are sequential")

for sid_, s in CFG["sources"].items():
    check(s.get("role") in ("index", "hub", "per-model", "instruction-files", "changelog"), f"{sid_}: known role")
    check(s.get("marker_kind") in ("model-list", "latest-model-frontmatter", "llms-txt-lines", "none"), f"{sid_}: marker_kind")
    check(re.fullmatch(r"[0-9a-f]{12}", s.get("baseline_sha", "")) is not None, f"{sid_}: baseline_sha is sha256/12")
    check(s.get("vendor") in {c["vendor"] for c in AUD.values()}, f"{sid_}: vendor has an audience")
    check(isinstance(s.get("baseline_date"), dt.date) and s["url"].startswith("https://"), f"{sid_}: date + https url")

VENDOR_WORDS = re.compile(r"anthropic|openai|claude|codex|gpt|opus|sonnet|haiku|fable|mythos|astra|gemini", re.I)
FILE_CONVENTIONS = re.compile(r"global-CLAUDE\.md|CLAUDE\.md|\.claude/|\.claude\b|claude-md")


def vendor_mentions(text: str) -> list:
    return [l for l in text.splitlines() if VENDOR_WORDS.search(FILE_CONVENTIONS.sub("", l))]


check(vendor_mentions((SKILL / "SKILL.md").read_text(encoding="utf-8")) == [], "no vendor or model name in SKILL.md")
for _py in [SKILL / "audit.py", *sorted((SKILL / "prompt_audit").glob("*.py"))]:
    check(vendor_mentions(_py.read_text(encoding="utf-8")) == [],
          f"no vendor or model name in {_py.relative_to(SKILL).as_posix()}")
prose = [l for l in pa.RULES_MD.read_text(encoding="utf-8").splitlines()
         if not l.startswith("Source:") and not l.startswith("### R-")]
prose = [re.sub(r"`?\[(?:anthropic|openai|shared|conflict)\]`?", "", l) for l in prose]
check([l for l in prose if VENDOR_WORDS.search(FILE_CONVENTIONS.sub("", l))] == [],
      "rules.md names vendors only inside tags and Source lines")
with open(SKILL / "sources.toml", "rb") as fh:
    check("audiences" in tomllib.load(fh), "sources.toml carries the audience vocabulary")

_h.report_and_exit("test_evals_and_contracts")
