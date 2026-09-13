"""Unit tests for .claude/skills/prompt-audit/audit.py (fleet-config#832).

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
import audit as pa  # noqa: E402

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
check(ustatus == "complete" and "not run — rule-set stale, see #900" in upd and "`guides=changed`" in upd,
      "update mode: complete, scan not run, points at the update issue")

# ---- contracts: rules.md, sources.toml, vendor neutrality ----

check(sorted(r for r, v in RULES.items() if v["detect"] == "lint") == pa.LINT_RULES,
      "every audit.py lint rule is a `Detect: lint` rule in rules.md and vice versa")
check(all(v["detect"] in ("lint", "judgment") for v in RULES.values()), "every rule declares lint or judgment")
vendors = {c["vendor"] for c in AUD.values()} | {"shared", "conflict"}
check(all(v["vendor"] in vendors and v["file"] in ("any", "claude-md", "skill") and v["tier"] in ("easy", "hard")
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
check(vendor_mentions((SKILL / "audit.py").read_text(encoding="utf-8")) == [], "no vendor or model name in audit.py")
prose = [l for l in pa.RULES_MD.read_text(encoding="utf-8").splitlines()
         if not l.startswith("Source:") and not l.startswith("### R-")]
prose = [re.sub(r"`?\[(?:anthropic|openai|shared|conflict)\]`?", "", l) for l in prose]
check([l for l in prose if VENDOR_WORDS.search(FILE_CONVENTIONS.sub("", l))] == [],
      "rules.md names vendors only inside tags and Source lines")
with open(SKILL / "sources.toml", "rb") as fh:
    check("audiences" in tomllib.load(fh), "sources.toml carries the audience vocabulary")

_h.report_and_exit("test_prompt_audit")
