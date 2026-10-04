"""Unit tests for .claude/skills/prompt-audit/ — `audit.py` and its `prompt_audit` package (fleet-config#832, #931).

Drives the deterministic half of /prompt-audit against a synthetic fleet in a temp
dir (no real repo, ledger issue, or ~/.claude/prompt-audit is touched): lint hits on
a fixture with known planted anti-patterns (exact counts) and on a clean fixture
(zero), audience classification including a marker-scoped section, worktree
exclusion, scaffold/lite dedup, all four diff-source verdicts, corrupt-state
degradation, the skip-unchanged ledger plan, digest honesty (unmeasured is never
compliant), and the rule-set/sources/vendor-neutrality contracts.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/test_prompt_audit.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import contextlib
import datetime as dt
import io
import json
import os
import re
import shutil
import sys
import tempfile
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SKILL = REPO / ".claude" / "skills" / "prompt-audit"
sys.path.insert(0, str(SKILL))
import prompt_audit as pa  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check

CFG = pa.load_toml()
AUD = CFG["audiences"]
RULES = pa.parse_rules(pa.RULES_MD.read_text(encoding="utf-8"))

PLANTED = """# Planted

- CRITICAL: You MUST run the gate.
- If in doubt, use the search tool.
- Always double-check your answer.
- Summarize progress every 3 tool calls.
- Hold all findings for the final response.
- Do not use markdown in replies.
- Think step by step before answering.
- Never think about unrelated files.
- Only report high-severity issues.
- Use a prefill for JSON output.
- Before August 2025 use the old endpoint.
- Outline a plan before editing.
- Always commit to the branch.
- Never commit to the branch.
- Run `MUST_FLAG` here.

```bash
double-check CRITICAL every 3 tool calls
```

Route Claude requests through the hub.
"""

CLEAN = """# Clean

Run the verification gate before pushing, because CI only checks formatting.
Keep changes to what the issue asks for.
"""

GLOBAL = """# Global

Keep commits small.

### Spawning *(Claude Code only — skip on other agents)*

Never spawn more than three workers.

### Shared

Never push to main.
"""

SKILL_GOOD = """---
name: good
description: Audits the thing and reports drift. E.g. "/good", "check the thing".
---

# good

Body.
"""

SKILL_BAD = """---
name: bad
description: Use this when you want your thing checked. E.g. "can you check my thing".
---

# bad
"""


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def entry_for(text: str, kind: str = "claude-md", audience: str = pa.NEUTRAL, key: str = "x/CLAUDE.md") -> pa.Entry:
    return pa.Entry(key=key, path=Path(key), repo=key.split("/")[0], kind=kind, audience=audience,
                    data=text.encode("utf-8"))


def by_rule(res: pa.LintResult, rule: str) -> list:
    return [h for h in res.hits if h.rule == rule]


# ---- lint: planted counts are exact, clean yields nothing ----

planted = pa.lint_entry(entry_for(PLANTED), RULES, AUD)
expected = {"R-01": 2, "R-02": 1, "R-03": 1, "R-04": 1, "R-05": 1, "R-06": 1, "R-07": 1,
            "R-09": 1, "R-10": 1, "R-11": 1, "R-12": 1, "R-13": 3, "R-16": 1, "R-17": 1}
check(planted.counts() == expected, f"planted fixture -> exact counts (got {planted.counts()})")
check(planted.neg == (3, 16), f"negative ratio counts instruction lines only (got {planted.neg})")
check(not by_rule(planted, "R-08"), "'never think about' is not a do-not-think rule")
check(all(h.line != 19 for h in planted.hits), "fenced block content is never linted")
check(by_rule(planted, "R-01")[0].cap == "consider", "single-vendor rule in a neutral file caps at consider")
check(by_rule(planted, "R-03")[0].cap == "violation", "shared rule in a neutral file is a violation")
check(by_rule(planted, "R-16")[0].text.endswith("Always commit to the branch."),
      "contradiction anchors at the first of the pair")
check("Route Claude requests" in by_rule(planted, "R-17")[0].text, "vendor-only paragraph flagged with its raw line")
check("hits=R-01:2," in pa.hits_line(planted) and "|neg=3/16|" in pa.hits_line(planted), "HITS manifest line format")

clean = pa.lint_entry(entry_for(CLEAN), RULES, AUD)
check(clean.hits == [], f"clean fixture -> no hits (got {[(h.rule, h.line) for h in clean.hits]})")
check("|hits=none|" in pa.hits_line(clean), "clean HITS line says hits=none")

# R-30 (#1070): reasoning-in-the-answer instructions; a plain "explain" or a fence is not a hit
R30 = """# Reasoning

- Explain your reasoning in the response.
- Write out your reasoning before the verdict.
- Show your work.
- Think out loud in your answer.
- Explain the change to the reviewer.
- Read the summarized thinking when the reasoning is needed downstream.

```
show your work
```
"""
r30 = pa.lint_entry(entry_for(R30), RULES, AUD)
check(r30.counts() == {"R-30": 4}, f"R-30 counts the four reasoning-in-the-answer lines only (got {r30.counts()})")
check(by_rule(r30, "R-30")[0].cap == "consider", "R-30 is single-vendor: consider in a neutral file")
check(pa.lint_entry(entry_for(R30, audience="claude"), RULES, AUD).hits[0].cap == "violation",
      "R-30 is a violation in a claude-scoped file")

multi = pa.lint_entry(entry_for("# M\n\nKeep the hub on one port.\nRoute Claude traffic through it.\n"), RULES, AUD)
check([h.line for h in by_rule(multi, "R-17")] == [4], "R-17 anchors on the line naming the vendor term, not the paragraph start")

long_md = pa.lint_entry(entry_for("line\n" * 201), RULES, AUD)
check(long_md.counts().get("R-14") == 1 and long_md.size == "201l/200", "CLAUDE.md over 200 lines -> R-14")
check(pa.lint_entry(entry_for("line\n" * 200), RULES, AUD).counts().get("R-14") is None, "exactly 200 lines is within cap")
big_agents = pa.lint_entry(entry_for("x" * 32769, kind="agents-md", key="x/AGENTS.md"), RULES, AUD)
check(big_agents.counts().get("R-14") == 1, "AGENTS.md over 32768 bytes -> R-14")

good = pa.lint_entry(entry_for(SKILL_GOOD, kind="skill", key="x/.claude/skills/good/SKILL.md"), RULES, AUD)
check(good.counts() == {}, f"third-person skill description -> no R-15 (got {good.counts()})")
check(good.desc_words == "9", f"prose words come from skill_description.prose_words (got {good.desc_words})")
bad = pa.lint_entry(entry_for(SKILL_BAD, kind="skill", key="x/.claude/skills/bad/SKILL.md"), RULES, AUD)
check(bad.counts().get("R-15") == 2, f"'you'/'your' in prose counted, quoted 'you'/'my' ignored (got {bad.counts()})")
broken = pa.lint_entry(entry_for("---\nname: x\ndescription: a: b\n---\n", kind="skill", key="x/s/SKILL.md"), RULES, AUD)
check(broken.desc_words == "unmeasured", "unparseable frontmatter -> description unmeasured, never 0")

# ---- audience: a marker-scoped section, the file's audience for the rest ----

glob_res = pa.lint_entry(entry_for(GLOBAL, key="fleet-config/global-CLAUDE.md"), RULES, AUD)
caps = {h.line: h.cap for h in by_rule(glob_res, "R-13")}
check(caps == {7: "violation", 11: "consider"},
      f"R-13 is a violation inside the vendor-scoped section, consider outside (got {caps})")
secs = pa.sections(GLOBAL, pa.NEUTRAL, AUD)
check(len(secs) == 1 and secs[0]["audience"] == "claude" and (secs[0]["start"], secs[0]["end"]) == (5, 8),
      f"section spans its heading to the next same-level heading (got {secs})")
check(pa.verdict_cap("openai", "claude", AUD) is None, "other vendor's rule does not apply in a scoped file")
check(pa.verdict_cap("conflict", "claude", AUD) is None and pa.verdict_cap("conflict", pa.NEUTRAL, AUD) == "violation",
      "conflict rule applies only to neutral readers")

# ---- inventory over a synthetic fleet ----

tmp = Path(tempfile.mkdtemp(prefix="prompt-audit-test-"))
try:
    fleet = tmp / "automation"
    write(fleet / "fleet-config" / "global-CLAUDE.md", GLOBAL)
    write(fleet / "fleet-config" / "CLAUDE.md", CLEAN)
    write(fleet / "fleet-config" / "AGENTS.md", "Read CLAUDE.md.\n")
    write(fleet / "fleet-config" / "skills" / "gs" / "SKILL.md", SKILL_GOOD)
    write(fleet / "fleet-config" / ".claude" / "skills" / "fs" / "SKILL.md", SKILL_GOOD)
    write(fleet / "project-scaffolding" / "CLAUDE.md", "# Master\n\n- Never commit secrets to the repository, ever.\n")
    write(fleet / "project-scaffolding" / "AGENTS.md", "See CLAUDE.md\n")
    write(fleet / "repo-a" / "CLAUDE.md", "# A\n\n- Never commit secrets to the repository, ever.\n")
    write(fleet / "repo-a" / "AGENTS.md", "Instructions live in CLAUDE.md.\n")
    write(fleet / "repo-a" / ".claude" / "rules" / "style.md", CLEAN)
    write(fleet / "repo-a" / ".claude" / "skills" / "a1" / "SKILL.md", SKILL_BAD)
    write(fleet / "repo-b" / "CLAUDE.md", "# B\n\n- Never commit secrets to the repository, ever.\n")
    write(fleet / "repo-a-wt-7" / "CLAUDE.md", CLEAN)
    write(fleet / "repo-a-wt-7" / ".git", "gitdir: ../repo-a/.git/worktrees/7\n")
    write(fleet / "ignored" / "CLAUDE.md", CLEAN)
    projects = write(tmp / "projects.toml", "".join(
        f'[{n}]\ncwd_prefix = "{(fleet / n).as_posix()}"\n\n'
        for n in ("fleet-config", "project-scaffolding", "repo-a", "repo-b", "repo-a-wt-7", "ignored"))
        + '[global]\narchitecture_ignore = ["ignored"]\n')

    repos = pa.fleet_repos(projects)
    entries = pa.inventory(repos, AUD)
    keys = [e.key for e in entries]
    check(keys == [
        "fleet-config/global-CLAUDE.md", "project-scaffolding/CLAUDE.md",
        "fleet-config/CLAUDE.md", "fleet-config/AGENTS.md",
        "project-scaffolding/AGENTS.md",
        "repo-a/CLAUDE.md", "repo-a/AGENTS.md", "repo-a/.claude/rules/style.md",
        "repo-b/CLAUDE.md",
        "fleet-config/skills/gs/SKILL.md", "fleet-config/.claude/skills/fs/SKILL.md",
        "repo-a/.claude/skills/a1/SKILL.md",
    ], f"inventory order, dedup, worktree + ignore exclusion (got {keys})")
    aud = {e.key: e.audience for e in entries}
    kinds = {e.key: e.kind for e in entries}
    check(aud["fleet-config/global-CLAUDE.md"] == pa.NEUTRAL, "global-CLAUDE.md is neutral")
    check(aud["repo-a/AGENTS.md"] == pa.NEUTRAL, "AGENTS.md is neutral")
    check(aud["repo-a/CLAUDE.md"] == pa.NEUTRAL, "CLAUDE.md with an AGENTS.md pointer is neutral")
    check(aud["repo-b/CLAUDE.md"] == "claude", "CLAUDE.md with no AGENTS.md pointer is vendor-scoped")
    check(aud["repo-a/.claude/rules/style.md"] == "claude" and kinds["repo-a/.claude/rules/style.md"] == "rules",
          ".claude/rules file is vendor-scoped, kind rules")
    check(kinds["fleet-config/global-CLAUDE.md"] == "claude-md" and kinds["repo-a/AGENTS.md"] == "agents-md",
          "kinds from file names")

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = pa.main(["--projects-toml", str(projects), "inventory"])
    text = out.getvalue()
    check(rc == 0 and "INVENTORY=12|repos=4" in text, f"inventory CLI summary (got {text[-80:]!r})")
    check(re.search(r"^FILE=fleet-config/global-CLAUDE\.md\|audience=neutral\|kind=claude-md\|sha=[0-9a-f]{12}\|lines=11$",
                    text, re.M) is not None, "FILE manifest line format")
    check("SECTION=fleet-config/global-CLAUDE.md#L5-L8|audience=claude|" in text, "SECTION line for the scoped section")

    # ---- dedup: shared with the master is filed once, with a propagate list ----
    findings = [
        {"path": "repo-a/CLAUDE.md", "rule": "R-13", "verdict": "consider", "line": 3,
         "text": "- Never commit secrets to the repository, ever."},
        {"path": "repo-b/CLAUDE.md", "rule": "R-13", "verdict": "violation", "line": 3,
         "text": "- **Never** commit secrets to the repository, ever."},
        {"path": "repo-a/CLAUDE.md", "rule": "R-01", "verdict": "consider", "line": 9, "text": "- MUST do the local thing here."},
        {"path": "fleet-config/global-CLAUDE.md", "rule": "R-03", "verdict": "violation", "line": 4,
         "text": "Always double-check the port before restarting."},
        {"path": "project-scaffolding/CLAUDE.md", "rule": "R-13", "verdict": "consider", "line": 3,
         "text": "- Never commit secrets to the repository, ever."},
    ]
    master = (fleet / "project-scaffolding" / "CLAUDE.md").read_text(encoding="utf-8")
    lite = "Always double-check the port before restarting.\n"
    ann = pa.dedup(findings, master, lite)
    check([a["scope"] for a in ann] == ["shared-with-scaffold", "shared-with-scaffold", "repo-local",
                                        "shared-with-lite", "scaffold-master"],
          f"dedup scopes (got {[a['scope'] for a in ann]})")
    check(ann[0]["file_against"] == "project-scaffolding/CLAUDE.md" and ann[0]["propagate_to"] == ["repo-a", "repo-b"],
          "shared finding filed against the master with every carrying repo (markup-insensitive)")
    check(ann[4]["propagate_to"] == ["repo-a", "repo-b"], "the master's own finding carries the propagate list")
    check(ann[3]["propagate_to"] == ["fleet-config-lite"], "global line shared with the lite port")
    check(ann[2]["file_against"] == "repo-a/CLAUDE.md" and "propagate_to" not in ann[2], "repo-local stays local")
    check(pa.dedup([{"path": "repo-a/CLAUDE.md", "rule": "R-01", "text": "- Never."}], "- Never.", "")[0]["scope"]
          == "repo-local", "lines under 20 normalised chars never match as shared")

    # ---- state: corrupt degrades to everything due; mark round-trips ----
    state_dir = tmp / "state"
    os.environ["PROMPT_AUDIT_STATE_DIR"] = str(state_dir)
    write(state_dir / "state.json", "{not json")
    check(pa.load_state() == {}, "corrupt state -> empty")
    today = dt.date(2026, 9, 13)
    check(pa.source_due(None, 7, today) == (True, "now"), "never-checked source is due")
    check(pa.source_due({"last": "2026-09-10", "verdict": "unchanged"}, 7, today) == (False, "2026-09-17"),
          "unchanged within cadence is fresh")
    check(pa.source_due({"last": "2026-09-10", "verdict": "changed"}, 7, today)[0], "a changed source stays due")
    check(pa.source_due({"last": "2026-09-06", "verdict": "unchanged"}, 7, today)[0], "cadence elapsed -> due")
    check(pa.source_due({"last": "garbage", "verdict": "unchanged"}, 7, today)[0], "unparseable date -> due")
    sid = next(iter(CFG["sources"]))
    with contextlib.redirect_stdout(io.StringIO()):
        rc_mark = pa.main(["state", "mark", "--source", sid, "--verdict", "unchanged", "--date", "2026-09-13"])
    with contextlib.redirect_stderr(io.StringIO()):
        rc_nc = pa.main(["state", "mark", "--source", sid, "--verdict", "not-checked"])
    shown = io.StringIO()
    with contextlib.redirect_stdout(shown):
        pa.main(["state", "show", "--date", "2026-09-14"])
    check(rc_mark == 0 and rc_nc == 2, "mark accepts a real verdict, refuses not-checked")
    check(f"SOURCE_STATE={sid}|due=no|last=2026-09-13|last_verdict=unchanged|next_due=2026-09-20" in shown.getvalue(),
          "marked source shows fresh after the round trip")
    check(f"DUE={len(CFG['sources']) - 1}" in shown.getvalue(), "every other source still due")
    os.environ.pop("PROMPT_AUDIT_STATE_DIR", None)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

# ---- diff-source: all four verdicts (+ index, flagship and redirect semantics) ----

index_cfg = {"marker_kind": "llms-txt-lines", "role": "index",
             "marker_pattern": r"prompt-engineering/(prompting-[a-z0-9-]+)\.md",
             "baseline_marker": "prompting-a,prompting-b"}
index_text = "- [A](https://x/prompt-engineering/prompting-a.md)\n- [B](https://x/prompt-engineering/prompting-b.md)\n"
index_cfg["baseline_sha"] = pa.sha12(index_text.encode())
check(pa.diff_source(index_cfg, index_text.encode())["verdict"] == "unchanged", "index identical -> unchanged")
moved = pa.diff_source(index_cfg, (index_text + "- [Other](https://x/other.md)\n").encode())
check(moved["verdict"] == "unchanged" and "sha moved" in moved["reason"], "index sha moving without new guides stays unchanged")
extra = pa.diff_source(index_cfg, (index_text + "- [C](https://x/prompt-engineering/prompting-c.md)\n").encode())
check(extra["verdict"] == "new-guide" and "prompting-c" in extra["reason"], "extra guide line -> new-guide")
check(pa.diff_source(index_cfg, index_text.splitlines()[0].encode())["verdict"] == "changed", "guide line removed -> changed")

page_cfg = {"marker_kind": "none", "baseline_sha": pa.sha12(b"guide text"), "baseline_marker": "",
            "baseline_final_url": "https://x/guide.md"}
check(pa.diff_source(page_cfg, b"guide text")["verdict"] == "unchanged", "same bytes -> unchanged")
check(pa.diff_source(dict(page_cfg, baseline_sha="000000000000"), b"guide text")["verdict"] == "changed",
      "tampered baseline -> changed")
check(pa.diff_source(page_cfg, None)["verdict"] == "not-checked", "missing fetched file -> not-checked")
check(pa.diff_source(page_cfg, b"")["verdict"] == "not-checked", "empty fetched file -> not-checked, never unchanged")
check(pa.diff_source(page_cfg, b"guide text", "https://y/elsewhere.md")["verdict"] == "changed",
      "moved redirect target -> changed")

flag_text = "---\nlatestInfo:\n  model: next-model\n---\n# Guide\n"
flag_cfg = {"marker_kind": "latest-model-frontmatter", "marker_key": "model",
            "baseline_sha": pa.sha12(flag_text.encode()), "baseline_marker": "old-model"}
check(pa.diff_source(flag_cfg, flag_text.encode())["verdict"] == "new-guide", "flagship marker change -> new-guide")
check(pa.extract_marker("model-list", "current models, including A 5.1, B 4.5, and C 5. The page", {})
      == "A 5.1, B 4.5, and C 5", "model-list marker keeps version dots")

cli = io.StringIO()
with contextlib.redirect_stdout(cli):
    pa.main(["diff-source", "--id", sid, "--file", str(REPO / "no-such-fetch.md")])
check(cli.getvalue().startswith(f"VERDICT=not-checked|id={sid}|sha=unmeasured|"), "diff-source CLI line format")

# ---- ledger plan / merge / round trip ----

rub = "a" * 64
led = {"rubric": rub, "run_at": "2026-09-12", "files": {"r/CLAUDE.md": "111111111111", "r/AGENTS.md": "222222222222"}}
cur = {"r/CLAUDE.md": "111111111111", "r/AGENTS.md": "999999999999", "r/new.md": "333333333333", "r/gone.md": "unmeasured"}
plan = pa.plan_scan(cur, led, rub)
check(plan == {"r/CLAUDE.md": ("skip", "unchanged"), "r/AGENTS.md": ("scan", "changed"),
               "r/new.md": ("scan", "new"), "r/gone.md": ("unmeasured", "unreadable")}, f"plan reasons (got {plan})")
check({v[0] for k, v in pa.plan_scan(cur, led, "b" * 64).items() if k != "r/gone.md"} == {"scan"},
      "a rules.md edit (new rubric) forces a full rescan")
check(pa.plan_scan(cur, {"rubric": None, "files": {}}, rub)["r/CLAUDE.md"] == ("scan", "no-ledger"), "no ledger -> scan")
check(pa.plan_scan(cur, led, rub, rescan_all=True)["r/CLAUDE.md"] == ("scan", "rescan-all"), "--rescan-all -> scan")
check(pa.rules_rubric(b"# r\r\nline\r\n") == pa.rules_rubric(b"# r\nline\n") != pa.rules_rubric(b"# r\nline2\n"),
      "rubric ignores CRLF/LF checkout differences but not content")
merged, dropped = pa.merge_ledger(led, {"r/AGENTS.md": "999999999999"}, "b" * 64)
check(merged == {"r/AGENTS.md": "999999999999"} and dropped == 0, "entries under an old rubric are not carried forward")
merged, _ = pa.merge_ledger(led, {"r/AGENTS.md": "999999999999"}, rub)
check(merged == {"r/CLAUDE.md": "111111111111", "r/AGENTS.md": "999999999999"}, "same rubric keeps prior entries")
body = pa.render_ledger_body(merged, rub, "2026-09-13", CFG["sources"])
back = pa.parse_ledger(body)
check(back == {"rubric": rub, "run_at": "2026-09-13", "files": merged}, f"ledger body round-trips (got {back})")
check(pa.parse_ledger("no block here") == {"rubric": None, "run_at": None, "files": {}}, "absent block -> empty ledger")

# ---- digest: honest partition ----

run = {
    "date": "2026-09-13", "rubric": rub, "scan_ran": True,
    "sources": ["VERDICT=unchanged|id=s1|sha=x|marker=none|reason=identical",
                "VERDICT=not-checked|id=s2|sha=unmeasured|marker=unmeasured|reason=fetch failed"],
    "plan": ["PLAN=r/a.md|action=scan|reason=new|sha=1", "PLAN=r/b.md|action=scan|reason=new|sha=2",
             "PLAN=r/c.md|action=skip|reason=unchanged|sha=3", "PLAN=r/d.md|action=scan|reason=new|sha=4"],
    "judgments": {"r/a.md": [{"rule": "R-01", "verdict": "violation", "line": 3, "text": "MUST", "note": "caps"},
                             {"rule": "R-18", "verdict": "compliant"}],
                  "r/b.md": [], "r/d.md": None},
}
md, status = pa.render_digest(run, RULES)
check(status == "partial", "an unjudged planned file makes the run partial")
check("`guides=not-checked`" in md and "`s2` **not-checked** — fetch failed" in md, "not-checked source surfaces with its reason")
check("scanned 2, skipped 1 (unchanged), unmeasured 1" in md, f"scan partition counts (got {md!r})")
check("- `r/d.md`" in md.split("### Unmeasured", 1)[-1], "unmeasured file listed as unmeasured")
check("Skipped — unchanged" in md and "- `r/c.md`" in md, "skipped file listed as skipped")
check("**Findings in scanned files:** 1 violation, 0 consider, across 1 files — skipped files keep" in md,
      "compliant verdicts are not findings, and a run with skips never reads as a fleet total")
shared_run = {"date": "d", "rubric": rub, "scan_ran": True, "sources": [],
              "plan": ["PLAN=a/CLAUDE.md|action=scan|reason=new|sha=1", "PLAN=b/CLAUDE.md|action=scan|reason=new|sha=2"],
              "judgments": {k: [{"rule": "R-26", "verdict": v, "line": 5, "text": "## Streamlit conventions for apps"}]
                            for k, v in (("a/CLAUDE.md", "consider"), ("b/CLAUDE.md", "violation"))}}
smd, _ = pa.render_digest(shared_run, RULES, master_text="## Streamlit conventions for apps\n")
check("- **R-26** violation — `## Streamlit conventions for apps` — propagate to: a, b" in smd,
      f"a shared line is listed once, with its strongest verdict (got {smd!r})")
part_run = {"date": "d", "rubric": rub, "scan_ran": True, "sources": [],
            "plan": ["PLAN=r/a.md|action=scan|reason=new|sha=aaaaaaaaaaaa", "PLAN=r/b.md|action=scan|reason=new|sha=bbbbbbbbbbbb",
                     "PLAN=r/c.md|action=scan|reason=new|sha=cccccccccccc", "PLAN=r/d.md|action=skip|reason=unchanged|sha=dddddddddddd"],
            "judgments": {"r/a.md": [], "r/b.md": [{"rule": "R-18", "verdict": "unmeasured", "note": "file too long to finish"}],
                          "r/c.md": None}}
check(pa.partition_run(part_run)["recorded"] == {"r/a.md": "aaaaaaaaaaaa"},
      "only a file judged with no unmeasured rule is recorded; unjudged, partly judged and skipped files are not")
pmd, pstatus = pa.render_digest(part_run, RULES)
check(pstatus == "partial" and "rule verdicts not established 1" in pmd and "- `r/b.md` **R-18** — file too long" in pmd,
      "a per-rule unmeasured verdict is listed and makes the run partial, never dropped as compliant")
upd, ustatus = pa.render_digest({"date": "d", "scan_ran": False, "update_issue": "#900", "rubric": rub,
                                 "sources": ["VERDICT=changed|id=s1|sha=y|marker=none|reason=sha x -> y"]}, RULES)
check(ustatus == "complete" and "**Scan:** not run — rule-set update issue #900" in upd and "`guides=changed`" in upd,
      "a run whose scan did not run says so and names the update issue")
check("<!-- prompt-audit-digest run=d status=complete scan=not-run guides=changed update-issue=#900 -->" in upd,
      f"a not-run scan stamps scan=not-run for delivery_check.py to refuse (got {upd[:200]!r})")
check("<!-- prompt-audit-digest run=2026-09-13 status=partial scan=posted guides=not-checked update-issue=none -->" in md,
      "scan mode stamps status and scan=posted near the top")
dmd, _ = pa.render_digest(dict(run, dry_run=True), RULES)
check("scan=dry-run" in dmd and "scan=posted" not in dmd, "a dry run never stamps scan=posted")
check(all(ord(c) < 128 for c in upd.splitlines()[1]), "the stamp line is pure ASCII")
nmd, nstatus = pa.render_digest({"date": "d", "scan_ran": True, "rubric": rub,
                                 "sources": ["VERDICT=not-checked|id=s1|sha=unmeasured|marker=unmeasured|reason=fetch failed"],
                                 "plan": ["PLAN=r/a.md|action=scan|reason=new|sha=aaaaaaaaaaaa"],
                                 "judgments": {"r/a.md": []}}, RULES)
check(nstatus == "complete" and "`guides=not-checked`" in nmd
      and "<!-- prompt-audit-digest run=d status=complete scan=posted guides=not-checked update-issue=none -->" in nmd,
      "every source not-checked still scans and stamps a delivered scan (#834)")

# ---- scan anyway: a changed guide files the update issue and the scan still runs (#1132) ----

SKILL_CHANGED = "VERDICT=changed|id=anthropic-skill-authoring|sha=y|marker=none|reason=sha x -> y"
prov = pa.provisional_rules(RULES, CFG["sources"], [SKILL_CHANGED, "VERDICT=unchanged|id=anthropic-memory|sha=x|marker=none|reason=identical"])
check({"R-11", "R-14", "R-15"} <= prov and "R-01" not in prov and "R-33" not in prov,
      f"a rule is provisional when its Source: line cites a changed source; the appendix after R-33 never leaks into it (got {sorted(prov)})")
check(pa.provisional_rules(RULES, CFG["sources"], [SKILL_CHANGED.replace("VERDICT=changed", "VERDICT=not-checked")]) == set()
      and pa.provisional_rules(RULES, CFG["sources"], []) == set(),
      "a not-checked or absent verdict makes nothing provisional")
check("R-14" in pa.provisional_rules(RULES, CFG["sources"], ["VERDICT=new-guide|id=anthropic-memory|sha=y|marker=none|reason=r"]),
      "a new-guide verdict counts as changed, matching a Source: URL without the .md suffix")
both_run = {"date": "d", "scan_ran": True, "update_issue": "#900", "rubric": rub, "sources": [SKILL_CHANGED],
            "plan": ["PLAN=r/.claude/skills/a/SKILL.md|action=scan|reason=new|sha=aaaaaaaaaaaa"],
            "judgments": {"r/.claude/skills/a/SKILL.md": [
                {"rule": "R-15", "verdict": "violation", "line": 3, "text": "description: I do x", "note": "first person"},
                {"rule": "R-01", "verdict": "violation", "line": 5, "text": "MUST", "note": "caps"}]}}
bmd, bstatus = pa.render_digest(both_run, RULES, provisional=prov)
check(bstatus == "complete"
      and "<!-- prompt-audit-digest run=d status=complete scan=posted guides=changed update-issue=#900 -->" in bmd,
      f"a changed guide still scans: stamp carries the posted scan, guides=changed and the update issue (got {bmd[:300]!r})")
r15 = next(l for l in bmd.splitlines() if "**R-15**" in l)
r01 = next(l for l in bmd.splitlines() if "**R-01**" in l)
check("provisional" in r15 and "provisional" not in r01,
      f"only a finding whose rule cites a changed source is marked provisional (got {r15!r} / {r01!r})")
check("#900" in bmd and "R-15" in bmd.split("**Findings in scanned files:**", 1)[0],
      "the digest names the update issue and lists the provisional rules before the findings")
check("_(provisional)_" not in pa.render_digest(both_run, RULES)[0],
      "no provisional set, no provisional marks")
bping = pa.render_ping(both_run, "https://github.com/o/r/issues/882#issuecomment-1", prov)
check(bping.endswith("2 violation (1 provisional), 0 consider - rule-set update issue #900 - ledger https://github.com/o/r/issues/882#issuecomment-1"),
      f"the ping of a scan with changed guides names the update issue and the provisional count (got {bping!r})")

# ---- chat ping: one ASCII line for a delivered run, counts shared with the digest (#831) ----

URL = "https://github.com/o/r/issues/882#issuecomment-1"
ping_run = dict(run, judgments=dict(run["judgments"], **{"r/b.md": [{"rule": "R-13", "verdict": "consider", "line": 2}]}))
ping = pa.render_ping(ping_run, URL)
check(ping == f"prompt-audit 2026-09-13 - status=partial - guides=not-checked - scanned 2, skipped 1, unmeasured 1"
             f" - 1 violation, 1 consider - ledger {URL}", f"scan-mode ping line (got {ping!r})")
pmd_ping, _ = pa.render_digest(ping_run, RULES)
check("scanned 2, skipped 1 (unchanged), unmeasured 1" in pmd_ping and "1 violation, 1 consider" in pmd_ping,
      "the ping and the digest report the same counts")
uping = pa.render_ping({"date": "d", "scan_ran": False, "update_issue": "#900",
                       "sources": ["VERDICT=changed|id=s1|sha=y|marker=none|reason=sha x -> y"]}, URL)
check(uping == f"prompt-audit d - status=complete - guides=changed - scan not run, rule-set update issue #900 - ledger {URL}",
      f"update-mode ping names the update issue instead of scan counts (got {uping!r})")
check(all(ord(c) < 128 for c in pa.render_ping(ping_run, URL + "·")), "the ping line is pure ASCII (#507)")

ping_tmp = Path(tempfile.mkdtemp(prefix="prompt-audit-ping-"))
try:
    run_file = write(ping_tmp / "run.json", json.dumps(ping_run))
    dry_file = write(ping_tmp / "dry.json", json.dumps(dict(ping_run, dry_run=True)))
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc_ping = pa.main(["ping", "--run", str(run_file), "--comment-url", URL])
        rc_dry = pa.main(["ping", "--run", str(dry_file), "--comment-url", URL])
    check(rc_ping == 0 and out.getvalue() == ping + "\n", "ping CLI prints exactly the rendered line")
    check(rc_dry == 2 and "dry run" in err.getvalue(), "ping CLI refuses a dry run, so a dry run sends nothing")
finally:
    shutil.rmtree(ping_tmp, ignore_errors=True)

skill_md = (SKILL / "SKILL.md").read_text(encoding="utf-8")
writes = next(l for l in skill_md.splitlines() if l.startswith("- **Writes are exactly these:**"))
check("chat ping" in writes and "delivered run only" in writes, "the ping is listed among the skill's writes")
step10 = skill_md.split("### 10.", 1)[-1]
check("ping --run" in step10 and "notify_send.py --category log" in step10, "step 10 pipes the helper line to notify_send")
check(all(m in step10 for m in ("PING=sent", "PING=not-delivered", "PING=not-sent")),
      "a failed or skipped ping is reported as its own state, never as sent")
check("never prints `SCHEDULED-RUN-FAILED`" in step10, "a failed ping never flips a delivered run to failed")

# ---- prompt-drift issues: routing, tiers, living-backlog merge (fleet-config#833) ----

MASTER = "# Scaffold\n\n## Streamlit conventions for every app\n\n- Keep secrets in the env file always.\n"
SKILL_TXT = ('---\nname: s\ndescription: Does a thing, e.g. "/s".\n---\n\n# s\n\n'
             "<!-- map:mermaid:start -->\nClaude diagram\n<!-- map:mermaid:end -->\n\nRoute Claude requests here.\n")
drift_findings = [
    {"path": "project-scaffolding/CLAUDE.md", "rule": "R-26", "verdict": "violation", "line": 3,
     "text": "## Streamlit conventions for every app", "note": "procedure inline"},
    {"path": "alpha/CLAUDE.md", "rule": "R-26", "verdict": "consider", "line": 9,
     "text": "## Streamlit conventions for every app", "note": "inherited"},
    {"path": "beta/CLAUDE.md", "rule": "R-26", "verdict": "violation", "line": 12,
     "text": "## Streamlit conventions for every app", "note": "inherited"},
    {"path": "alpha/CLAUDE.md", "rule": "R-17", "verdict": "violation", "line": 3,
     "text": "Route Claude requests through the hub.", "note": "one vendor named"},
    {"path": "alpha/CLAUDE.md", "rule": "R-13", "verdict": "consider", "line": 5,
     "text": "Never fork a local copy.", "note": "negative framing"},
    {"path": "gamma/.claude/skills/s/SKILL.md", "rule": "R-17", "verdict": "violation", "line": 9,
     "text": "Claude diagram", "note": "inside the marked block"},
    {"path": "gamma/.claude/skills/s/SKILL.md", "rule": "R-15", "verdict": "violation", "line": 3,
     "text": 'description: Does a thing, e.g. "/s".', "note": "prose"},
    {"path": "fleet-config/global-CLAUDE.md", "rule": "R-03", "verdict": "violation", "line": 4,
     "text": "Always double-check the result.", "note": "forced re-check"},
]
texts = {"gamma/.claude/skills/s/SKILL.md": SKILL_TXT, "alpha/CLAUDE.md": "# a\n"}
per_repo = pa.drift_items(drift_findings, RULES, texts, MASTER, "")
check(sorted(per_repo) == ["alpha", "fleet-config", "gamma", "project-scaffolding"],
      f"violations route to their repo; considers alone file nothing (got {sorted(per_repo)})")
shared = per_repo["project-scaffolding"]
check(len(shared) == 1 and shared[0]["propagate"] == ["alpha", "beta"] and shared[0]["line"] == 3,
      f"a line shared with the master is filed once there, propagating to every sister copy (got {shared})")
check("beta" not in per_repo and all(i["rule"] != "R-26" for i in per_repo["alpha"]),
      "neither sister's issue lists the shared line")
check([i["rule"] for i in per_repo["alpha"]] == ["R-17"], "a consider never becomes a cleanup item")
check(shared[0]["tier"] == "hard" and per_repo["fleet-config"][0]["tier"] == "hard",
      "the scaffolding master and the global file are hard tier whatever the rule's own tier")
check(per_repo["alpha"][0]["tier"] == "easy", "an easy rule in an ordinary file stays easy")
gamma = {i["rule"]: i["tier"] for i in per_repo["gamma"]}
check(gamma == {"R-17": "hard", "R-15": "easy"},
      f"a fix inside a marked block is hard; description prose around the triggers stays easy (got {gamma})")
check(pa.in_protected_span(SKILL_TXT, 2, "R-17") and not pa.in_protected_span(SKILL_TXT, 14, "R-17"),
      "frontmatter lines are protected for every rule but the description-prose one")

rub12 = "c" * 64
body1, c1 = pa.merge_drift("", per_repo["alpha"], {"alpha/CLAUDE.md"}, set(), RULES, "2026-09-13", rub12)
check(c1["new"] == 1 and c1["tier"] == "easy" and c1["open"] == 1, f"first run creates the item (got {c1})")
check("**Tier (`/cleanup-fleet` prompt-drift rule): easy** — 1 open item(s): 1 easy, 0 hard." in body1,
      "the body states the issue's tier for the cleanup scorer")
check(pa.parse_drift_body(body1)[0][0]["rule"] == "R-17", "an item round-trips through its hidden identity")
check(".." not in pa.render_drift_item(dict(per_repo["alpha"][0], note="ends in a period."), RULES, "d"),
      "a note that already ends in a period is not doubled")
ticked = body1.replace("- [ ] **`CLAUDE.md:3`**", "- [x] **`CLAUDE.md:3`**")
moved = [dict(per_repo["alpha"][0], line=7)]
body2, c2 = pa.merge_drift(ticked, moved, {"alpha/CLAUDE.md"}, set(), RULES, "2026-09-20", rub12)
check(body2.count("R-17") == body1.count("R-17") and "- [x] **`CLAUDE.md:3`**" in body2 and c2["new"] == 0,
      "a second run merges into the same item and preserves the tick (no duplicate)")
check(c2["tier"] == "none" and len(pa.parse_drift_body(body2)[1]) == 2, "the run log gains one line per run")
body3, c3 = pa.merge_drift(body1, moved, {"alpha/CLAUDE.md"}, set(), RULES, "2026-09-20", rub12)
check("**`CLAUDE.md:7`**" in body3 and c3["matched"] == 1, "an unticked re-matched item refreshes its line number")
body4, c4 = pa.merge_drift(body1, [], set(), set(), RULES, "2026-09-20", rub12)
check(c4["kept"] == 1 and c4["not_resurfaced"] == 0 and "not re-surfaced 2026" not in body4,
      "an item for a file not rescanned (unchanged) is kept as it is")
body5, c5 = pa.merge_drift(body1, [], {"alpha/CLAUDE.md"}, set(), RULES, "2026-09-20", rub12)
check(c5["not_resurfaced"] == 1 and "- [ ] " in body5 and "not re-surfaced 2026-09-20" in body5 and c5["tier"] == "none",
      "a finding gone from a rescanned file is tagged, never ticked or deleted, and no longer counts as open")
body6, c6 = pa.merge_drift(body1, [], {"alpha/CLAUDE.md"}, {("alpha/CLAUDE.md", "R-17")}, RULES, "2026-09-20", rub12)
check(c6["kept"] == 1 and "not re-surfaced 2026" not in body6, "an item whose rule was unmeasured this run is kept, not tagged")

prov_repo = pa.drift_items(drift_findings, RULES, texts, MASTER, "", provisional={"R-15"})
gamma_items = {i["rule"]: i for i in prov_repo["gamma"]}
check(gamma_items["R-15"]["provisional"] and not gamma_items["R-17"]["provisional"]
      and [i["id"] for i in prov_repo["gamma"]] == [i["id"] for i in per_repo["gamma"]],
      "drift items carry the provisional flag without changing their identity (#1132)")
prov_line = pa.render_drift_item(gamma_items["R-15"], RULES, "d")
plain_line = pa.render_drift_item(gamma_items["R-17"], RULES, "d")
check("Provisional:" in prov_line and "Provisional:" not in plain_line,
      f"a provisional violation is marked in the prompt-drift body; others are not (got {prov_line!r})")
check(pa.parse_drift_body(prov_line)[0][0]["rule"] == "R-15", "a provisional item still round-trips through its hidden identity")
sbody, _ = pa.merge_drift("", shared, {"project-scaffolding/CLAUDE.md", "alpha/CLAUDE.md", "beta/CLAUDE.md"},
                          set(), RULES, "2026-09-13", rub12)
narrowed = [dict(shared[0], propagate=["alpha"])]
sbody2, _ = pa.merge_drift(sbody, narrowed, {"project-scaffolding/CLAUDE.md", "alpha/CLAUDE.md"}, set(),
                           RULES, "2026-09-20", rub12)
check("Propagate to: alpha, beta." in sbody2, "a sister not rescanned this run stays on the propagate list")
check(pa.DRIFT_KIND in __import__("audit_issue").KINDS, "prompt-drift is a managed kind")

# ---- skill structure (#1126): fence length, R-15 frontmatter, R-34..R-37, skill-ref inventory ----

# A four-backtick fence holds three-backtick lines; a toggle-on-every-fence parser lints its middle.
NESTED_FENCE = "# N\n\n````md\n```bash\nCRITICAL inside the example\n```\n````\n\nKeep it short.\n"
check(pa.lint_entry(entry_for(NESTED_FENCE), RULES, AUD).hits == [],
      "a three-backtick line inside a four-backtick fence does not close it (fence length tracked)")


def skill_text(body: str, name: str = "good", desc: str = "Audits the thing. E.g. \"/good\".") -> str:
    return f"---\nname: {name}\ndescription: {desc}\n---\n\n# s\n\n{body}\n"


def skill_hits(text: str, rule: str, vocab=None) -> list:
    res = pa.lint_entry(entry_for(text, kind="skill", key="x/.claude/skills/s/SKILL.md"), RULES, AUD, mcp_vocab=vocab)
    return by_rule(res, rule)


check(len(skill_hits(skill_text("Body.", name="Bad_Name"), "R-15")) == 1, "R-15: a name outside [a-z0-9-] is a hit")
check(len(skill_hits(skill_text("Body.", name="a" * 65), "R-15")) == 1, "R-15: a name over 64 chars is a hit")
check(len(skill_hits(skill_text("Body.", desc="Audits <example>things</example>."), "R-15")) == 1,
      "R-15: an XML tag in the description is a hit")
check(skill_hits(skill_text("Body.", name="good-name-2"), "R-15") == [], "R-15: a valid name and plain description pass")

R36 = skill_text("Run scripts\\helper.py next.\nOpen E:\\automation\\fleet-config\\docs now.\n"
                 "Use `tools\\lint.py` too.\nUse scripts/helper.py and snake\\_case.\n"
                 "Run `& .\\.venv\\Scripts\\python.exe -m x`.\n\n```bat\ncd /d E:\\automation\\x\n```")
r36 = skill_hits(R36, "R-36")
check(sorted((h.line, h.cap) for h in r36) == [(8, "violation"), (9, "consider"), (10, "violation"), (12, "consider")],
      f"R-36: relative backslash path -> violation, drive or venv path -> consider, fence/forward/escape ignored (got {[(h.line, h.cap) for h in r36]})")
check(skill_hits(skill_text("Use scripts/helper.py."), "R-36") == [], "R-36: forward-slash paths pass")

vocab = pa.mcp_vocabulary(["Load `mcp__web__navigate` and `mcp__web__read_page`.", "`mcp__dev__computer_` prefix"])
check(sorted(vocab) == ["navigate", "read_page"], f"R-37 vocabulary: qualified names only, prefix forms skipped (got {sorted(vocab)})")
R37 = skill_text("Call `navigate` first.\nThen `mcp__web__read_page` (`read_page`).\nUse `other_tool`.\n\n```\n`navigate`\n```")
r37 = skill_hits(R37, "R-37", vocab)
check([h.line for h in r37] == [8], f"R-37: a bare known tool name is a hit; a line carrying the qualified form, unknown names and fences are not (got {[h.line for h in r37]})")
check(skill_hits(R37, "R-37", {}) == [], "R-37: an empty vocabulary flags nothing")

tmp2 = Path(tempfile.mkdtemp(prefix="prompt-audit-refs-"))
try:
    f2 = tmp2 / "automation"
    sdir = f2 / "repo-c" / ".claude" / "skills" / "s1"
    write(sdir / "SKILL.md", skill_text(
        "See [the reference](reference.md) and `docs/shared.md`.\n"
        "Delegate to [s2](../s2/SKILL.md). Cases: [c](evals/case/prompt.md), [t](conversations/t.md).\n"
        "Not there: `missing.md`. Repo file: [readme](../../../CLAUDE.md)."))
    write(sdir / "reference.md", "# Ref\n\nSee [leaf](leaf.md) and [shared](../../../docs/shared.md).\n"
          "Mentions `other.md` without linking it.\n" + "x\n" * 120)
    write(sdir / "other.md", "# Other\n")
    write(sdir / "leaf.md", "# Leaf\n")
    write(sdir / "evals" / "case" / "prompt.md", "# case\n")
    write(sdir / "conversations" / "t.md", "# t\n")
    write(f2 / "repo-c" / "docs" / "shared.md", "# Shared\n\n## Contents\n\n- one\n" + "y\n" * 120)
    write(f2 / "repo-c" / ".claude" / "skills" / "s2" / "SKILL.md", skill_text("See [ref2](ref2.md)."))
    write(f2 / "repo-c" / ".claude" / "skills" / "s2" / "ref2.md", "# Ref2\n\nSee [s1](../s1/SKILL.md).\n")
    write(f2 / "repo-c" / "CLAUDE.md", CLEAN)
    proj2 = write(tmp2 / "projects.toml", f'[repo-c]\ncwd_prefix = "{(f2 / "repo-c").as_posix()}"\n')
    ents = pa.inventory(pa.fleet_repos(proj2), AUD)
    refs = [e.key for e in ents if e.kind == "skill-ref"]
    check(refs == ["repo-c/.claude/skills/s1/reference.md", "repo-c/docs/shared.md", "repo-c/.claude/skills/s2/ref2.md"],
          f"skill-ref: linked and backticked references in order; delegation, evals/, conversations/, missing and CLAUDE.md excluded (got {refs})")
    by_key = {e.key: e for e in ents}
    s1 = pa.lint_entry(by_key["repo-c/.claude/skills/s1/SKILL.md"], RULES, AUD)
    r34 = by_rule(s1, "R-34")
    check(len(r34) == 1 and r34[0].line == 8 and r34[0].count == 1 and "leaf.md" in r34[0].text,
          f"R-34: a reference that links a leaf SKILL.md does not reference is a hit at the SKILL.md line (got {[(h.line, h.text) for h in r34]})")
    check(by_rule(pa.lint_entry(by_key["repo-c/.claude/skills/s2/SKILL.md"], RULES, AUD), "R-34") == [],
          "R-34: a reference that only delegates to another skill's SKILL.md is not nesting")
    r35 = by_rule(pa.lint_entry(by_key["repo-c/.claude/skills/s1/reference.md"], RULES, AUD), "R-35")
    check(len(r35) == 1, "R-35: a reference over 100 lines with no contents heading is a hit")
    check(by_rule(pa.lint_entry(by_key["repo-c/docs/shared.md"], RULES, AUD), "R-35") == [],
          "R-35: a long reference with a Contents heading passes")
    check(by_rule(pa.lint_entry(by_key["repo-c/.claude/skills/s2/ref2.md"], RULES, AUD), "R-35") == [],
          "R-35: a short reference passes")
    check(not any(h.rule in ("R-01", "R-13", "R-14") for e in ents if e.kind == "skill-ref"
                  for h in pa.lint_entry(e, RULES, AUD).hits),
          "skill-ref files are linted only by rules scoped to them")
finally:
    shutil.rmtree(tmp2, ignore_errors=True)

# ---- skill structure, judgment half (#1127): R-39 and R-43 lint assists, consider-only guard ----

LONG_FLOW = skill_text("## Steps\n\n" + "".join(f"### {i}. Step {i}\n\nDo thing {i}.\n\n" for i in range(1, 8)))
r39 = skill_hits(LONG_FLOW, "R-39")
check(len(r39) == 1 and r39[0].count == 7 and r39[0].line == 10,
      f"R-39 assist: seven numbered step headings and no checklist is a candidate at the first step (got {[(h.line, h.count) for h in r39]})")
check(skill_hits(LONG_FLOW + "\n```\n- [ ] 1. Step 1\n- [ ] 2. Step 2\n```\n", "R-39") == [],
      "R-39 assist: a copyable checklist (fenced counts) clears it")
check(skill_hits(skill_text("".join(f"{i}. Do thing {i}.\n" for i in range(1, 6))), "R-39") == [],
      "R-39 assist: a five-item numbered list is under the threshold")
check(len(skill_hits(skill_text("".join(f"{i}. Do thing {i}.\n" for i in range(1, 8))), "R-39")) == 1,
      "R-39 assist: a seven-item top-level numbered list counts when there are no step headings")
check(RULES["R-39"]["consider_only"] and RULES["R-41"]["consider_only"] and not RULES["R-38"]["consider_only"],
      "R-39 and R-41 carry the consider-only first-cycle flag; the others do not")
check(all(h.cap == "consider" for h in pa.lint_entry(entry_for(LONG_FLOW, kind="skill", audience="claude",
                                                             key="x/.claude/skills/s/SKILL.md"), RULES, AUD).hits
          if h.rule == "R-39"),
      "a consider-only rule caps at consider even for a vendor-scoped reader")

tmp3 = Path(tempfile.mkdtemp(prefix="prompt-audit-r43-"))
try:
    repo3 = tmp3 / "repo-d"
    sk = repo3 / ".claude" / "skills" / "s3"
    write(sk / "helper.py", "import json\nimport requests\nimport yaml\nfrom . import sibling\nimport localmod\n")
    write(sk / "localmod.py", "X = 1\n")
    write(sk / "clean.py", "import json\nimport localmod\n")
    write(repo3 / "tests" / "test_x.py", "import pytest\n")
    body43 = "Run `helper.py` to fetch.\nRun `clean.py` to tidy.\nNeeds the yaml package. Tests: `tests/test_x.py`.\n"
    write(sk / "SKILL.md", skill_text(body43))
    e43 = pa.Entry(key="repo-d/.claude/skills/s3/SKILL.md", path=sk / "SKILL.md", repo="repo-d", kind="skill",
                   data=(sk / "SKILL.md").read_bytes(), repo_dir=repo3)
    r43 = by_rule(pa.lint_entry(e43, RULES, AUD), "R-43")
    check(len(r43) == 1 and r43[0].line == 8 and r43[0].count == 1 and "requests" in r43[0].text,
          f"R-43 assist: a third-party import SKILL.md never names is a candidate; stdlib, local, relative and named imports are not (got {[(h.line, h.text) for h in r43]})")
finally:
    shutil.rmtree(tmp3, ignore_errors=True)

# ---- coverage check (#1128): page cache, fence-aware sections, uncovered set, honesty, dedup ----

COV_PAGE = """# Guide

## Core

### Alpha rule

Text.

### Beta rule

````markdown
### Not a section
```bash
### Still not a section
```
## Nor this
````

### Gamma rule

- [ ] A checklist line

## Lone leaf
"""
leaves, checklist = pa.page_sections(COV_PAGE)
check(leaves == ["Alpha rule", "Beta rule", "Gamma rule", "Lone leaf"],
      f"coverage parser: headings inside a four-backtick fence holding a three-backtick fence are not sections (got {leaves})")
check(checklist == ["A checklist line"], f"coverage parser: checklist lines are read (got {checklist})")

COV_RULES = """### R-90 Fixture        tags: [shared] [file: any] [tier: easy]
Detect: lint — x
Source: https://example.test/guide (Alpha rule; Core → Gamma rule)

- appendix
Source: https://example.test/guide · https://other.test/page (Beta rule)
"""
COV_SRC = {"url": "https://example.test/guide.md", "role": "instruction-files"}
cov = pa.coverage("guide", COV_SRC, COV_PAGE, COV_RULES)
check(cov["status"] == "checked" and cov["uncovered"] == ["Beta rule", "Lone leaf"] and cov["new"] == cov["uncovered"],
      f"coverage: exactly the sections no Source line cites, another page's citation does not count (got {cov})")
check(len(cov["unsectioned_sources"]) == 1 and "· https://other.test/page" in cov["unsectioned_sources"][0],
      "coverage: a Source line citing the page with no section is named")
check(pa.digest_line(cov).startswith("coverage: guide uncovered 2 (new 2)"), f"coverage digest line (got {pa.digest_line(cov)})")

nc = pa.coverage("guide", COV_SRC, None, COV_RULES)
check(nc["status"] == "not-checked" and pa.digest_line(nc) == "coverage: guide not-checked",
      "coverage: an unreadable page is not-checked, never uncovered 0")
check(pa.coverage("idx", {"role": "index"}, "## A\n", COV_RULES)["status"] == "not-applicable",
      "coverage: an index page is not applicable")

posted = pa.coverage_comment([cov])
check(posted is not None and posted.count("prompt-audit-coverage: id=") == 2, "coverage: new sections make one comment")
again = pa.coverage("guide", COV_SRC, COV_PAGE, COV_RULES, pa.known_items(posted))
check(again["new"] == [] and again["uncovered"] == cov["uncovered"] and pa.coverage_comment([again]) is None,
      "coverage dedup: a re-run with the same uncovered set adds no second comment")

cov_run = {"date": "2026-10-04", "dry_run": False, "sources": ["VERDICT=unchanged|id=guide|sha=x|marker=|reason=identical"],
           "update_issue": None, "scan_ran": True, "plan": [], "judgments": {}, "coverage": [cov]}
cov_body, cov_status = pa.render_digest(cov_run, RULES)
check(cov_status == "complete" and "guides=unchanged" in cov_body and "Rule-set update:" not in cov_body
      and "coverage: guide uncovered 2" in cov_body,
      "coverage: uncovered sections never put the run into update mode or stop the scan")
check(pa.provisional_rules(RULES, {"guide": COV_SRC}, cov_run["sources"]) == set(),
      "coverage: uncovered sections mark no rule provisional")
check("coverage: not-checked" in pa.render_digest(dict(cov_run, coverage=None), RULES)[0],
      "coverage: a run that never ran the check prints not-checked")

cache_dir = Path(tempfile.mkdtemp(prefix="prompt-audit-cache-"))
try:
    os.environ["PROMPT_AUDIT_STATE_DIR"] = str(cache_dir)
    page_file = write(cache_dir / "fetched" / "x.md", "")
    page_file.write_bytes(b"## A\r\nbytes \xe2\x80\x94 verbatim\n")
    sid0 = next(iter(CFG["sources"]))
    with contextlib.redirect_stdout(io.StringIO()):
        pa.main(["diff-source", "--id", sid0, "--file", str(page_file), "--cache"])
    cached = cache_dir / "pages" / f"{sid0}.md"
    check(cached.is_file() and cached.read_bytes() == page_file.read_bytes(),
          "diff-source --cache keeps the fetched bytes verbatim in pages/<id>.md")
finally:
    os.environ.pop("PROMPT_AUDIT_STATE_DIR", None)
    shutil.rmtree(cache_dir, ignore_errors=True)

# ---- leads intake (#1129): parse, loud on malformed, traced never re-sent, state persists, unverified only reported ----

seed = pa.load_leads(CFG["sources"])
check(len(seed) == 1 and seed[0]["id"] == "skills-outdated-video" and len(seed[0]["claims"]) == 7,
      f"leads.toml carries the video lead with its 7 claims (got {[(l['id'], len(l['claims'])) for l in seed]})")
check(pa.resolve_leads(seed, {})["open"] == [], "every seeded claim is traced, so none is open")

leads_dir = Path(tempfile.mkdtemp(prefix="prompt-audit-leads-"))
try:
    bad_toml = write(leads_dir / "bad.toml", "[leads.x\nurl = 1\n")
    bad_schema = write(leads_dir / "schema.toml", '[leads.x]\nurl = "https://e.test"\nkind = "podcast"\nadded = 2026-10-02\nclaims = [{ text = "t" }]\n')
    bad_trace = write(leads_dir / "trace.toml", '[leads.x]\nurl = "https://e.test"\nkind = "post"\nadded = 2026-10-02\nclaims = [{ text = "t", trace = "nope#Sec" }]\n')
    for f, why in ((bad_toml, "invalid TOML"), (bad_schema, "unknown kind"), (bad_trace, "trace to an untracked source"),
                   (leads_dir / "missing.toml", "missing file")):
        try:
            pa.load_leads(CFG["sources"], f)
            raised = False
        except pa.LeadsError:
            raised = True
        check(raised, f"leads: {why} fails loudly with LeadsError, never reads as no leads")

    mixed = write(leads_dir / "mixed.toml", '[leads.y]\nurl = "https://e.test/p"\nkind = "post"\nadded = 2026-10-03\nclaims = [\n'
                  '  { text = "Already traced", trace = "anthropic-skill-authoring#Token budgets" },\n'
                  '  { text = "Needs tracing", trace = "" },\n'
                  '  { text = "Nobody says this", trace = "" },\n]\n')
    ml = pa.load_leads(CFG["sources"], mixed)
    ids = {c["text"]: c["id"] for c in ml[0]["claims"]}
    first = pa.resolve_leads(ml, {})
    check(first["open"] == [ids["Needs tracing"], ids["Nobody says this"]],
          "leads: only claims with an empty trace are open; a traced claim is never sent to the worker")
    st = pa.mark_lead({}, ids["Needs tracing"], "traced", "anthropic-best-practices#Tool usage", dt.date(2026, 10, 4))
    st = pa.mark_lead(st, ids["Nobody says this"], "unverified", "")
    state_dir2 = leads_dir / "state"
    os.environ["PROMPT_AUDIT_STATE_DIR"] = str(state_dir2)
    __import__("prompt_audit.state", fromlist=["save_state"]).save_state(st)
    again2 = pa.resolve_leads(ml, pa.load_state())
    check(again2["open"] == [], "leads: trace results persist in state.json and a second run re-traces nothing")
    os.environ.pop("PROMPT_AUDIT_STATE_DIR", None)

    lines = pa.lead_digest_lines(again2)
    check(any("**unverified**: Nobody says this" in l for l in lines) and "unverified 1" in lines[0],
          f"leads: an untraced claim is listed as unverified in the digest (got {lines})")
    lead_run = {"date": "2026-10-04", "dry_run": False, "sources": [], "update_issue": None, "scan_ran": True,
                "plan": [], "judgments": {}, "coverage": [], "leads": again2}
    lead_body, lead_status = pa.render_digest(lead_run, RULES)
    check(lead_status == "complete" and "**unverified**: Nobody says this" in lead_body
          and pa.partition_run(lead_run)["findings"] == [],
          "leads: an unverified claim creates no finding, so no rule and no prompt-drift item")
    check("**Leads:** not-checked" in pa.render_digest(dict(lead_run, leads=None), RULES)[0],
          "leads: a run without the leads step prints not-checked")
    sugg = pa.suggestions_comment(again2)
    check(sugg is not None and 'trace = "anthropic-best-practices#Tool usage"' in sugg and "Nobody says this" not in sugg,
          "leads: a traced result becomes a suggested trace value on the update issue; unverified does not")
    check(pa.suggestions_comment(again2, sugg) is None, "leads: a suggestion already on the issue is not posted again")
finally:
    os.environ.pop("PROMPT_AUDIT_STATE_DIR", None)
    shutil.rmtree(leads_dir, ignore_errors=True)

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

# ---- contracts: rules.md, sources.toml, vendor neutrality ----

check(sorted(r for r, v in RULES.items() if v["detect"] == "lint") == pa.LINT_RULES,
      "every audit.py lint rule is a `Detect: lint` rule in rules.md and vice versa")
check(all(v["detect"] in ("lint", "judgment") for v in RULES.values()), "every rule declares lint or judgment")
check(sorted(r for r, v in RULES.items() if v["assist"]) == pa.ASSIST_RULES,
      "every lint-assisted judgment rule in rules.md has a lint assist in audit.py and vice versa")
_page_sections = {"Avoid time-sensitive information", "Naming conventions", "Token budgets", "Writing effective descriptions",
                  "YAML frontmatter requirements", "Core quality", "Avoid deeply nested references",
                  "Structure longer reference files with table of contents", "Avoid Windows-style paths",
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

_h.report_and_exit("test_prompt_audit")
