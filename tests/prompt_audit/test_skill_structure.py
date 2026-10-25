"""prompt-audit skill structure: fence length, frontmatter and skill-ref rules, the judgment-half lint assists, the coverage check, and leads intake.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/prompt_audit/test_skill_structure.py`  (also invoked by tests/run_acceptance.py)
"""
from __future__ import annotations

import contextlib
import datetime as dt
import io
import os
import shutil
import tempfile
from pathlib import Path

from _suite import AUD, CFG, CLEAN, RULES, _h, by_rule, check, entry_for, pa, write  # noqa: E402


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
check(sorted(r for r, v in RULES.items() if v["consider_only"]) == ["R-45", "R-46", "R-47", "R-48"],
      "only R-45..R-48 carry the consider-only first-cycle flag (R-39 and R-41 became normal rules on 2026-10-12)")
CONSIDER_RULES = {**RULES, "R-39": {**RULES["R-39"], "consider_only": True}}
r39_hits = [h for h in pa.lint_entry(entry_for(LONG_FLOW, kind="skill", audience="claude",
                                               key="x/.claude/skills/s/SKILL.md"), CONSIDER_RULES, AUD).hits
            if h.rule == "R-39"]
check(r39_hits and all(h.cap == "consider" for h in r39_hits),
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
paren = pa.coverage("guide", COV_SRC, "## Personality (Friendly vs Pragmatic)\n\n## Tail\n",
                    "Source: https://example.test/guide (Tail; Personality (Friendly vs Pragmatic))\n")
check(paren["uncovered"] == [], f"coverage: a cited section title holding parentheses counts as cited (got {paren['uncovered']})")

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

_h.report_and_exit("test_skill_structure")
