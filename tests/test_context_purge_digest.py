"""Pure-logic tests for .claude/skills/context-purge/digest.py (fleet-config#627).

Covers the rendering layer without touching `gh`, Telegram, or the filesystem
beyond a temp dir. The properties worth pinning are the ones a reviewer would
otherwise have to take on trust, and the ones the issue was filed about:

  * **unknown never becomes zero.** `"probe": null` ("not recorded") and
    `"probe": {"ran": false}` ("not probed") are different facts and must
    render differently, and neither may render as `0`. Same for token counts
    and inventory totals. This is the requirement the issue states outright.
  * **probe coverage is per file, not per repo.** A repo that probed its
    `CLAUDE.md` and skipped four `SKILL.md` files is not "probed" -- that gap
    (1 of 41 on 2026-08-15) is the number nobody had.
  * **a partial run says so and names who was missed**, in every rendering.
  * **repos rank by cost of a lost directive**, not alphabetically.
  * **validate() rejects run data that would make the digest lie** -- most
    importantly a `status`/`unreached` pair that contradicts itself.

Run: E:/automation/fleet-config/.venv/Scripts/python.exe tests/test_context_purge_digest.py
Exit 0 = all pass.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location(
    "context_purge_digest", REPO / ".claude" / "skills" / "context-purge" / "digest.py")
digest = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(digest)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check


def _file(path: str, action: str = "rewritten", **kw) -> dict:
    entry = {"path": path, "action": action}
    if action == "rewritten" and "probe" not in kw:
        kw["probe"] = None
    entry.update(kw)
    return entry


RUN = {
    "run_id": "20260815T010000",
    "mode": "fleet",
    "status": "complete",
    "gate": {"to_purge": 41, "unchanged": 12, "surface": 53},
    "unreached": [],
    "repos": [
        {
            "repo": "life-os", "status": "shipped", "pr": "https://x/pull/1",
            "risk": {"always_on": True, "shape_change": "large", "checked": False},
            "files": [
                _file("CLAUDE.md", tokens_before=4000, tokens_after=3000,
                      inventory_items=50, inventory_discharged=50,
                      probe={"ran": True, "questions": 14, "compressed": 13, "control": 13}),
                _file(".claude/skills/recap/SKILL.md", tokens_before=2000, tokens_after=1500,
                      inventory_items=20, inventory_discharged=20,
                      probe={"ran": False}),
                _file("docs/notes.md", action="assessed-lean", note="already lean"),
            ],
            "descriptions": [
                {"path": ".claude/skills/recap/SKILL.md", "words_before": 62,
                 "words_after": 44, "before": "long text", "after": "short text"},
            ],
            "decisions": [{"summary": "dropped sparring nuance", "where": "PR #1"}],
            "not_fixed": [{"summary": "context-audit defect", "reason": "code, out of scope"}],
        },
        {
            "repo": "photo-ocr", "status": "skipped", "pr": None,
            "risk": {"always_on": False, "shape_change": "small", "checked": True},
            "files": [_file("CLAUDE.md", action="untouched", note="nothing to cut")],
            "descriptions": [], "decisions": [], "not_fixed": [],
        },
    ],
}


def _clone(**overrides) -> dict:
    run = json.loads(json.dumps(RUN))
    run.update(overrides)
    return run


# ---- probe_state: the three-way split -------------------------------------

check(digest.probe_state({"probe": {"ran": True}}) == "probed", "probe_state: ran=True is probed")
check(digest.probe_state({"probe": {"ran": False}}) == "not-probed",
      "probe_state: ran=False is 'not probed' -- a real, deliberate zero")
check(digest.probe_state({"probe": None}) == "unknown",
      "probe_state: null is 'unknown' -- the run did not record it")
check(digest.probe_state({}) == "unknown",
      "probe_state: an absent probe key is unknown, never a silent pass")
check(digest.probe_state({"probe": "yes"}) == "unknown",
      "probe_state: a malformed probe is unknown rather than optimistically parsed")
check(digest.probe_state({"probe": {"ran": False}}) != digest.probe_state({"probe": None}),
      "probe_state: 'not probed' and 'not recorded' are DIFFERENT states (the issue's rule)")


# ---- headline: unknowns are counted separately, never summed as zero ------

h = digest.headline(RUN)
check(h["files_rewritten"] == 2, "headline: only rewritten files count as rewritten")
check(h["files_assessed"] == 4, "headline: assessed counts every file entry")
check(h["tokens_removed"] == 1500, "headline: tokens removed sums only recorded pairs (1000+500)")
check(h["probed"] == 1 and h["not_probed"] == 1 and h["probe_unknown"] == 0,
      "headline: probe states are partitioned, not collapsed")
check(h["inventory_walked"] == 70, "headline: inventory items sum across rewritten files")

unknown_run = _clone(repos=[{
    "repo": "r", "status": "shipped", "risk": {},
    "files": [_file("CLAUDE.md")],  # no tokens, no inventory, probe=None
    "descriptions": [], "decisions": [], "not_fixed": [],
}])
hu = digest.headline(unknown_run)
check(hu["tokens_removed"] == 0 and hu["tokens_unknown"] == 1,
      "headline: a file with no token record contributes 0 to the sum but is COUNTED "
      "as unknown -- the two must never be conflated")
check(hu["probe_unknown"] == 1 and hu["probed"] == 0,
      "headline: an unrecorded probe is unknown, not a zero-score probe")
check(hu["inventory_unknown"] == 1, "headline: unrecorded inventory is tracked separately")

check("not recorded" in digest.coverage_line(hu),
      "coverage: an unrecorded probe is spelled out, never rendered as 0/1")
check(digest.coverage_line(hu).startswith("0/1"),
      "coverage: the fraction is per file and honest about the numerator")
check(digest.coverage_line(digest.headline(_clone(repos=[]))) == "no files rewritten",
      "coverage: no rewritten files says so rather than dividing by zero")


# ---- risk ranking ----------------------------------------------------------

ranked = [r["repo"] for r in digest.ranked_repos(RUN)]
check(ranked == ["life-os", "photo-ocr"],
      "ranking: always-on + large + unverified outranks not-always-on + small + verified")
# Purpose-built so cost-order and alphabetical-order genuinely disagree: the
# original fixture happened to rank life-os before photo-ocr, which is also
# alphabetical, so it proved nothing about the ordering rule.
_rank_run = _clone(repos=[
    {"repo": "aaa-safe", "status": "shipped",
     "risk": {"always_on": False, "shape_change": "small", "checked": True},
     "files": [], "descriptions": [], "decisions": [], "not_fixed": []},
    {"repo": "zzz-risky", "status": "shipped",
     "risk": {"always_on": True, "shape_change": "large", "checked": False},
     "files": [], "descriptions": [], "decisions": [], "not_fixed": []},
])
_rank_order = [r["repo"] for r in digest.ranked_repos(_rank_run)]
check(_rank_order == ["zzz-risky", "aaa-safe"],
      "ranking: the riskiest repo leads even when that is the reverse of alphabetical")
check(_rank_order != sorted(_rank_order),
      "ranking: the order is by cost, and is demonstrably not alphabetical")

_score_unknown, label_unknown = digest.risk_score({"risk": {"checked": None}})
check(label_unknown == "unknown",
      "ranking: an unrecorded 'checked' reads as unknown, not as verified")
check(digest.risk_score({"risk": {"checked": None}})[0]
      == digest.risk_score({"risk": {"checked": False}})[0],
      "ranking: unknown verification is scored as risky as unverified -- it is not evidence")


# ---- validate --------------------------------------------------------------

check(digest.validate(RUN) == [], "validate: the well-formed run passes clean")
check(digest.validate("not a dict"), "validate: non-object run data is rejected")
check(any("run_id" in e for e in digest.validate(_clone(run_id=""))),
      "validate: a run with no id is rejected")
check(any("status" in e for e in digest.validate(_clone(status="done"))),
      "validate: an unknown status is rejected")
check(any("unreached" in e for e in digest.validate(_clone(status="partial"))),
      "validate: status=partial with nobody named is rejected -- a partial run must "
      "say which repos it did not reach")
check(any("contradictory" in e for e in
          digest.validate(_clone(unreached=[{"repo": "x", "reason": "y"}]))),
      "validate: status=complete alongside unreached repos is contradictory")

_bad_probe = _clone()
del _bad_probe["repos"][0]["files"][0]["probe"]
check(any("probe" in e for e in digest.validate(_bad_probe)),
      "validate: a rewritten file must carry a probe key -- silence about probing is "
      "the exact gap #627 was filed over")

_bad_action = _clone()
_bad_action["repos"][0]["files"][0]["action"] = "tweaked"
check(any("action" in e for e in digest.validate(_bad_action)),
      "validate: an unknown file action is rejected")


# ---- markdown --------------------------------------------------------------

md = digest.render_markdown(RUN)
check("20260815T010000" in md, "markdown: the run id is on the page")
check("| `life-os` | `CLAUDE.md` |" in md, "markdown: per-file rows are rendered")
check("not probed" in md, "markdown: an explicitly unprobed file says 'not probed'")
check("62 → 44" in md, "markdown: description word counts are before → after")
check("long text" in md and "short text" in md,
      "markdown: both the before and after description text are shown")
check("dropped sparring nuance" in md, "markdown: reviewer decisions are collected")
check("context-audit defect" in md and "out of scope" in md,
      "markdown: deliberately-not-fixed findings survive with their reason")
check("docs/notes.md" in md and "photo-ocr/CLAUDE.md" in md,
      "markdown: assessed-but-untouched files are listed so 'not in the diff' cannot "
      "read as 'not looked at'")

md_unknown = digest.render_markdown(unknown_run)
check("not recorded" in md_unknown,
      "markdown: unrecorded figures render as 'not recorded', never as 0")
_probe_section = md_unknown.split("### Probe coverage, per file")[1].split("###")[0]
check("not recorded" in _probe_section and "0/0" not in _probe_section,
      "markdown: an unrecorded file's row says 'not recorded' and invents no zero counts")

partial = _clone(status="partial",
                 unreached=[{"repo": "grocery", "reason": "gh auth failed"}])
md_p = digest.render_markdown(partial)
check("PARTIAL RUN" in md_p, "markdown: a partial run is banner-flagged")
check("grocery" in md_p and "gh auth failed" in md_p,
      "markdown: a partial run names the unreached repo AND why")

_pipe_run = _clone()
_pipe_run["repos"][0]["descriptions"][0]["before"] = "a | b"
check("a \\| b" in digest.render_markdown(_pipe_run),
      "markdown: a literal pipe in a description does not break the table")


# ---- chat -------------------------------------------------------------------

chat = digest.render_chat(RUN, link="https://example/comment")
check("1,500" in chat, "chat: the headline token figure is present")
check("https://example/comment" in chat, "chat: the link is carried")
check(len(chat.splitlines()) <= 8, "chat: the message stays phone-readable, not a wall")
check("🖐️" in chat, "chat: a waiting reviewer decision is flagged")

chat_p = digest.render_chat(partial)
check("PARTIAL" in chat_p and "grocery" in chat_p,
      "chat: a partial run is flagged and names who was missed")

_regressed = _clone()
_regressed["repos"][0]["files"][0]["probe"] = {"ran": True, "questions": 14,
                                               "compressed": 9, "control": 13}
check("REGRESSION" in digest.render_chat(_regressed),
      "chat: a probe regression is a hard flag, not buried in the page")
check(digest.probe_verdict(_regressed["repos"][0]["files"][0]) is False,
      "probe_verdict: compressed scoring below control is a failure")
check(digest.probe_verdict({"probe": {"ran": True}}) is None,
      "probe_verdict: a probe with no scores is unmeasured, not a pass")

chat_u = digest.render_chat(unknown_run)
check("no probe record" in chat_u,
      "chat: unrecorded probe coverage is surfaced as a warning, not hidden")


# ---- html ------------------------------------------------------------------

html = digest.render_html(RUN)
check("<title>" in html and "20260815T010000" in html, "html: titled with the run id")
check("prefers-color-scheme" in html and 'data-theme="dark"' in html,
      "html: theme-aware in all three theme states")
check("overflow-x:auto" in html.replace(" ", ""),
      "html: wide tables scroll inside their own container")
check("<script" not in html and "http://" not in html and "https://cdn" not in html,
      "html: self-contained -- no external fetches, which a strict CSP would block")
check("&lt;" in html or "life-os" in html, "html: content is escaped, not injected raw")

_xss = _clone()
_xss["repos"][0]["descriptions"][0]["before"] = "<img src=x onerror=alert(1)>"
check("<img src=x" not in digest.render_html(_xss),
      "html: run data is HTML-escaped -- a description is untrusted text")

html_p = digest.render_html(partial)
check("PARTIAL RUN" in html_p and "grocery" in html_p,
      "html: the partial banner names the unreached repos too")


# ---- open purge PR backlog (fleet-config#757) ------------------------------

md_no_backlog = digest.render_markdown(RUN)
check("not recorded" in md_no_backlog.lower() and "Open purge PR backlog" in md_no_backlog,
      "markdown: an omitted backlog renders as unknown, never as an empty/clean one")

BACKLOG = [
    {"repo": "automation", "number": 110, "title": "chore: compress",
     "url": "https://github.com/ferraroroberto/automation/pull/110",
     "created_at": "2026-08-22T06:10:51Z", "age_days": 14},
]
md_backlog = digest.render_markdown(RUN, backlog=BACKLOG)
check("automation" in md_backlog and "#110" in md_backlog and "14d" in md_backlog,
      "markdown: an open purge PR is listed with repo, number and age")

md_empty_backlog = digest.render_markdown(RUN, backlog=[])
check("No open" in md_empty_backlog,
      "markdown: a genuinely empty backlog (fetched, and empty) reads as clean, "
      "distinct from 'not recorded'")

chat_backlog = digest.render_chat(RUN, backlog=BACKLOG)
check("1 open purge PR" in chat_backlog, "chat: the open-PR count is surfaced")
chat_no_backlog = digest.render_chat(RUN, backlog=None)
check("backlog check failed" in chat_no_backlog,
      "chat: an unrecorded backlog is flagged, not silently omitted")

html_backlog = digest.render_html(RUN, backlog=BACKLOG)
check("automation" in html_backlog and "#110" in html_backlog,
      "html: the backlog table renders the open PR")


# ---- stamp -----------------------------------------------------------------

stamp = digest.render_stamp(RUN, "posted")
check("status=complete" in stamp and "unreached=0" in stamp and "delivery=posted" in stamp,
      "stamp: carries status, unreached count and delivery state")
check("run=20260815T010000" in stamp, "stamp: carries the run id")
stamp_p = digest.render_stamp(partial, "failed")
check("status=partial" in stamp_p and "unreached=1" in stamp_p and "delivery=failed" in stamp_p,
      "stamp: a partial run with a failed ping stamps both facts")

# The stamp the digest writes must be the stamp the post-condition reads.
sys.path.insert(0, str(REPO / "skills" / "_lib"))
import digest_delivery  # noqa: E402

check(digest_delivery.parse_stamp(stamp, digest.STAMP_PREFIX)["status"] == "complete",
      "stamp: digest.py's writer and digest_delivery.py's reader agree on the format")
check(digest_delivery.parse_stamp(stamp_p, digest.STAMP_PREFIX)["unreached"] == "1",
      "stamp: the unreached count round-trips through the post-condition's parser")


# ---- CLI -------------------------------------------------------------------

_tmp = Path(tempfile.mkdtemp(prefix="digest_cli_"))
try:
    run_path = _tmp / "run.json"
    run_path.write_text(json.dumps(RUN), encoding="utf-8")
    check(digest.main(["validate", str(run_path)]) == 0, "cli: validate accepts good run data")

    bad = _tmp / "bad.json"
    bad.write_text(json.dumps(_clone(status="partial")), encoding="utf-8")
    check(digest.main(["validate", str(bad)]) == 2,
          "cli: validate exits non-zero on run data that would make the digest lie")

    missing = _tmp / "nope.json"
    check(digest.main(["validate", str(missing)]) == 2,
          "cli: unreadable run data is an error, never an empty success")

    md_out, html_out, chat_out = _tmp / "d.md", _tmp / "d.html", _tmp / "d.txt"
    check(digest.main(["render", str(run_path), "--md", str(md_out),
                       "--html", str(html_out), "--chat-text", str(chat_out)]) == 0,
          "cli: render writes all three renderings")
    check(md_out.exists() and html_out.exists() and chat_out.exists(),
          "cli: every requested rendering actually lands on disk")
    check("20260815T010000" in md_out.read_text(encoding="utf-8"),
          "cli: the rendered markdown carries the run")

    check(digest.main(["render", str(run_path), "--md", str(md_out),
                       "--url", "https://artifact/x"]) == 0,
          "cli: an artifact URL is accepted")
    check("https://artifact/x" in md_out.read_text(encoding="utf-8"),
          "cli: the artifact URL is referenced from the durable copy")

    reconcile_out = _tmp / "reconcile.json"
    reconcile_out.write_text(json.dumps({"updates": {}, "backlog": BACKLOG}), encoding="utf-8")
    check(digest.main(["render", str(run_path), "--md", str(md_out),
                       "--reconcile-json", str(reconcile_out)]) == 0,
          "cli: --reconcile-json is accepted")
    check("automation" in md_out.read_text(encoding="utf-8"),
          "cli: the reconcile file's backlog reaches the rendered markdown")
finally:
    shutil.rmtree(_tmp, ignore_errors=True)


_h.report_and_exit("test_context_purge_digest")
