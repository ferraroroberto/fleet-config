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
              ledger issue. This is the link the Slack message can always
              carry: an Artifact publish is unproven from a headless run
              (see `/fleet-health`'s SKILL.md), so it may never be the only one.
  html     -- a self-contained page, published best-effort as a private
              Artifact because it reads better on a phone.
  slack    -- a short text digest: the three headline figures, any hard flag,
              and the link.

Every published comment carries a machine-readable stamp:

    <!-- context-purge-digest run=<id> status=complete|partial unreached=N slack=posted|failed|unknown -->

`delivery_check.py` reads it, so a partial run and a silently failed Slack post
are both caught by the same post-condition rather than exiting 0 having half
delivered.

stdlib only. CLI:
    digest.py validate <run.json>
    digest.py render   <run.json> --html P --md P --slack P [--url U]
    digest.py publish  <run.json> --md P [--slack-state posted|failed|unknown]
"""

from __future__ import annotations

import argparse
import html as _html
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional

SKILL_DIR = Path(__file__).resolve().parent
REPO_ROOT = SKILL_DIR.parents[2]

sys.path.insert(0, str(REPO_ROOT / "skills" / "_lib"))
from no_window import NO_WINDOW  # noqa: E402
from utf8_stdio import ensure_utf8_stdio  # noqa: E402

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

SHAPE_WEIGHT = {"large": 3, "medium": 2, "small": 1}


# ---- run-data access: unknown never becomes zero ---------------------------

def _num(value: Any) -> Optional[int]:
    """An int, or None when the run did not record one. Never coerces to 0."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def probe_state(entry: dict) -> str:
    """`probed` | `not-probed` | `unknown` for one file entry.

    The three-way split is the whole point: "0 files probed" and "probe
    coverage not recorded" are different facts.
    """
    if "probe" not in entry:
        return "unknown"
    probe = entry.get("probe")
    if probe is None:
        return "unknown"
    if not isinstance(probe, dict):
        return "unknown"
    if probe.get("ran") is True:
        return "probed"
    if probe.get("ran") is False:
        return "not-probed"
    return "unknown"


def probe_verdict(entry: dict) -> Optional[bool]:
    """True when the compressed file scored >= the control, None if unmeasured."""
    probe = entry.get("probe")
    if not isinstance(probe, dict) or probe.get("ran") is not True:
        return None
    compressed, control = _num(probe.get("compressed")), _num(probe.get("control"))
    if compressed is None or control is None:
        return None
    return compressed >= control


def rewritten_files(run: dict) -> list[tuple[dict, dict]]:
    return [(repo, f) for repo in run.get("repos", [])
            for f in repo.get("files", []) if f.get("action") == "rewritten"]


def headline(run: dict) -> dict:
    """The figures a reader needs before trusting anything else in the digest."""
    pairs = rewritten_files(run)
    tokens_removed = 0
    tokens_unknown = 0
    for _repo, f in pairs:
        before, after = _num(f.get("tokens_before")), _num(f.get("tokens_after"))
        if before is None or after is None:
            tokens_unknown += 1
        else:
            tokens_removed += before - after

    inventory_walked = 0
    inventory_unknown = 0
    for _repo, f in pairs:
        items = _num(f.get("inventory_items"))
        if items is None:
            inventory_unknown += 1
        else:
            inventory_walked += items

    states = [probe_state(f) for _repo, f in pairs]
    return {
        "tokens_removed": tokens_removed,
        "tokens_unknown": tokens_unknown,
        "files_rewritten": len(pairs),
        "files_assessed": sum(len(r.get("files", [])) for r in run.get("repos", [])),
        "inventory_walked": inventory_walked,
        "inventory_unknown": inventory_unknown,
        "probed": states.count("probed"),
        "not_probed": states.count("not-probed"),
        "probe_unknown": states.count("unknown"),
        "repos": len(run.get("repos", [])),
        "unreached": len(run.get("unreached", [])),
    }


def coverage_line(h: dict) -> str:
    """Probe coverage as a fraction, with unknowns kept as their own term."""
    if h["files_rewritten"] == 0:
        return "no files rewritten"
    line = f"{h['probed']}/{h['files_rewritten']} rewritten files probed"
    if h["probe_unknown"]:
        line += f", {h['probe_unknown']} not recorded"
    return line


def risk_score(repo: dict) -> tuple[int, str]:
    """Rank by what a lost directive would cost, not alphabetically."""
    risk = repo.get("risk") or {}
    score = 0
    if risk.get("always_on") is True:
        score += 3
    score += SHAPE_WEIGHT.get(str(risk.get("shape_change", "")).lower(), 0)
    checked = risk.get("checked")
    if checked is not True:
        score += 3  # nothing verified the result, or we do not know that it did
    label = "checked" if checked is True else ("unchecked" if checked is False else "unknown")
    return score, label


def ranked_repos(run: dict) -> list[dict]:
    return sorted(run.get("repos", []), key=lambda r: (-risk_score(r)[0], r.get("repo", "")))


def all_descriptions(run: dict) -> list[dict]:
    out = []
    for repo in run.get("repos", []):
        for d in repo.get("descriptions", []):
            out.append({**d, "repo": repo.get("repo", "?")})
    return out


def collect(run: dict, key: str) -> list[dict]:
    out = []
    for repo in run.get("repos", []):
        for item in repo.get(key, []):
            out.append({**item, "repo": repo.get("repo", "?")})
    return out


def untouched_files(run: dict) -> list[dict]:
    out = []
    for repo in run.get("repos", []):
        for f in repo.get("files", []):
            if f.get("action") in ("assessed-lean", "untouched"):
                out.append({**f, "repo": repo.get("repo", "?")})
    return out


# ---- validation -------------------------------------------------------------

def validate(run: Any) -> list[str]:
    """Structural problems that would make the digest lie. Empty = usable."""
    errors: list[str] = []
    if not isinstance(run, dict):
        return ["run data is not a JSON object"]
    if not run.get("run_id"):
        errors.append("run_id is missing")
    status = run.get("status")
    if status not in ("complete", "partial"):
        errors.append(f"status must be 'complete' or 'partial', got {status!r}")
    if not isinstance(run.get("repos"), list):
        errors.append("repos must be a list")
        return errors
    if status == "partial" and not run.get("unreached"):
        errors.append("status=partial but no unreached repos are named -- a partial run "
                      "must say which repos it did not reach")
    if status == "complete" and run.get("unreached"):
        errors.append("status=complete but unreached repos are listed -- contradictory")
    for repo in run["repos"]:
        name = repo.get("repo", "<unnamed>")
        if not repo.get("repo"):
            errors.append("a repo entry has no name")
        if repo.get("status") not in ("shipped", "skipped", "failed"):
            errors.append(f"{name}: status must be shipped|skipped|failed, "
                          f"got {repo.get('status')!r}")
        for f in repo.get("files", []):
            if not f.get("path"):
                errors.append(f"{name}: a file entry has no path")
            if f.get("action") not in ("rewritten", "assessed-lean", "untouched"):
                errors.append(f"{name}/{f.get('path')}: action must be "
                              f"rewritten|assessed-lean|untouched, got {f.get('action')!r}")
            if f.get("action") == "rewritten" and "probe" not in f:
                errors.append(f"{name}/{f.get('path')}: rewritten file must carry a "
                              f"'probe' key (null is allowed and means 'not recorded')")
    return errors


# ---- stamp ------------------------------------------------------------------

def render_stamp(run: dict, slack_state: str) -> str:
    return (f"<!-- {STAMP_PREFIX} run={run.get('run_id', 'unknown')} "
            f"status={run.get('status', 'unknown')} "
            f"unreached={len(run.get('unreached', []))} "
            f"slack={slack_state} -->")


# ---- markdown ---------------------------------------------------------------

def _tok(value: Any) -> str:
    n = _num(value)
    return "not recorded" if n is None else f"{n:,}"


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


def render_markdown(run: dict, artifact_url: Optional[str] = None) -> str:
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
    if artifact_url:
        lines.append(f"- **Full page:** {artifact_url}")
    lines.append("")

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
    return "\n".join(lines) + "\n"


# ---- slack ------------------------------------------------------------------

def render_slack(run: dict, link: Optional[str] = None) -> str:
    h = headline(run)
    partial = run.get("status") == "partial"
    head = "⚠️ PARTIAL" if partial else "🧹"
    lines = [f"{head} `/context-purge` run `{run.get('run_id','unknown')}` — "
             f"{h['tokens_removed']:,} est. tokens removed, "
             f"{h['files_rewritten']} file(s) rewritten across {h['repos']} repo(s)",
             f"Probe coverage: {coverage_line(h)}"]
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
    if link:
        lines.append(link)
    return "\n".join(lines)


# ---- html -------------------------------------------------------------------

def render_html(run: dict) -> str:
    h = headline(run)
    md_rows = render_markdown(run)
    esc = _html.escape
    partial = run.get("status") == "partial"

    def cells(row: list[str], tag: str = "td") -> str:
        return "".join(f"<{tag}>{c}</{tag}>" for c in row)

    file_rows = "".join(
        "<tr>" + cells([
            f"<code>{esc(repo.get('repo','?'))}</code>",
            f"<code>{esc(f.get('path','?'))}</code>",
            esc(_delta(f)),
            esc(_probe_cell(f)),
        ]) + "</tr>"
        for repo, f in rewritten_files(run)
    ) or "<tr><td colspan='4'>no files rewritten</td></tr>"

    desc_rows = "".join(
        "<tr>" + cells([
            f"<code>{esc(d['repo'])}</code>",
            f"<code>{esc(d.get('path','?'))}</code>",
            esc(f"{d.get('words_before','?')} → {d.get('words_after','?')}"),
            f"<div class='was'>{esc(d.get('before',''))}</div>"
            f"<div class='now'>{esc(d.get('after',''))}</div>",
        ]) + "</tr>"
        for d in all_descriptions(run)
    ) or "<tr><td colspan='4'>none rewritten</td></tr>"

    repo_rows = "".join(
        "<tr>" + cells([
            f"<code>{esc(r.get('repo','?'))}</code>",
            esc(str((r.get('risk') or {}).get('always_on', 'not recorded'))),
            esc(str((r.get('risk') or {}).get('shape_change', 'not recorded'))),
            esc(risk_score(r)[1]),
            esc(r.get('status', '?')),
        ]) + "</tr>"
        for r in ranked_repos(run)
    )

    def bullets(items: list[dict], fmt) -> str:
        return "".join(f"<li>{fmt(i)}</li>" for i in items) or "<li>none</li>"

    banner = ""
    if partial:
        names = ", ".join(esc(u.get("repo", "?")) for u in run.get("unreached", []))
        banner = (f"<div class='banner'><strong>PARTIAL RUN.</strong> "
                  f"{h['unreached']} repo(s) not reached: {names}. "
                  f"Every figure below covers only the repos that were.</div>")

    return f"""<title>context-purge {esc(str(run.get('run_id','')))}</title>
<style>
:root {{ --bg:#fbfbfa; --fg:#1f2328; --muted:#6b7280; --line:#e5e7eb;
        --card:#ffffff; --accent:#b45309; --warn:#fef3c7; }}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{
  --bg:#16181d; --fg:#e6e8eb; --muted:#9ca3af; --line:#2b2f36;
  --card:#1c1f26; --accent:#fbbf24; --warn:#3a2f10; }} }}
:root[data-theme="dark"] {{ --bg:#16181d; --fg:#e6e8eb; --muted:#9ca3af;
  --line:#2b2f36; --card:#1c1f26; --accent:#fbbf24; --warn:#3a2f10; }}
body {{ background:var(--bg); color:var(--fg); margin:0; padding:1.5rem 1rem 4rem;
  font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
main {{ max-width:60rem; margin:0 auto; }}
h1 {{ font-size:1.5rem; margin:0 0 .25rem; }}
h2 {{ font-size:1.05rem; margin:2rem 0 .5rem; padding-bottom:.3rem;
  border-bottom:1px solid var(--line); }}
.sub {{ color:var(--muted); margin:0 0 1.5rem; }}
.grid {{ display:grid; gap:.75rem; grid-template-columns:repeat(auto-fit,minmax(11rem,1fr)); }}
.stat {{ background:var(--card); border:1px solid var(--line); border-radius:.6rem; padding:.8rem; }}
.stat .n {{ font-size:1.5rem; font-weight:600; }}
.stat .l {{ color:var(--muted); font-size:.82rem; }}
.banner {{ background:var(--warn); border:1px solid var(--accent); border-radius:.6rem;
  padding:.8rem; margin-bottom:1.25rem; }}
.scroll {{ overflow-x:auto; }}
table {{ border-collapse:collapse; width:100%; font-size:.88rem; }}
th,td {{ text-align:left; padding:.45rem .6rem; border-bottom:1px solid var(--line);
  vertical-align:top; }}
th {{ color:var(--muted); font-weight:600; }}
code {{ font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:.85em; }}
.was {{ color:var(--muted); text-decoration:line-through; }}
.now {{ margin-top:.3rem; }}
ul {{ padding-left:1.1rem; }}
</style>
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

<h2>Probe coverage, per file</h2>
<div class="scroll"><table>
<tr><th>Repo</th><th>File</th><th>Tokens</th><th>Probe</th></tr>
{file_rows}
</table></div>

<h2>Rewritten skill descriptions</h2>
<div class="scroll"><table>
<tr><th>Repo</th><th>File</th><th>Words</th><th>Before / after</th></tr>
{desc_rows}
</table></div>

<h2>Repos by cost of a lost directive</h2>
<div class="scroll"><table>
<tr><th>Repo</th><th>Always-on</th><th>Shape change</th><th>Verified</th><th>Result</th></tr>
{repo_rows}
</table></div>

<h2>Reviewer decisions asked for</h2>
<ul>{bullets(collect(run, 'decisions'),
             lambda d: f"<code>{esc(d['repo'])}</code> — {esc(d.get('summary',''))}")}</ul>

<h2>Found and deliberately not fixed</h2>
<ul>{bullets(collect(run, 'not_fixed'),
             lambda n: f"<code>{esc(n['repo'])}</code> — {esc(n.get('summary',''))} "
                       f"<em>({esc(n.get('reason','not recorded'))})</em>")}</ul>

<h2>Assessed and left untouched</h2>
<ul>{bullets(untouched_files(run),
             lambda f: f"<code>{esc(f['repo'])}/{esc(f.get('path',''))}</code> "
                       f"({esc(f.get('action',''))})")}</ul>
</main>
"""


# ---- publish ----------------------------------------------------------------

def _audit_issue(*args: str) -> str:
    res = subprocess.run(
        [sys.executable, str(AUDIT_ISSUE), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        creationflags=NO_WINDOW,
    )
    if res.returncode != 0:
        raise RuntimeError(f"audit_issue.py {args[0]} failed: {(res.stderr or res.stdout).strip()}")
    return res.stdout


def _gh(args: list[str]) -> str:
    res = subprocess.run(
        ["gh", *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=120, creationflags=NO_WINDOW,
    )
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


def publish(run: dict, markdown: str, slack_state: str) -> str:
    """Post the digest as a comment on the ledger; return the comment URL."""
    number = ensure_ledger()
    body = markdown.rstrip() + "\n\n" + render_stamp(run, slack_state) + "\n"
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

    r = sub.add_parser("render", help="render markdown / html / slack from run data")
    r.add_argument("run", type=Path)
    r.add_argument("--md", type=Path)
    r.add_argument("--html", type=Path)
    r.add_argument("--slack", type=Path)
    r.add_argument("--url", help="artifact URL to reference, when one published")

    p = sub.add_parser("publish", help="post the digest to the managed ledger issue")
    p.add_argument("run", type=Path)
    p.add_argument("--md", type=Path, required=True)
    p.add_argument("--slack-state", choices=["posted", "failed", "unknown"], default="unknown")

    args = ap.parse_args(argv)

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
        if args.md:
            args.md.write_text(render_markdown(run, args.url), encoding="utf-8")
        if args.html:
            args.html.write_text(render_html(run), encoding="utf-8")
        if args.slack:
            args.slack.write_text(render_slack(run, args.url), encoding="utf-8")
        print(f"RENDERED run={run.get('run_id')} "
              f"md={bool(args.md)} html={bool(args.html)} slack={bool(args.slack)}")
        return 0

    # publish
    try:
        markdown = args.md.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"FAIL could not read rendered markdown: {exc}", file=sys.stderr)
        return 2
    try:
        url = publish(run, markdown, args.slack_state)
    except RuntimeError as exc:
        print(f"FAIL publish did not land: {exc}", file=sys.stderr)
        return 1
    print(f"PUBLISHED {url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
