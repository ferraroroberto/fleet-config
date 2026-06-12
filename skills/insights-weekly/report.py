"""Diff the two newest Claude Code Insights reports via the local LLM hub.

``/insights`` writes a fresh ``report-<timestamp>.html`` into
``~/.claude/usage-data/`` on every run, so that timestamped series *is* the
history. This finds the newest two reports, strips each to clean text (reusing
``extract.ReportExtractor``), and asks the **local LLM hub** (127.0.0.1:8000,
OpenAI-shape ``/v1/chat/completions``) to narrate what changed week-over-week.
The analysis is delegated to the hub — the orchestrating skill does not read the
200 raw JSON files, and does not write the narrative itself.

Output: a dated markdown report under ``~/.claude/usage-data/weekly/`` (durable,
user-local, never committed) and a short ``TL;DR`` digest printed to stdout for
the skill to post to Slack.

Stdlib only — POSTs to the hub with ``urllib`` so there's nothing to install in
the system Python (mirrors ``hooks/slack_notify.py``). The model is the hub's
job, not a re-implemented ``claude -p`` wrapper.

Config (env): ``INSIGHTS_DIFF_MODEL`` (default ``claude_sonnet`` — reliably up
via the hub's claude backend; point it at ``gemma4_26b`` / ``qwen3.5-4b`` /
``gemini_flash`` when that backend is loaded), ``INSIGHTS_HUB_URL`` (default
``http://127.0.0.1:8000/v1``).

Exit codes: 0 ok (diff or baseline), 1 no reports found, 3 hub call failed.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

# Reuse the extractor (same dir; Python puts the script's dir on sys.path[0]).
from extract import ReportExtractor

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):  # pragma: no cover
    pass

USAGE_DIR = Path(os.environ.get("INSIGHTS_USAGE_DIR", Path.home() / ".claude" / "usage-data"))
MODEL = os.environ.get("INSIGHTS_DIFF_MODEL", "claude_sonnet")
HUB_URL = os.environ.get("INSIGHTS_HUB_URL", "http://127.0.0.1:8000/v1").rstrip("/")
DIGEST_MARKER = "## TL;DR"


def find_reports(usage_dir: Path) -> list[Path]:
    """Dated reports, newest first. Excludes the ``report.html`` latest-copy."""
    reports = [p for p in usage_dir.glob("report-*.html") if p.is_file()]
    return sorted(reports, key=lambda p: p.name, reverse=True)


def extract_text(path: Path) -> str:
    parser = ReportExtractor()
    parser.feed(path.read_text(encoding="utf-8"))
    return parser.get_markdown()


def call_hub(messages: list[dict]) -> str:
    payload = json.dumps({"model": MODEL, "messages": messages, "max_tokens": 1600,
                          "temperature": 0.3}).encode("utf-8")
    req = urllib.request.Request(
        f"{HUB_URL}/chat/completions", data=payload,
        headers={"Content-Type": "application/json", "Authorization": "Bearer local-dummy"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        data = json.load(resp)
    return data["choices"][0]["message"]["content"].strip()


def diff_prompt(prev_text: str, cur_text: str) -> list[dict]:
    system = (
        "You compare two Claude Code Insights reports for the same user and write a "
        "concise, personal week-over-week 'what changed' note. Be specific and "
        "concrete; quantify shifts (sessions, lines, friction) when the numbers are "
        "there. Skip flattery and boilerplate. Do not restate a section that is "
        "unchanged. Markdown only."
    )
    user = (
        "Two Claude Code Insights reports follow. PREVIOUS is last time, CURRENT is "
        "now. Write the report with exactly these parts:\n\n"
        f"{DIGEST_MARKER}\n3-5 bullets a busy person reads on their phone: the most "
        "important changes this period.\n\n"
        "## What changed\nSection-by-section deltas (what you work on, how you use "
        "Claude Code, wins, friction) — only where it actually moved. Note new themes "
        "that appeared and old ones that dropped.\n\n"
        "## Numbers\nHeadline-stat deltas (messages, lines, files, days, sessions).\n\n"
        "## Watch\nOne or two things to keep an eye on next week.\n\n"
        f"=== PREVIOUS REPORT ===\n{prev_text}\n\n=== CURRENT REPORT ===\n{cur_text}\n"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def baseline_prompt(cur_text: str) -> list[dict]:
    system = (
        "You summarize a Claude Code Insights report into a short personal baseline. "
        "This is the first capture, so there is nothing to compare yet. Be concise and "
        "concrete; skip flattery. Markdown only."
    )
    user = (
        "This is the first Claude Code Insights report captured for week-over-week "
        "tracking — there is no prior report to diff against. Write:\n\n"
        f"{DIGEST_MARKER}\n3-4 bullets: the current state at a glance (what you work "
        "on, main friction, headline numbers).\n\n"
        "## Baseline\nA short paragraph describing this starting point. Note that next "
        "run will diff against it.\n\n"
        f"=== CURRENT REPORT ===\n{cur_text}\n"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def extract_digest(report: str) -> str:
    """Pull the TL;DR block (to the next H2) for the Slack post; fall back to head."""
    start = report.find(DIGEST_MARKER)
    if start == -1:
        return report[:600].strip()
    body = report[start + len(DIGEST_MARKER):]
    end = body.find("\n## ")
    return (body if end == -1 else body[:end]).strip()


def main() -> int:
    today = _dt.date.today().isoformat()
    reports = find_reports(USAGE_DIR)
    if not reports:
        print(f"no report-*.html found in {USAGE_DIR}", file=sys.stderr)
        return 1

    current = reports[0]
    previous = reports[1] if len(reports) > 1 else None
    cur_text = extract_text(current)

    if previous is None:
        mode, header = "baseline", "first run — baseline captured, no prior report to diff"
        messages = baseline_prompt(cur_text)
    else:
        mode, header = "diff", f"compared {current.name} vs {previous.name}"
        messages = diff_prompt(extract_text(previous), cur_text)

    try:
        body = call_hub(messages)
    except (urllib.error.URLError, urllib.error.HTTPError, KeyError, TimeoutError, OSError) as exc:
        print(f"hub call failed ({MODEL} @ {HUB_URL}): {exc}", file=sys.stderr)
        return 3

    out_dir = USAGE_DIR / "weekly"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"insights-diff-{today}.md"
    front = (
        f"<!-- insights-weekly {mode} · {today} · model={MODEL} · {header} -->\n"
        f"# Claude Code Insights — weekly {('diff' if mode == 'diff' else 'baseline')} ({today})\n\n"
        f"_{header}_\n\n"
    )
    out_file.write_text(front + body + "\n", encoding="utf-8")

    # stdout: machine-friendly first line (path) + the Slack digest after a blank line.
    print(out_file)
    print()
    print(extract_digest(body))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
