"""Weekly fleet learning log — narrate the GitHub work stream via the local hub.

`/learning-log` reads the **work stream itself** (merged PRs + closed issues
across the ferraroroberto fleet) for the trailing window and asks the local LLM
hub to narrate the learning journey + grade last week's horizon. It reads **no
source code** — only `gh search` and the learning-log ledger issue.

This script does the read-only heavy lifting and emits two files for the
orchestrating skill (`SKILL.md`) to write to GitHub:

  * the **weekly digest** (posted as a comment on the ledger issue), and
  * the **updated ledger body** (durable archive + the next horizon),

then prints machine-friendly `KEY=VALUE` lines and the TL;DR. It never writes to
GitHub itself — the skill upserts the ledger (via `skills/_lib/audit_issue.py`),
posts the comment, and fires the Slack ping.

Division of labour mirrors `insights-weekly/report.py`: the **hub does the
analysis**, this script only gathers + assembles, and the orchestrator does the
GitHub/Slack writes. Stdlib only — POSTs to the hub with `urllib` (zero install).

Window: `--since YYYY-MM-DD` overrides; otherwise the ledger's `last-run-at`
(so a missed weekly run widens the next window instead of dropping a week);
otherwise trailing 7 days on the very first run.

Config (env): `LEARNING_LOG_MODEL` (default `claude_sonnet`), `LEARNING_LOG_HUB_URL`
(default `http://127.0.0.1:8000/v1`).

Exit codes: 0 ok (narrated or quiet-week), 3 hub call failed.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):  # pragma: no cover
    pass

# Default to gemini_flash: it's reliably up via the hub and handles a ~5k-token
# rollup fast. claude_sonnet (nested `claude -p`) hangs on inputs this size, and
# the local llama backends (gemma4_26b, …) are lazy-loaded so they 502 when cold
# — neither is safe for an unattended weekly run. The input is GitHub-public PR /
# issue titles + counts, not secrets. Override with LEARNING_LOG_MODEL (e.g.
# gemma4_26b for a fully-local run, when you keep that backend warm).
MODEL = os.environ.get("LEARNING_LOG_MODEL", "gemini_flash")
HUB_URL = os.environ.get("LEARNING_LOG_HUB_URL", "http://127.0.0.1:8000/v1").rstrip("/")
HELPER = Path(__file__).resolve().parents[1] / "_lib" / "audit_issue.py"

# This is a high-velocity fleet (~25 merged PRs/day) — hundreds of items even in
# a normal week. The hub gets a compact per-repo rollup (summarize_window), never
# the raw item list, so the prompt stays small and `claude_sonnet` doesn't time
# out. An absolute per-kind cap (most-recent) still protects the rollup from a
# pathological backlog. Env-overridable.
MAX_PER_KIND = int(os.environ.get("LEARNING_LOG_MAX_ITEMS", "600"))

DIGEST_MARKER = "## TL;DR"
ARCHIVE_HEADER = "## Decision / discovery archive"
HORIZON_HEADER = "## Horizon → next week"
DISCOVERIES_HEADER = "## Discoveries to archive"
LEDGER_TITLE = "learning log — fleet"


# ---- pure helpers (unit-tested without gh / hub) --------------------------

def parse_last_run(body: str) -> str | None:
    """The `last-run-at: YYYY-MM-DD` stamp from the ledger body, or None."""
    m = re.search(r"last-run-at:\s*(\d{4}-\d{2}-\d{2})", body or "")
    return m.group(1) if m else None


def resolve_since(arg: str | None, prior_body: str, today: _dt.date) -> str:
    """Window start: explicit arg → ledger last-run-at → trailing 7 days."""
    if arg:
        return arg
    last = parse_last_run(prior_body)
    if last:
        return last
    return (today - _dt.timedelta(days=7)).isoformat()


def slice_section(text: str, header: str) -> str:
    """Return the body of a `## Header` section up to the next H2 (or end)."""
    idx = (text or "").find(header)
    if idx == -1:
        return ""
    rest = text[idx + len(header):]
    end = rest.find("\n## ")
    return (rest if end == -1 else rest[:end]).strip()


def extract_digest(report: str) -> str:
    """Pull the TL;DR block for the Slack ping; fall back to the head."""
    block = slice_section(report, DIGEST_MARKER)
    return block or (report or "")[:600].strip()


def _bullet_lines(section: str) -> list[str]:
    return [ln.strip() for ln in (section or "").splitlines()
            if ln.strip().startswith(("-", "*"))]


def dated_discovery_bullets(discoveries_section: str, today: str, cap: int = 8) -> list[str]:
    """Turn the hub's 'Discoveries to archive' bullets into dated archive lines."""
    out: list[str] = []
    for ln in _bullet_lines(discoveries_section)[:cap]:
        content = ln.lstrip("-*").strip()
        if content:
            out.append(f"- {today}: {content}")
    return out


def extract_archive_bullets(prior_body: str) -> list[str]:
    """Existing archive bullets, preserved verbatim (the durable memory)."""
    return _bullet_lines(slice_section(prior_body, ARCHIVE_HEADER))


def build_ledger_body(prior_body: str, today: str, horizon_section: str,
                      discoveries_section: str) -> str:
    """Assemble the new ledger body: state stamp + next horizon + grown archive.

    The `<!-- audit-managed: kind=learning -->` marker is added by the upsert
    helper; this returns everything below it. Newest discoveries lead the
    archive; every prior bullet is preserved (durable memory).
    """
    horizon = "\n".join(_bullet_lines(horizon_section)) or "- [ ] (none captured this run)"
    archive = dated_discovery_bullets(discoveries_section, today) + extract_archive_bullets(prior_body)
    archive_md = "\n".join(archive) if archive else "- (nothing archived yet)"
    return (
        "<!-- learning-log-state -->\n"
        f"last-run-at: {today}\n\n"
        "# Learning log — fleet\n\n"
        "The fleet's weekly learning journal — what shipped, what we learned, and "
        "what's next — sourced from merged PRs + closed issues by `/learning-log`. "
        "The running week-by-week narrative lives in this issue's comments; this "
        "body is the durable archive + the live horizon. One canonical issue "
        "(durable knowledge lands here, not in `docs/`).\n\n"
        f"## Horizon → next week (set {today})\n{horizon}\n\n"
        f"{ARCHIVE_HEADER}\n{archive_md}\n"
    )


# ---- gh + hub plumbing ----------------------------------------------------

def _gh_search(args: list[str]) -> list[dict]:
    """Run a read-only `gh search …` and parse JSON; [] on any failure."""
    try:
        proc = subprocess.run(["gh", "search", *args], capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=90)
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"gh search failed: {exc}", file=sys.stderr)
        return []
    if proc.returncode != 0:
        print(f"gh search exited {proc.returncode}: {proc.stderr.strip()[:200]}", file=sys.stderr)
        return []
    try:
        data = json.loads(proc.stdout or "[]")
    except ValueError:
        return []
    return data if isinstance(data, list) else []


# GitHub's *search* API is rate-limited hard (~30 req/min) and 401s on deep
# pagination, so cap the fetch shallow and sort by recency — we want the most
# recent slice of a high-velocity window, not every page. `--limit 300` is 3
# pages; a normal week is well under it, and a big backfill gets its recent head.
SEARCH_LIMIT = os.environ.get("LEARNING_LOG_SEARCH_LIMIT", "300")


def gather_merged_prs(owner: str, since: str) -> list[dict]:
    return _gh_search([
        "prs", "--owner", owner, "--merged", "--merged-at", f">={since}",
        "--sort", "updated", "--order", "desc", "--limit", SEARCH_LIMIT,
        "--json", "number,title,url,closedAt,repository,body",
    ])


def gather_closed_issues(owner: str, since: str) -> list[dict]:
    rows = _gh_search([
        "issues", "--owner", owner, "--closed", f">={since}",
        "--sort", "updated", "--order", "desc", "--limit", SEARCH_LIMIT,
        "--json", "number,title,url,closedAt,repository,labels,isPullRequest",
    ])
    # `gh search issues` includes PRs; keep only real issues (PRs come via gather_merged_prs).
    return [r for r in rows if not r.get("isPullRequest")]


def _recent(rows: list[dict], n: int) -> list[dict]:
    """The n most-recently-closed rows (the cap that bounds the hub prompt)."""
    return sorted(rows, key=lambda r: r.get("closedAt") or "", reverse=True)[:n]


def _repo_name(row: dict) -> str:
    repo = row.get("repository") or {}
    return repo.get("name") or repo.get("nameWithOwner") or "?"


def _type_prefix(title: str) -> str:
    """Conventional-commit type from a PR title (feat/fix/chore/…), else 'other'."""
    m = re.match(r"\s*([a-z]+)(?:\([^)]*\))?!?:", title or "", re.I)
    return m.group(1).lower() if m else "other"


def summarize_window(prs: list[dict], issues: list[dict],
                     notable_prs: int = 6, notable_issues: int = 4) -> str:
    """Compact per-repo rollup covering the WHOLE window.

    On this fleet a week is hundreds of items — dumping every raw line both
    bloats the prompt and times the hub out. Instead, per repo: total PR/issue
    counts, a breakdown by conventional-commit type, and a sample of the most
    recent notable titles. The counts give the hub the full breadth; the sampled
    titles give it texture. One bounded prompt, full-window faithful.
    """
    repos: dict[str, dict[str, list[dict]]] = {}
    for p in prs:
        repos.setdefault(_repo_name(p), {"prs": [], "issues": []})["prs"].append(p)
    for i in issues:
        repos.setdefault(_repo_name(i), {"prs": [], "issues": []})["issues"].append(i)

    blocks: list[str] = []
    for name in sorted(repos, key=lambda k: len(repos[k]["prs"]) + len(repos[k]["issues"]), reverse=True):
        rp, ri = repos[name]["prs"], repos[name]["issues"]
        types: dict[str, int] = {}
        for p in rp:
            t = _type_prefix(p.get("title", ""))
            types[t] = types.get(t, 0) + 1
        breakdown = ", ".join(f"{n} {t}" for t, n in sorted(types.items(), key=lambda x: -x[1]))
        head = f"### {name} — {len(rp)} PRs" + (f" ({breakdown})" if breakdown else "") + f", {len(ri)} issues closed"
        lines = [head]
        lines += [f"- PR #{p.get('number')}: {p.get('title')}" for p in _recent(rp, notable_prs)]
        for i in _recent(ri, notable_issues):
            labels = ",".join(l.get("name", "") for l in (i.get("labels") or []))
            lines.append(f"- issue #{i.get('number')}: {i.get('title')}" + (f" [{labels}]" if labels else ""))
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) or "(no activity)"


def call_hub(messages: list[dict]) -> str:
    payload = json.dumps({"model": MODEL, "messages": messages, "max_tokens": 2200,
                          "temperature": 0.3}).encode("utf-8")
    req = urllib.request.Request(
        f"{HUB_URL}/chat/completions", data=payload,
        headers={"Content-Type": "application/json", "Authorization": "Bearer local-dummy"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        data = json.load(resp)
    return data["choices"][0]["message"]["content"].strip()


def narrate_prompt(rollup: str, prior_horizon: str,
                   since: str, today: str, totals: str) -> list[dict]:
    system = (
        "You are the engineering chronicler for a one-person AI-automation fleet "
        "(~27 repos). From a per-repo rollup of merged PRs and closed issues you "
        "write a concise, specific learning log: what shipped, what was learned, "
        "what's worth remembering, and what's next. Group insight by THEME, not by "
        "repo. Be concrete; skip flattery and filler. Markdown only, using exactly "
        "the section headers requested."
    )
    horizon_block = prior_horizon.strip() or "(none — this is the first run; no prior horizon to grade)"
    user = (
        f"Window: {since} → {today}. The fleet closed {totals} in this window. Below "
        "is a per-repo rollup: each repo's PR/issue counts, a breakdown by "
        "conventional-commit type (feat/fix/chore/docs/refactor), and a sample of "
        "the most recent notable titles. Use the counts for breadth and the titles "
        "for texture. Reference repos as plain `repo#N` (e.g. local-llm-hub#116) — "
        "never invent URLs or file:// links. Write the learning log with EXACTLY "
        "these sections and headers:\n\n"
        f"{DIGEST_MARKER}\n3-5 phone bullets: the most important things that "
        "happened and were learned this period.\n\n"
        "## What shipped & what we learned\nThemed prose (group by theme, not by "
        "repo): the arc of the week and the reasoning/lessons behind the changes.\n\n"
        f"{DISCOVERIES_HEADER}\n3-7 bullets, each a single durable learning worth "
        "remembering months from now, with the (repo#N) it came from.\n\n"
        "## Horizon grading\nGrade ONLY items that literally appear in the PRIOR "
        "HORIZON block below: which shipped, which slipped, plus what emerged "
        "UNPLANNED this week. If that block says there is none / first run, write "
        "exactly 'First run — baseline, no prior horizon to grade.' and nothing "
        "else here — never invent predictions that aren't in that block.\n\n"
        f"{HORIZON_HEADER}\n3-6 forward checkboxes ('- [ ] …') inferred from open "
        "threads and the direction of travel.\n\n"
        f"=== PRIOR HORIZON (last week's predictions) ===\n{horizon_block}\n\n"
        f"=== PER-REPO ROLLUP THIS WINDOW ===\n{rollup}\n"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def read_ledger(repo: str) -> dict:
    """Best-effort `audit_issue.py get --kind learning`; {} on failure."""
    try:
        proc = subprocess.run(
            [sys.executable, str(HELPER), "get", "--repo", repo, "--kind", "learning"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
        )
        if proc.returncode == 0:
            return json.loads(proc.stdout or "{}")
        print(f"ledger get exited {proc.returncode}: {proc.stderr.strip()[:200]}", file=sys.stderr)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        print(f"ledger get failed: {exc}", file=sys.stderr)
    return {}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Weekly fleet learning log via the local hub.")
    ap.add_argument("--since", help="Window start YYYY-MM-DD (overrides ledger last-run-at).")
    ap.add_argument("--repo", default="ferraroroberto/claude-config", help="Ledger repo.")
    ap.add_argument("--owner", default="ferraroroberto", help="GitHub owner to search.")
    args = ap.parse_args(argv)

    today = _dt.date.today().isoformat()
    ledger = read_ledger(args.repo)
    prior_body = ledger.get("body") or ""
    since = resolve_since(args.since, prior_body, _dt.date.today())
    prior_horizon = slice_section(prior_body, HORIZON_HEADER)

    prs_all = gather_merged_prs(args.owner, since)
    issues_all = gather_closed_issues(args.owner, since)
    prs = _recent(prs_all, MAX_PER_KIND)
    issues = _recent(issues_all, MAX_PER_KIND)
    items = f"{len(prs_all)} PRs / {len(issues_all)} issues"
    capped = len(prs) < len(prs_all) or len(issues) < len(issues_all)
    scope = f" · rollup of the {len(prs)} most-recent PRs / {len(issues)} issues" if capped else ""

    tmp = Path(tempfile.gettempdir())
    digest_file = tmp / f"learning-log-digest-{today}.md"
    ledger_file = tmp / f"learning-log-ledger-{today}.md"

    if not prs_all and not issues_all:
        # Quiet week: still record the run so the ledger keeps cadence + last-run-at.
        narrative = (
            f"{DIGEST_MARKER}\n- No merged PRs or closed issues across the fleet "
            f"in {since} → {today}. A quiet week.\n"
        )
        digest_file.write_text(
            f"# 📓 Weekly learning log — {since} → {today}\n\n"
            f"_Quiet week: {items} across the fleet._\n\n{narrative}", encoding="utf-8")
        ledger_file.write_text(
            build_ledger_body(prior_body, today, prior_horizon, ""), encoding="utf-8")
        print(f"DIGEST_FILE={digest_file}")
        print(f"LEDGER_BODY_FILE={ledger_file}")
        print(f"SINCE={since}")
        print(f"ITEMS={items}")
        print()
        print(extract_digest(narrative))
        return 0

    messages = narrate_prompt(summarize_window(prs, issues), prior_horizon, since, today, items)
    try:
        narrative = call_hub(messages)
    except (urllib.error.URLError, urllib.error.HTTPError, KeyError, TimeoutError, OSError) as exc:
        print(f"hub call failed ({MODEL} @ {HUB_URL}): {exc}", file=sys.stderr)
        return 3

    digest_file.write_text(
        f"# 📓 Weekly learning log — {since} → {today}\n\n"
        f"_{items} across the fleet{scope} · model={MODEL}_\n\n{narrative}\n", encoding="utf-8")
    ledger_file.write_text(
        build_ledger_body(prior_body, today,
                          slice_section(narrative, HORIZON_HEADER),
                          slice_section(narrative, DISCOVERIES_HEADER)),
        encoding="utf-8")

    print(f"DIGEST_FILE={digest_file}")
    print(f"LEDGER_BODY_FILE={ledger_file}")
    print(f"SINCE={since}")
    print(f"ITEMS={items}")
    print()
    print(extract_digest(narrative))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
