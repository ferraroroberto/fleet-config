"""prompt-audit run delivery: scan-anyway on a changed guide, the one-line chat ping, and prompt-drift issue routing, tiers and the living-backlog merge.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/prompt_audit/test_runs_and_drift.py`  (also invoked by tests/run_acceptance.py)
"""
from __future__ import annotations

import contextlib
import io
import json
import re
import shutil
import tempfile
from pathlib import Path

from _suite import CFG, RULES, SKILL, _h, check, pa, rub, run, write  # noqa: E402


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

# #1477: a finding that vanished because the rubric changed (file bytes unchanged) is not "gone".
rub_old, rub_new = "c" * 64, "d" * 64
sha_a, sha_b = "a" * 12, "b" * 12
filed, _ = pa.merge_drift("", per_repo["alpha"], {"alpha/CLAUDE.md"}, set(), RULES, "2026-10-07", rub_old,
                          {"alpha/CLAUDE.md": sha_a})
rc_body, rc = pa.merge_drift(filed, [], {"alpha/CLAUDE.md"}, set(), RULES, "2026-10-15", rub_new,
                             {"alpha/CLAUDE.md": sha_a})
check(rc["rule_changed"] == 1 and rc["not_resurfaced"] == 0 and "rule changed 2026-10-15" in rc_body
      and "not re-surfaced 2026" not in rc_body and "tick it" not in rc_body and "- [ ] " in rc_body,
      f"rubric changed + file unchanged reads 'rule changed', never gone or tickable (got {rc})")
gone_body, gone = pa.merge_drift(filed, [], {"alpha/CLAUDE.md"}, set(), RULES, "2026-10-15", rub_new,
                                 {"alpha/CLAUDE.md": sha_b})
check(gone["not_resurfaced"] == 1 and gone["rule_changed"] == 0 and "not re-surfaced 2026-10-15" in gone_body,
      f"rubric changed + file changed and the finding is absent still reads gone (got {gone})")
legacy = re.sub(r" rubric=\S+ sha=\S+", "", filed)  # a pre-#1477 item: only the run log knows the old rubric
check("rubric=" not in legacy.split("## Audit run log")[0] and "rubric `cccccccccccc`" in legacy,
      "fixture: the legacy body has no per-item rubric, only the run-log line")
lg_body, lg = pa.merge_drift(legacy, [], {"alpha/CLAUDE.md"}, set(), RULES, "2026-10-15", rub_new)
check(lg["rule_changed"] == 1 and lg["not_resurfaced"] == 0 and "not re-surfaced 2026" not in lg_body,
      f"a legacy item whose run-log rubric differs and whose file sha is unknown reads 'rule changed' (got {lg})")
again, ag = pa.merge_drift(rc_body, [], {"alpha/CLAUDE.md"}, set(), RULES, "2026-10-22", "e" * 64,
                           {"alpha/CLAUDE.md": sha_a})
check(ag["rule_changed"] == 1 and again.count("_(rule changed") == 1, "a second rule change replaces the tag, never stacks it")
rematch, rm = pa.merge_drift(rc_body, per_repo["alpha"], {"alpha/CLAUDE.md"}, set(), RULES, "2026-10-22", rub_new,
                             {"alpha/CLAUDE.md": sha_a})
check(rm["matched"] == 1 and "_(rule changed" not in rematch, "a re-raised finding drops the rule-changed tag")

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

_h.report_and_exit("test_runs_and_drift")
