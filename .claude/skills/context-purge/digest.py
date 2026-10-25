"""Per-run digest for `/context-purge` — render, publish, ping (fleet-config#627).

A `/context-purge fleet` run used to end as 15 open draft PRs and a job log:
no single artifact said what the run did, and reviewing it meant opening every
PR body and reconciling the numbers by hand. Doing that once, for the
2026-08-15 run, surfaced two facts no individual PR made visible -- that every
`CLAUDE.md` in the sweep was behaviourally probed but only 1 of 41 `SKILL.md`
files was, and that two of the run's own reports disagreed by one on the
over-cap count.

**The digest renders the run's own data, never the PR bodies.** Parsing prose
the run just wrote would be a second place for the numbers to disagree. Each
per-repo worker emits one `run/<repo>.json`; the orchestrator concatenates
them; the PR bodies and this digest are two renderings of that one structure.

**Unknown is a first-class state.** `"probe": null` ("not recorded") and
`"probe": {"ran": false}` ("not probed") are different facts and must never
render the same, and neither may render as `0`. Probe coverage is reported
per *file*, not per repo -- a repo that probed its `CLAUDE.md` and skipped four
`SKILL.md` files is not "probed", and the per-file breakdown is what makes that
gap visible.

Three outputs from one structure:

  markdown -- the canonical, durable copy, posted as a comment on the managed
              ledger issue. This is the link the Telegram message can always
              carry: an Artifact publish is unproven from a headless run
              (see `/fleet-health`'s SKILL.md), so it may never be the only one.
  html     -- a self-contained page, published best-effort as a private
              Artifact because it reads better on a phone.
  chat     -- a short text digest: the three headline figures, any hard flag,
              and the link.

Every published comment carries a machine-readable stamp:

    <!-- context-purge-digest run=<id> status=complete|partial unreached=N delivery=posted|failed|unknown -->

`delivery_check.py` reads it, so a partial run and a silently failed chat post
are both caught by the same post-condition rather than exiting 0 having half
delivered.

**Naming note (fleet-config#740):** this field and the CLI flags below used to
be named after Slack, the transport #540 replaced with Telegram. The rename is
not backward-compatible on purpose -- an older comment's `slack=` field simply
reads as `delivery=unknown` (unestablished, never a false pass), and every
such comment ages out of `delivery_check.py`'s freshness window within one
weekly cycle, so no transition-window shim was worth carrying.

The run model (probe states, merge outcomes, `validate`) lives in
`digest_model.py`; the page stylesheet in `digest_style.py`.

stdlib only. CLI:
    digest.py validate <run.json>
    digest.py render   <run.json> --html P --md P --chat-text P [--url U]
                       [--reconcile-json P]
    digest.py publish  <run.json> --md P [--delivery-state posted|failed|unknown]
    digest.py merge-check <repo.json>   # exit 0 = may merge, 2 = blocked

**The run merges its own validated rewrites** (fleet-config#1312), so the digest
is the review surface: per file the token and line delta and who loads it
(`blast_radius`), per repo the merged PR/SHA with its one-line `git revert`, and
whether the live checkout was fast-forwarded. `merge_blockers` is the single
go/no-go -- `merge-check` runs it before `gh pr merge` and `validate` re-applies
it to every repo recorded as merged, so an unvalidated rewrite cannot be merged
and still publish a clean digest.

`--reconcile-json` (fleet-config#757) points at `gate.py reconcile --json`'s
output -- a standing fact about open `chore/context-purge-*` PRs, not run
data, so it is supplied at render time rather than carried in `run.json`.
Omitted (or unreadable), the "Open purge PR backlog" section renders `None`
as *not recorded*, never as an empty/clean backlog.
"""

from __future__ import annotations

import argparse
import html as _html
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional

SKILL_DIR = Path(__file__).resolve().parent
REPO_ROOT = SKILL_DIR.parents[2]

sys.path.insert(0, str(REPO_ROOT / "skills" / "_lib"))
# Its own folder too: a caller that loads this file by path still finds the sibling modules.
sys.path.insert(0, str(SKILL_DIR))
import git_run  # noqa: E402
from audit_ledger.audit_issue_client import run_audit_issue  # noqa: E402
from fleet_core.utf8_stdio import ensure_utf8_stdio  # noqa: E402

from digest_model import (  # noqa: E402
    all_descriptions,
    blast_radius,
    collect,
    coverage_line,
    headline,
    _lines,
    merge_blockers,
    _merge_cell,
    merge_record,
    merge_state,
    merge_summary,
    _num,
    _primary_cell,
    probe_state,
    probe_verdict,
    ranked_repos,
    _revert,
    rewritten_files,
    risk_score,
    untouched_files,
    validate,
)
from digest_style import PAGE_CSS  # noqa: E402

AUDIT_ISSUE = REPO_ROOT / "skills" / "_lib" / "audit_issue.py"
LEDGER_REPO = "ferraroroberto/fleet-config"
KIND = "context-purge-digest"
# Reads unmistakably as a ledger rather than as open work: it sits permanently
# open in a backlog where every other issue is actionable.
TITLE = "[ledger] context-purge run digests (machine-managed, not actionable)"
PRIMARY_LABEL = "chore"
# ...and `audit-meta` on top, because that is what makes `/issue-start`'s pick
# mode and `/issue-triage` skip it. The title tells a human; the label tells the
# tooling.
LEDGER_LABEL = "audit-meta"
STAMP_PREFIX = "context-purge-digest"

# ---- stamp ------------------------------------------------------------------

def render_stamp(run: dict, delivery_state: str) -> str:
    return (f"<!-- {STAMP_PREFIX} run={run.get('run_id', 'unknown')} "
            f"status={run.get('status', 'unknown')} "
            f"unreached={len(run.get('unreached', []))} "
            f"delivery={delivery_state} -->")


# ---- markdown ---------------------------------------------------------------

def _delta(f: dict) -> str:
    before, after = _num(f.get("tokens_before")), _num(f.get("tokens_after"))
    if before is None or after is None:
        return "not recorded"
    pct = 0.0 if before == 0 else (before - after) * 100.0 / before
    return f"{before:,} to {after:,} (-{before - after:,}, {pct:.0f}%)"


def _probe_cell(f: dict) -> str:
    state = probe_state(f)
    if state == "unknown":
        return "not recorded"
    if state == "not-probed":
        return "not probed"
    probe = f.get("probe") or {}
    compressed, control = _num(probe.get("compressed")), _num(probe.get("control"))
    questions = _num(probe.get("questions"))
    if compressed is None or control is None:
        return f"probed ({questions or '?'} q, scores not recorded)"
    verdict = "pass" if compressed >= control else "REGRESSION"
    return f"{compressed}/{control} vs control, {questions or '?'} q -- {verdict}"


def merge_line(s: dict) -> str:
    """`9 of 10 repos` plus every term that is not a clean merge, spelled out."""
    line = f"{s['merged']} of {s['candidates']} repo(s)"
    if s["not_merged"]:
        line += f", {s['not_merged']} NOT merged"
    if s["unknown"]:
        line += f", {s['unknown']} merge not recorded"
    if s["primaries_left"]:
        line += f"; {s['primaries_left']} live checkout(s) left unsynced"
    if s["primaries_unknown"]:
        line += f"; {s['primaries_unknown']} checkout sync not recorded"
    return line


def _applied_lines(run: dict) -> list[str]:
    """What the run changed on `main`: per file, then per repo (fleet-config#1312).

    The run merges its own validated rewrites, so nothing waits on a reviewer;
    this section is what makes the after-the-fact review possible -- what
    changed, how much, who loads it, and the one command that undoes it.
    """
    lines = ["### What changed", "",
             "The run merges its own validated rewrites; review here, revert with one command.", ""]
    lines.append("| Repo | File | Tokens | Lines | Blast radius |")
    lines.append("|---|---|---|---|---|")
    pairs = rewritten_files(run)
    for repo, f in pairs:
        name, path = repo.get("repo", "?"), f.get("path", "?")
        lines.append(f"| `{name}` | `{path}` | {_delta(f)} | {_lines(f)} | "
                     f"{blast_radius(name, path)} |")
    if not pairs:
        lines.append("| — | — | — | — | no files rewritten |")
    lines.append("")
    lines.append("| Repo | Merge | PR | Revert | Live checkout |")
    lines.append("|---|---|---|---|---|")
    for repo in ranked_repos(run):
        if repo.get("status") == "skipped":
            continue
        pr = repo.get("pr") or "none"
        lines.append(f"| `{repo.get('repo','?')}` | {_merge_cell(repo)} | {pr} | "
                     f"{_revert(repo)} | {_primary_cell(repo)} |")
    lines.append("")
    return lines


def _backlog_lines(backlog: "list[dict] | None") -> list[str]:
    """Markdown lines for the standing open-purge-PR backlog (fleet-config#757).

    A draft PR nobody promotes silently suppresses its files from every
    future gate forever — this section is what makes that backlog visible
    somewhere a human reads weekly, whether or not this run rewrote anything.
    `backlog` is `None` when the render wasn't given a live PR check to draw
    on (no `--reconcile-json`, or that check itself failed) — treated as
    unknown, never as an empty/clean backlog.
    """
    lines = ["### Open purge PR backlog", ""]
    if backlog is None:
        lines.append("Not recorded this run — treat as unknown, not clean.")
    elif not backlog:
        lines.append("No open `chore/context-purge-*` PRs.")
    else:
        lines.append("A draft PR nobody promotes hides its files from every future gate — merge or close it.")
        lines.append("")
        lines.append("| Repo | PR | Age | Title |")
        lines.append("|---|---|---|---|")
        for b in sorted(backlog, key=lambda b: -(b.get("age_days") or 0)):
            age = "unknown" if b.get("age_days") is None else f"{b['age_days']}d"
            lines.append(f"| `{b.get('repo','?')}` | [#{b.get('number','?')}]({b.get('url','')}) | "
                         f"{age} | {b.get('title','?')} |")
    lines.append("")
    return lines


def render_markdown(run: dict, artifact_url: Optional[str] = None,
                     backlog: "list[dict] | None" = None) -> str:
    h = headline(run)
    partial = run.get("status") == "partial"
    lines: list[str] = []

    lines.append(f"## `/context-purge` run digest — `{run.get('run_id', 'unknown')}`")
    lines.append("")

    if partial:
        names = ", ".join(f"`{u.get('repo', '?')}`" for u in run.get("unreached", []))
        lines.append(f"> **PARTIAL RUN.** {h['unreached']} repo(s) were not reached: {names}. "
                     f"Every figure below covers only the repos that were.")
        lines.append("")
        for u in run.get("unreached", []):
            lines.append(f"- `{u.get('repo', '?')}` — {u.get('reason', 'reason not recorded')}")
        lines.append("")

    lines.append("### Headline")
    lines.append("")
    lines.append(f"- **Est. tokens removed:** {h['tokens_removed']:,}"
                 + (f" (+{h['tokens_unknown']} file(s) with no token record)"
                    if h["tokens_unknown"] else ""))
    lines.append(f"- **Files rewritten:** {h['files_rewritten']} of {h['files_assessed']} assessed, "
                 f"across {h['repos']} repo(s)")
    lines.append(f"- **Directive-inventory items walked:** {h['inventory_walked']:,}"
                 + (f" ({h['inventory_unknown']} file(s) not recorded)"
                    if h["inventory_unknown"] else ""))
    lines.append(f"- **Probe coverage:** {coverage_line(h)}")
    lines.append(f"- **Merged to main:** {merge_line(merge_summary(run))}")
    if artifact_url:
        lines.append(f"- **Full page:** {artifact_url}")
    lines.append("")

    lines.extend(_applied_lines(run))
    lines.extend(_backlog_lines(backlog))

    lines.append("### Probe coverage, per file")
    lines.append("")
    lines.append("Per *file*, not per repo — a repo that probed its `CLAUDE.md` and skipped four `SKILL.md` files is not \"probed\".")
    lines.append("")
    lines.append("| Repo | File | Tokens | Inventory | Probe |")
    lines.append("|---|---|---|---|---|")
    for repo, f in rewritten_files(run):
        items, discharged = _num(f.get("inventory_items")), _num(f.get("inventory_discharged"))
        inv = "not recorded" if items is None or discharged is None else f"{discharged}/{items}"
        lines.append(f"| `{repo.get('repo','?')}` | `{f.get('path','?')}` | {_delta(f)} | {inv} | {_probe_cell(f)} |")
    if not rewritten_files(run):
        lines.append("| — | — | — | — | no files rewritten |")
    lines.append("")

    lines.extend(_md_description_lines(run))

    lines.append("### Repos by cost of a lost directive")
    lines.append("")
    lines.append("| Repo | Always-on | Shape change | Verified | Result |")
    lines.append("|---|---|---|---|---|")
    for repo in ranked_repos(run):
        risk = repo.get("risk") or {}
        _score, checked = risk_score(repo)
        always = "yes" if risk.get("always_on") is True else (
            "no" if risk.get("always_on") is False else "not recorded")
        shape = risk.get("shape_change") or "not recorded"
        pr = repo.get("pr")
        result = f"{repo.get('status','?')}" + (f" — {pr}" if pr else "")
        lines.append(f"| `{repo.get('repo','?')}` | {always} | {shape} | {checked} | {result} |")
    lines.append("")

    lines.extend(_md_review_lines(run))
    return "\n".join(lines) + "\n"


def _md_description_lines(run: dict) -> list[str]:
    """Rewritten skill descriptions, before and after: the highest-risk edit the purge makes."""
    lines: list[str] = []
    descriptions = all_descriptions(run)
    lines.append("### Rewritten skill descriptions")
    lines.append("")
    lines.append("Always-on in every session and they decide whether a skill fires — the highest-risk edit the purge makes.")
    lines.append("")
    if descriptions:
        lines.append("| Repo | File | Words | Before → after |")
        lines.append("|---|---|---|---|")
        for d in descriptions:
            wb, wa = _num(d.get("words_before")), _num(d.get("words_after"))
            words = "not recorded" if wb is None or wa is None else f"{wb} → {wa}"
            before = (d.get("before") or "").replace("|", "\\|")
            after = (d.get("after") or "").replace("|", "\\|")
            lines.append(f"| `{d['repo']}` | `{d.get('path','?')}` | {words} | "
                         f"**before:** {before}<br>**after:** {after} |")
    else:
        lines.append("No skill descriptions were rewritten this run.")
    lines.append("")
    return lines


def _md_review_lines(run: dict) -> list[str]:
    """Reviewer decisions, findings deliberately left, and files assessed but untouched."""
    lines: list[str] = []
    decisions = collect(run, "decisions")
    lines.append("### Reviewer decisions asked for")
    lines.append("")
    if decisions:
        for d in decisions:
            where = f" ({d['where']})" if d.get("where") else ""
            lines.append(f"- **`{d['repo']}`**{where} — {d.get('summary','?')}"
                         + (f" {d['detail']}" if d.get("detail") else ""))
    else:
        lines.append("None raised.")
    lines.append("")

    not_fixed = collect(run, "not_fixed")
    lines.append("### Found and deliberately not fixed")
    lines.append("")
    if not_fixed:
        for n in not_fixed:
            lines.append(f"- **`{n['repo']}`** — {n.get('summary','?')} "
                         f"_(reason: {n.get('reason','not recorded')})_")
    else:
        lines.append("Nothing was found and left.")
    lines.append("")

    untouched = untouched_files(run)
    lines.append("### Assessed and left untouched")
    lines.append("")
    lines.append("Listed so \"not in the diff\" cannot read as \"not looked at\".")
    lines.append("")
    if untouched:
        for f in untouched:
            note = f" — {f['note']}" if f.get("note") else ""
            lines.append(f"- `{f['repo']}/{f.get('path','?')}` ({f.get('action')}){note}")
    else:
        lines.append("Every assessed file was rewritten.")
    lines.append("")
    return lines


# ---- chat --------------------------------------------------------------------

def render_chat(run: dict, link: Optional[str] = None,
                 backlog: "list[dict] | None" = None) -> str:
    h = headline(run)
    partial = run.get("status") == "partial"
    head = "⚠️ PARTIAL" if partial else "🧹"
    lines = [f"{head} `/context-purge` run `{run.get('run_id','unknown')}` — "
             f"{h['tokens_removed']:,} est. tokens removed, "
             f"{h['files_rewritten']} file(s) rewritten across {h['repos']} repo(s)",
             f"Probe coverage: {coverage_line(h)}",
             f"Merged to main: {merge_line(merge_summary(run))}"]
    not_merged = [f"{r.get('repo')} ({(merge_record(r) or {}).get('reason', 'no reason recorded')})"
                  for r in run.get("repos", []) if merge_state(r) == "not-merged"]
    if not_merged:
        lines.append(f"❌ NOT merged: {'; '.join(not_merged)}")
    if partial:
        names = ", ".join(u.get("repo", "?") for u in run.get("unreached", []))
        lines.append(f"Not reached: {names}")
    if h["probe_unknown"]:
        lines.append(f"⚠️ {h['probe_unknown']} rewritten file(s) have no probe record — "
                     f"treat their compression as unverified")
    regressions = [f"{repo.get('repo')}/{f.get('path')}"
                   for repo, f in rewritten_files(run) if probe_verdict(f) is False]
    if regressions:
        lines.append(f"❌ probe REGRESSION in: {', '.join(regressions)}")
    decisions = collect(run, "decisions")
    if decisions:
        lines.append(f"🖐️ {len(decisions)} reviewer decision(s) waiting")
    if backlog is None:
        lines.append("⚠️ open-purge-PR backlog check failed — could not confirm none are stuck")
    elif backlog:
        stale = [b for b in backlog if (b.get("age_days") or 0) >= 7]
        lines.append(f"🗄️ {len(backlog)} open purge PR(s)"
                     + (f", {len(stale)} older than a week" if stale else ""))
    if link:
        lines.append(link)
    return "\n".join(lines)


# ---- html -------------------------------------------------------------------

def _cells(row: list[str], tag: str = "td") -> str:
    return "".join(f"<{tag}>{c}</{tag}>" for c in row)


def _bullets(items: list[dict], fmt) -> str:
    return "".join(f"<li>{fmt(i)}</li>" for i in items) or "<li>none</li>"


def _html_backlog(backlog: "list[dict] | None") -> str:
    esc = _html.escape
    if backlog is None:
        return "<p>Not recorded this run — treat as unknown, not clean.</p>"
    if not backlog:
        return "<p>No open <code>chore/context-purge-*</code> PRs.</p>"
    backlog_rows = "".join(
        "<tr>" + _cells([
            f"<code>{esc(b.get('repo','?'))}</code>",
            f"<a href='{esc(b.get('url',''))}'>#{esc(str(b.get('number','?')))}</a>",
            "unknown" if b.get("age_days") is None else f"{b['age_days']}d",
            esc(b.get("title", "?")),
        ]) + "</tr>"
        for b in sorted(backlog, key=lambda b: -(b.get("age_days") or 0))
    )
    return (f"<div class='scroll'><table>"
            f"<tr><th>Repo</th><th>PR</th><th>Age</th><th>Title</th></tr>"
            f"{backlog_rows}</table></div>")


def _html_rows(run: dict) -> dict:
    """The body rows of every per-file and per-repo table on the page."""
    esc = _html.escape
    rows: dict = {}
    rows["file_rows"] = "".join(
        "<tr>" + _cells([
            f"<code>{esc(repo.get('repo','?'))}</code>",
            f"<code>{esc(f.get('path','?'))}</code>",
            esc(_delta(f)),
            esc(_probe_cell(f)),
        ]) + "</tr>"
        for repo, f in rewritten_files(run)
    ) or "<tr><td colspan='4'>no files rewritten</td></tr>"

    rows["desc_rows"] = "".join(
        "<tr>" + _cells([
            f"<code>{esc(d['repo'])}</code>",
            f"<code>{esc(d.get('path','?'))}</code>",
            esc(f"{d.get('words_before','?')} → {d.get('words_after','?')}"),
            f"<div class='was'>{esc(d.get('before',''))}</div>"
            f"<div class='now'>{esc(d.get('after',''))}</div>",
        ]) + "</tr>"
        for d in all_descriptions(run)
    ) or "<tr><td colspan='4'>none rewritten</td></tr>"

    rows["repo_rows"] = "".join(
        "<tr>" + _cells([
            f"<code>{esc(r.get('repo','?'))}</code>",
            esc(str((r.get('risk') or {}).get('always_on', 'not recorded'))),
            esc(str((r.get('risk') or {}).get('shape_change', 'not recorded'))),
            esc(risk_score(r)[1]),
            esc(r.get('status', '?')),
        ]) + "</tr>"
        for r in ranked_repos(run)
    )

    rows["change_rows"] = "".join(
        "<tr>" + _cells([
            f"<code>{esc(repo.get('repo','?'))}</code>",
            f"<code>{esc(f.get('path','?'))}</code>",
            esc(_delta(f)),
            esc(_lines(f)),
            esc(blast_radius(repo.get("repo", "?"), f.get("path", "?"))),
        ]) + "</tr>"
        for repo, f in rewritten_files(run)
    ) or "<tr><td colspan='5'>no files rewritten</td></tr>"

    rows["merge_rows"] = "".join(
        "<tr>" + _cells([
            f"<code>{esc(r.get('repo','?'))}</code>",
            esc(_merge_cell(r)),
            esc(str(r.get("pr") or "none")),
            esc(_revert(r)),
            esc(_primary_cell(r)),
        ]) + "</tr>"
        for r in ranked_repos(run) if r.get("status") != "skipped"
    )
    return rows


def render_html(run: dict, backlog: "list[dict] | None" = None) -> str:
    h = headline(run)
    esc = _html.escape
    partial = run.get("status") == "partial"
    backlog_html = _html_backlog(backlog)
    rows = _html_rows(run)

    banner = ""
    if partial:
        names = ", ".join(esc(u.get("repo", "?")) for u in run.get("unreached", []))
        banner = (f"<div class='banner'><strong>PARTIAL RUN.</strong> "
                  f"{h['unreached']} repo(s) not reached: {names}. "
                  f"Every figure below covers only the repos that were.</div>")

    return f"""<title>context-purge {esc(str(run.get('run_id','')))}</title>
<style>
{PAGE_CSS}</style>
<main>
<h1>/context-purge run digest</h1>
<p class="sub"><code>{esc(str(run.get('run_id','unknown')))}</code> ·
 {esc(str(run.get('mode','')))} · status {esc(str(run.get('status','unknown')))}</p>
{banner}
<div class="grid">
  <div class="stat"><div class="n">{h['tokens_removed']:,}</div><div class="l">est. tokens removed</div></div>
  <div class="stat"><div class="n">{h['files_rewritten']}</div><div class="l">files rewritten of {h['files_assessed']} assessed</div></div>
  <div class="stat"><div class="n">{h['inventory_walked']:,}</div><div class="l">inventory items walked</div></div>
  <div class="stat"><div class="n">{h['probed']}/{h['files_rewritten']}</div><div class="l">probed{f" · {h['probe_unknown']} not recorded" if h['probe_unknown'] else ""}</div></div>
</div>

<h2>What changed</h2>
<p>Merged to main: {esc(merge_line(merge_summary(run)))}</p>
<div class="scroll"><table>
<tr><th>Repo</th><th>File</th><th>Tokens</th><th>Lines</th><th>Blast radius</th></tr>
{rows["change_rows"]}
</table></div>
<div class="scroll"><table>
<tr><th>Repo</th><th>Merge</th><th>PR</th><th>Revert</th><th>Live checkout</th></tr>
{rows["merge_rows"]}
</table></div>

<h2>Open purge PR backlog</h2>
{backlog_html}

<h2>Probe coverage, per file</h2>
<div class="scroll"><table>
<tr><th>Repo</th><th>File</th><th>Tokens</th><th>Probe</th></tr>
{rows["file_rows"]}
</table></div>

<h2>Rewritten skill descriptions</h2>
<div class="scroll"><table>
<tr><th>Repo</th><th>File</th><th>Words</th><th>Before / after</th></tr>
{rows["desc_rows"]}
</table></div>

<h2>Repos by cost of a lost directive</h2>
<div class="scroll"><table>
<tr><th>Repo</th><th>Always-on</th><th>Shape change</th><th>Verified</th><th>Result</th></tr>
{rows["repo_rows"]}
</table></div>

<h2>Reviewer decisions asked for</h2>
<ul>{_bullets(collect(run, 'decisions'),
             lambda d: f"<code>{esc(d['repo'])}</code> — {esc(d.get('summary',''))}")}</ul>

<h2>Found and deliberately not fixed</h2>
<ul>{_bullets(collect(run, 'not_fixed'),
             lambda n: f"<code>{esc(n['repo'])}</code> — {esc(n.get('summary',''))} "
                       f"<em>({esc(n.get('reason','not recorded'))})</em>")}</ul>

<h2>Assessed and left untouched</h2>
<ul>{_bullets(untouched_files(run),
             lambda f: f"<code>{esc(f['repo'])}/{esc(f.get('path',''))}</code> "
                       f"({esc(f.get('action',''))})")}</ul>
</main>
"""


# ---- publish ----------------------------------------------------------------

def _audit_issue(*args: str) -> str:
    return run_audit_issue(AUDIT_ISSUE, *args)


def _gh(args: list[str]) -> str:
    res = git_run.run_gh(args, timeout=git_run.GH_LIST_TIMEOUT_S)
    if res.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)} failed: {(res.stderr or res.stdout).strip()}")
    return (res.stdout or "").strip()


def ensure_ledger() -> int:
    """The managed ledger issue number, creating it on first use."""
    body = (
        "Machine-managed ledger for `/context-purge` run digests — **not actionable work**. "
        "Each run posts one comment here; `delivery_check.py` asserts a fresh one exists, so this "
        "issue stays permanently open by design. Managed by "
        "`.claude/skills/context-purge/digest.py` (fleet-config#627).\n"
    )
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as fh:
        fh.write(body)
        tmp = fh.name
    try:
        _audit_issue("upsert", "--repo", LEDGER_REPO, "--kind", KIND,
                     "--label", PRIMARY_LABEL, "--title", TITLE, "--body-file", tmp)
    finally:
        Path(tmp).unlink(missing_ok=True)

    data = json.loads(_audit_issue("get", "--repo", LEDGER_REPO, "--kind", KIND))
    number = data.get("number")
    if not number:
        raise RuntimeError("ledger issue could not be resolved after upsert")
    # Second label so the pick-mode/triage tooling skips it. Best effort: the
    # digest must still publish if the label cannot be added.
    try:
        _gh(["issue", "edit", str(number), "--repo", LEDGER_REPO,
             "--add-label", LEDGER_LABEL])
    except RuntimeError as exc:
        print(f"WARN could not add '{LEDGER_LABEL}' label: {exc}", file=sys.stderr)
    return int(number)


def publish(run: dict, markdown: str, delivery_state: str) -> str:
    """Post the digest as a comment on the ledger; return the comment URL."""
    number = ensure_ledger()
    body = markdown.rstrip() + "\n\n" + render_stamp(run, delivery_state) + "\n"
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as fh:
        fh.write(body)
        tmp = fh.name
    try:
        return _gh(["issue", "comment", str(number), "--repo", LEDGER_REPO, "--body-file", tmp])
    finally:
        Path(tmp).unlink(missing_ok=True)


# ---- CLI --------------------------------------------------------------------

def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: Optional[list[str]] = None) -> int:
    ensure_utf8_stdio()
    ap = argparse.ArgumentParser(description="context-purge per-run digest")
    sub = ap.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("validate", help="check run data before anything renders it")
    v.add_argument("run", type=Path)

    r = sub.add_parser("render", help="render markdown / html / chat text from run data")
    r.add_argument("run", type=Path)
    r.add_argument("--md", type=Path)
    r.add_argument("--html", type=Path)
    r.add_argument("--chat-text", type=Path)
    r.add_argument("--url", help="artifact URL to reference, when one published")
    r.add_argument("--reconcile-json", type=Path,
                   help="gate.py reconcile --json output; carries the live "
                        "open-purge-PR backlog (fleet-config#757). Omit to "
                        "render the backlog section as 'not recorded'.")

    p = sub.add_parser("publish", help="post the digest to the managed ledger issue")
    p.add_argument("run", type=Path)
    p.add_argument("--md", type=Path, required=True)
    p.add_argument("--delivery-state", choices=["posted", "failed", "unknown"], default="unknown")

    m = sub.add_parser("merge-check",
                       help="go/no-go before `gh pr merge`: may this repo's rewrite ship?")
    m.add_argument("repo_json", type=Path,
                   help="one repo entry (the worker's run/<repo>.json)")

    args = ap.parse_args(argv)

    if args.cmd == "merge-check":
        try:
            repo = _load(args.repo_json)
        except (OSError, ValueError) as exc:
            print(f"MERGE=blocked: could not read repo data: {exc}", file=sys.stderr)
            return 2
        if not isinstance(repo, dict):
            print("MERGE=blocked: repo data is not a JSON object", file=sys.stderr)
            return 2
        blockers = merge_blockers(repo)
        if blockers:
            print("MERGE=blocked: " + "; ".join(blockers), file=sys.stderr)
            return 2
        print(f"MERGE=ok repo={repo.get('repo', '?')}")
        return 0

    try:
        run = _load(args.run)
    except (OSError, ValueError) as exc:
        print(f"FAIL could not read run data: {exc}", file=sys.stderr)
        return 2

    errors = validate(run)
    if errors:
        for e in errors:
            print(f"FAIL {e}", file=sys.stderr)
        return 2

    if args.cmd == "validate":
        h = headline(run)
        print(f"OK run={run.get('run_id')} status={run.get('status')} "
              f"repos={h['repos']} rewritten={h['files_rewritten']} "
              f"probe={coverage_line(h)}")
        return 0

    if args.cmd == "render":
        backlog = None
        if args.reconcile_json:
            try:
                backlog = json.loads(args.reconcile_json.read_text(encoding="utf-8")).get("backlog")
            except (OSError, ValueError) as exc:
                print(f"WARN could not read --reconcile-json: {exc}", file=sys.stderr)
        if args.md:
            args.md.write_text(render_markdown(run, args.url, backlog), encoding="utf-8")
        if args.html:
            args.html.write_text(render_html(run, backlog), encoding="utf-8")
        if args.chat_text:
            args.chat_text.write_text(render_chat(run, args.url, backlog), encoding="utf-8")
        print(f"RENDERED run={run.get('run_id')} "
              f"md={bool(args.md)} html={bool(args.html)} chat={bool(args.chat_text)}")
        return 0

    # publish
    try:
        markdown = args.md.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"FAIL could not read rendered markdown: {exc}", file=sys.stderr)
        return 2
    try:
        url = publish(run, markdown, args.delivery_state)
    except RuntimeError as exc:
        print(f"FAIL publish did not land: {exc}", file=sys.stderr)
        return 1
    print(f"PUBLISHED {url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
