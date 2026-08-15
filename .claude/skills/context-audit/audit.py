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
     for every `SKILL.md` **in the fleet**, flagged against the ~50-word cap.
     Trigger examples are exempt, so the prose count is the one that matters.
     A description that cannot be measured is reported as `unmeasured`, never
     folded into the compliant count (fleet-config#626).
  2. Always-on budget    — bytes / words / est-tokens of `global-CLAUDE.md` and
     every project `CLAUDE.md` under the fleet root, plus a fleet total.
  3. Single-home leaks   — substantial lines in a project `CLAUDE.md` that also
     appear (normalized) in `global-CLAUDE.md`: candidate duplications to review.
  4. Header inventory     — `##`/`###` headers per project `CLAUDE.md` and the
     overlap with the project-scaffolding master, for drift review. Projects in
     the ignore-list (deliberate one-offs) are still measured but tagged.

stdlib only. Run from the `fleet-config` repo root: `E:/automation/fleet-config/.venv/Scripts/python.exe .claude/skills/context-audit/audit.py`.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Tuple

# Repo root = .../fleet-config ; fleet root = .../automation (its parent).
REPO_ROOT = Path(__file__).resolve().parents[3]
FLEET_ROOT = REPO_ROOT.parent
GLOBAL_FILE = REPO_ROOT / "global-CLAUDE.md"
SCAFFOLD_FILE = FLEET_ROOT / "project-scaffolding" / "CLAUDE.md"

sys.path.insert(0, str(REPO_ROOT / "skills" / "_lib"))
from fleet_repo_scan import fleet_repos  # noqa: E402
from skill_description import frontmatter_description, prose_words, word_count  # noqa: E402

# The repo whose `skills/` tier is junctioned into every agent home — its
# descriptions are always-on in *every* repo's sessions, not just its own.
USER_TIER_REPO = "fleet-config"

DEFAULT_CAP = 50  # words; the ~50-word skill-description prose cap (#137).
# Deliberate one-offs that do NOT derive from project-scaffolding — drift vs the
# scaffold master is expected and should not be flagged. Tag, don't penalize.
DEFAULT_IGNORE = {"fleet-config", "project-scaffolding"}


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def _read_or_none(path: Path) -> Optional[str]:
    """`_read`, but distinguishing "could not read" from "read an empty file".

    The skill scan must never treat an unreadable file as measured-and-empty —
    that is the silent-working-set shrink fleet-config#626 is about.
    """
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _est_tokens(text: str) -> int:
    """Rough token estimate (~4 chars/token) — for tracking budget trend, not billing."""
    return round(len(text) / 4)


def _norm(line: str) -> str:
    return re.sub(r"\s+", " ", line.strip()).lower()


class SkillRoot(NamedTuple):
    """One directory of `*/SKILL.md` belonging to one fleet repo."""

    repo: str
    tier: str  # "user" (junctioned into every agent home) | "project"
    repo_dir: Path
    skills_dir: Path


def skill_roots(
    repos: Optional[Dict[str, Path]] = None,
    user_tier_repo: str = USER_TIER_REPO,
    self_root: Optional[Path] = None,
) -> List[SkillRoot]:
    """Every skills root in the fleet, in repo order.

    Membership comes from `fleet_repos()` — the same `hooks/projects.toml` list
    `/system-map` and `/config-map` read — so a repo added there is cap-checked
    the day it lands, with no second registry to keep in sync. Before
    fleet-config#626 this was a two-entry constant naming only *this* repo's two
    tiers, so ~20 sister-repo descriptions were never measured at all, while the
    audit still printed `over_cap=0`.

    `user_tier_repo` carries a second, `skills/` tier junctioned into every agent
    home: those descriptions are always-on in every repo's sessions, so they are
    measured under their owning repo but tagged `user`. `self_root` replaces that
    repo's directory for **both** its tiers — `projects.toml` names the *primary*
    checkout, and an audit run from a worktree must measure its own branch.
    """
    repos = fleet_repos() if repos is None else repos
    roots: List[SkillRoot] = []
    for name, path in sorted(repos.items()):
        if name == user_tier_repo:
            if self_root is not None:
                path = self_root
            roots.append(SkillRoot(name, "user", path, path / "skills"))
        roots.append(SkillRoot(name, "project", path, path / ".claude" / "skills"))
    return roots


def scan_skills(
    cap: int, roots: Optional[List[SkillRoot]] = None
) -> Tuple[List[dict], List[dict]]:
    """Measure every fleet skill description against `cap`.

    Returns `(rows, unmeasured)`. A `SKILL.md` the audit cannot read, or whose
    frontmatter carries no `description:`, lands in `unmeasured` — never in
    `rows`, and never counted compliant. A repo directory that is missing
    entirely is one `unmeasured` entry for the repo. A repo that simply has no
    skills at a tier is not unmeasured: there is nothing there to measure.
    """
    roots = skill_roots(self_root=REPO_ROOT) if roots is None else roots
    rows: List[dict] = []
    unmeasured: List[dict] = []
    missing_repos: set = set()

    for root in roots:
        if not root.repo_dir.is_dir():
            if root.repo not in missing_repos:
                missing_repos.add(root.repo)
                unmeasured.append(
                    {
                        "repo": root.repo,
                        "skill": "*",
                        "tier": root.tier,
                        "path": str(root.repo_dir),
                        "reason": "repo checkout not found",
                    }
                )
            continue
        if not root.skills_dir.is_dir():
            continue  # no skills at this tier — nothing to measure, not a gap
        for skill_md in sorted(root.skills_dir.glob("*/SKILL.md"), key=lambda p: p.parent.name):
            entry = {"repo": root.repo, "skill": skill_md.parent.name, "tier": root.tier}
            text = _read_or_none(skill_md)
            if text is None:
                unmeasured.append({**entry, "path": str(skill_md), "reason": "unreadable"})
                continue
            desc = frontmatter_description(text)
            if not desc:
                unmeasured.append(
                    {**entry, "path": str(skill_md), "reason": "no frontmatter description"}
                )
                continue
            prose = prose_words(desc)
            rows.append(
                {
                    **entry,
                    "words": word_count(desc),
                    "prose_words": prose,
                    "over_cap": prose > cap,
                }
            )
    return rows, unmeasured


def per_repo_summary(rows: List[dict], unmeasured: List[dict]) -> List[dict]:
    """One `{repo, skills, over_cap, unmeasured}` row per repo with any surface."""
    repos = sorted({r["repo"] for r in rows} | {u["repo"] for u in unmeasured})
    return [
        {
            "repo": name,
            "skills": sum(1 for r in rows if r["repo"] == name),
            "over_cap": sum(1 for r in rows if r["repo"] == name and r["over_cap"]),
            "unmeasured": sum(1 for u in unmeasured if u["repo"] == name),
        }
        for name in repos
    ]


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
                "words": word_count(text),
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

    skills, unmeasured = scan_skills(args.cap)
    by_repo = per_repo_summary(skills, unmeasured)
    bd = scan_budget_and_drift(DEFAULT_IGNORE)
    report = {
        "cap": args.cap,
        "skills": skills,
        "unmeasured": unmeasured,
        "skills_by_repo": by_repo,
        **bd,
    }

    if args.json:
        print(json.dumps(report, indent=2))
        return 0

    over = [s for s in skills if s["over_cap"]]
    print("=== context-audit ===")
    print(
        f"MANIFEST: skills={len(skills) + len(unmeasured)} "
        f"compliant={len(skills) - len(over)} over_cap={len(over)} "
        f"unmeasured={len(unmeasured)} repos={len(by_repo)} "
        f"claude_mds={len(bd['budget'])} leaks={len(bd['leaks'])} "
        f"total_est_tokens={bd['total_est_tokens']}"
    )

    print("\n-- skill descriptions (prose words, cap {}) --".format(args.cap))
    for s in sorted(skills, key=lambda r: (-r["prose_words"], r["repo"], r["skill"])):
        flag = "  ⚠️ OVER" if s["over_cap"] else ""
        label = f"{s['repo']}/{s['skill']}"
        print(f"  {label:<46} {s['prose_words']:>3} prose / {s['words']:>3} total{flag}")

    print("\n-- unmeasured descriptions (NOT compliant — could not be counted) --")
    if not unmeasured:
        print("  none")
    for u in unmeasured:
        print(f"  {u['repo']}/{u['skill']:<28} {u['reason']} ({u['path']})")

    print("\n-- per repo (skills / over cap / unmeasured) --")
    for r in by_repo:
        print(f"  {r['repo']:<32} {r['skills']:>3} skills  {r['over_cap']:>2} over  {r['unmeasured']:>2} unmeasured")

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
