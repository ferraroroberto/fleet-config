"""Deterministic measurement of the fleet's always-on context surface.

The `/context-audit` skill enforces the single-home-by-altitude standard
(ferraroroberto/project-scaffolding#68). This helper does the *measuring* — word
counts, file sizes, duplication, header inventory — so the numbers are exact and
the orchestrating session never invents them (same discipline as
`learning-log/gather.py`). The LLM orchestrator reads this manifest and supplies
the *judgment*: which flagged duplication is a genuine universal-directive leak
vs. a legitimate project-specific instance.

Measured surfaces:
  1. Skill descriptions  — word count (total + prose excluding quoted examples)
     for every `skills/*/SKILL.md`, flagged against the ~50-word cap. Trigger
     examples are exempt, so the prose count is the one that matters.
  2. Always-on budget    — bytes / words / est-tokens of `global-CLAUDE.md` and
     every project `CLAUDE.md` under the fleet root, plus a fleet total.
  3. Single-home leaks   — substantial lines in a project `CLAUDE.md` that also
     appear (normalized) in `global-CLAUDE.md`: candidate duplications to review.
  4. Header inventory     — `##`/`###` headers per project `CLAUDE.md` and the
     overlap with the project-scaffolding master, for drift review. Projects in
     the ignore-list (deliberate one-offs) are still measured but tagged.

stdlib only. Run from the `fleet-config` repo root: `py skills/context-audit/audit.py`.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# Repo root = .../fleet-config ; fleet root = .../automation (its parent).
REPO_ROOT = Path(__file__).resolve().parents[3]
FLEET_ROOT = REPO_ROOT.parent
GLOBAL_FILE = REPO_ROOT / "global-CLAUDE.md"
SCAFFOLD_FILE = FLEET_ROOT / "project-scaffolding" / "CLAUDE.md"
# Skills live in two roots: `skills/` (junctioned → ~/.claude/skills, always-on
# everywhere) and `.claude/skills/` (project-scoped, loads only in fleet-config).
# Both cost description tokens in-session, so the word-count cap audits both.
SKILLS_DIRS = [REPO_ROOT / "skills", REPO_ROOT / ".claude" / "skills"]

DEFAULT_CAP = 50  # words; the ~50-word skill-description prose cap (#137).
# Deliberate one-offs that do NOT derive from project-scaffolding — drift vs the
# scaffold master is expected and should not be flagged. Tag, don't penalize.
DEFAULT_IGNORE = {"fleet-config", "project-scaffolding"}

_QUOTED = re.compile(r"[\"'].*?[\"']")  # crude: strips "…" / '…' example phrases
_WORD = re.compile(r"\S+")


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def _frontmatter_description(skill_md: str) -> str:
    """Return the YAML `description:` value (single-line) or '' if absent."""
    if not skill_md.startswith("---"):
        return ""
    end = skill_md.find("\n---", 3)
    front = skill_md[3 : end if end != -1 else len(skill_md)]
    for line in front.splitlines():
        if line.startswith("description:"):
            return line[len("description:") :].strip()
    return ""


def _word_count(text: str) -> int:
    return len(_WORD.findall(text))


def _prose_words(description: str) -> int:
    """Word count with quoted example/trigger phrases removed (the exempt part)."""
    return _word_count(_QUOTED.sub(" ", description))


def _est_tokens(text: str) -> int:
    """Rough token estimate (~4 chars/token) — for tracking budget trend, not billing."""
    return round(len(text) / 4)


def _norm(line: str) -> str:
    return re.sub(r"\s+", " ", line.strip()).lower()


def scan_skills(cap: int) -> list[dict]:
    rows: list[dict] = []
    skill_mds = [m for d in SKILLS_DIRS for m in d.glob("*/SKILL.md")]
    for skill_md in sorted(skill_mds, key=lambda p: p.parent.name):
        desc = _frontmatter_description(_read(skill_md))
        if not desc:
            continue
        prose = _prose_words(desc)
        rows.append(
            {
                "skill": skill_md.parent.name,
                "words": _word_count(desc),
                "prose_words": prose,
                "over_cap": prose > cap,
            }
        )
    return rows


def find_project_claude_mds() -> list[tuple[str, Path]]:
    out: list[tuple[str, Path]] = []
    for d in sorted(p for p in FLEET_ROOT.iterdir() if p.is_dir()):
        cm = d / "CLAUDE.md"
        if cm.is_file():
            out.append((d.name, cm))
    return out


def _headers(text: str) -> list[str]:
    return [m.group(0).strip() for m in re.finditer(r"^#{2,3} .+$", text, re.M)]


def scan_budget_and_drift(ignore: set[str]) -> dict:
    global_text = _read(GLOBAL_FILE)
    global_norm = {_norm(l) for l in global_text.splitlines() if len(_norm(l)) >= 40}
    scaffold_headers = set(_headers(_read(SCAFFOLD_FILE)))

    files = [("global-CLAUDE.md", GLOBAL_FILE)] + find_project_claude_mds()
    budget: list[dict] = []
    leaks: list[dict] = []
    drift: list[dict] = []
    total_tokens = 0

    for name, path in files:
        text = _read(path)
        toks = _est_tokens(text)
        total_tokens += toks
        budget.append(
            {
                "file": name if name == "global-CLAUDE.md" else f"{name}/CLAUDE.md",
                "bytes": len(text.encode("utf-8")),
                "words": _word_count(text),
                "est_tokens": toks,
            }
        )
        if path == GLOBAL_FILE:
            continue

        # Single-home leaks: substantial lines duplicated verbatim from global.
        dup = sorted(
            {
                _norm(l)
                for l in text.splitlines()
                if len(_norm(l)) >= 40 and _norm(l) in global_norm
            }
        )
        if dup:
            leaks.append({"repo": name, "count": len(dup), "lines": dup[:8]})

        # Header drift vs the scaffold master (skipped for ignore-list one-offs).
        hdrs = set(_headers(text))
        shared = sorted(hdrs & scaffold_headers)
        drift.append(
            {
                "repo": name,
                "ignored": name in ignore,
                "headers": len(hdrs),
                "shared_with_scaffold": len(shared),
            }
        )

    budget.sort(key=lambda r: r["est_tokens"], reverse=True)
    return {
        "budget": budget,
        "total_est_tokens": total_tokens,
        "leaks": leaks,
        "drift": drift,
    }


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # UTF-8 under capture (fleet gotcha)
    ap = argparse.ArgumentParser(description="Measure the fleet's always-on context surface.")
    ap.add_argument("--cap", type=int, default=DEFAULT_CAP, help="skill-description prose word cap")
    ap.add_argument("--json", action="store_true", help="emit the full report as JSON")
    args = ap.parse_args()

    skills = scan_skills(args.cap)
    bd = scan_budget_and_drift(DEFAULT_IGNORE)
    report = {"cap": args.cap, "skills": skills, **bd}

    if args.json:
        print(json.dumps(report, indent=2))
        return 0

    over = [s for s in skills if s["over_cap"]]
    print("=== context-audit ===")
    print(
        f"MANIFEST: skills={len(skills)} over_cap={len(over)} "
        f"claude_mds={len(bd['budget'])} leaks={len(bd['leaks'])} "
        f"total_est_tokens={bd['total_est_tokens']}"
    )

    print("\n-- skill descriptions (prose words, cap {}) --".format(args.cap))
    for s in sorted(skills, key=lambda r: r["prose_words"], reverse=True):
        flag = "  ⚠️ OVER" if s["over_cap"] else ""
        print(f"  {s['skill']:<22} {s['prose_words']:>3} prose / {s['words']:>3} total{flag}")

    print("\n-- always-on budget (est tokens, desc) --")
    for b in bd["budget"]:
        print(f"  {b['file']:<40} {b['est_tokens']:>6} tok  {b['words']:>6} words")
    print(f"  {'TOTAL':<40} {bd['total_est_tokens']:>6} tok")

    print("\n-- single-home leaks (lines duplicated from global) --")
    if not bd["leaks"]:
        print("  none")
    for lk in bd["leaks"]:
        print(f"  {lk['repo']}: {lk['count']} duplicated line(s)")
        for ln in lk["lines"]:
            print(f"      · {ln[:100]}")

    print("\n-- header drift vs scaffold master --")
    for d in sorted(bd["drift"], key=lambda r: r["shared_with_scaffold"], reverse=True):
        tag = " (ignored one-off)" if d["ignored"] else ""
        print(f"  {d['repo']:<28} {d['shared_with_scaffold']}/{d['headers']} headers shared{tag}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
