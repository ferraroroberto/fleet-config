"""Group a `vendored_drift` scan by repo so a wave opens one PR per repo, not one per component.

A repo is a target for a component when it declared it and is behind the scaffold tip (or
drifted locally), or when it carries the component's files byte-identical to the tip
without declaring it (adopt). Everything else is reported, never targeted: a selector
the tip drops holds that component until a human names it in `allow_breaking`, and a
carrier that differs from the tip is a human call.

A repo behind on nothing can still need a PR: its `.ignore` block (ignore_block.py) is
missing or stale against its manifest. Those repos get their own `ignore_only` entry;
a repo already in the wave gets the block regenerated inside its one PR. A block that
is malformed or whose state could not be read is held for a human, never rewritten.

CLI:

  plan [--scaffold <path>] [--component a,b,...] [--allow-breaking a,b] [--part-of <url>]
       [--format json|text]
      Scan the fleet (every component when `--component` is omitted) and print the
      per-repo wave plan. Read-only; always exits 0, like `vendored_drift.py scan`.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, FrozenSet, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import vendored_drift as vd  # noqa: E402
from fleet_core.utf8_stdio import ensure_utf8_stdio  # noqa: E402

ensure_utf8_stdio()

SHORT = 7


def short(sha: Optional[str]) -> str:
    return (sha or "")[:SHORT]


def _entry(row: Dict[str, object], action: str, reason: str, files: List[str], old_sha: Optional[str]) -> Dict[str, object]:
    return {
        "component": row["component"], "action": action, "reason": reason,
        "src": row.get("src") or row.get("path"), "dest": row.get("dest") or row.get("path"),
        "old_sha": old_sha, "new_sha": row.get("head_sha"), "files": files,
    }


def _adopter_entry(row: Dict[str, object]) -> Optional[Dict[str, object]]:
    behind, drifted = bool(row["behind_head"]), bool(row["local_drift"])
    if not (behind or drifted):
        return None
    reason = "behind+local-drift" if behind and drifted else "behind" if behind else "local-drift"
    files = sorted(set(row["behind_diff_files"]) | set(row["local_diff_files"]))
    return _entry(row, "re-vendor", reason, files, str(row["pinned_sha"]))


def commit_subject(components: List[str], new_sha: str) -> str:
    what = components[0] if len(components) == 1 else f"{len(components)} components"
    return f"chore: re-vendor {what} from project-scaffolding@{short(new_sha)}"


def branch_name(components: List[str], new_sha: str) -> str:
    if len(components) == 1:
        return f"chore/revendor-{components[0]}-{short(new_sha)}"
    return f"chore/revendor-{short(new_sha)}"


def worktree_key(components: List[str], new_sha: str) -> str:
    return f"{components[0]}-{short(new_sha)}" if len(components) == 1 else f"revendor-{short(new_sha)}"


def commit_body(entries: List[Dict[str, object]]) -> str:
    return "\n".join(f"- {e['component']}: {short(e['old_sha']) or 'none'} -> {short(e['new_sha'])}" for e in entries)


def pr_body(entries: List[Dict[str, object]], new_sha: str, part_of: Optional[str]) -> str:
    """Markdown with one long line per paragraph. Never a closing keyword: "Part of" only, because GitHub's
    parser matches substrings and would close the scaffold's tracking issue on the first adopter merge."""
    ref = f"project-scaffolding@{short(new_sha)}"
    if len(entries) == 1:
        e = entries[0]
        old = f"`{short(e['old_sha'])}`" if e["old_sha"] else "not previously pinned"
        summary = (f"Byte-for-byte {e['action']} of `{e['component']}` ({old} -> `{short(e['new_sha'])}`) "
                   f"from {ref}, hash-verified.")
        listing = ""
    else:
        summary = f"Byte-for-byte re-vendor of {len(entries)} components from {ref}, each hash-verified."
        rows = [f"| `{e['component']}` | {e['action']} | {('`' + short(e['old_sha']) + '`') if e['old_sha'] else 'none'} "
                f"| `{short(e['new_sha'])}` | {len(e['files'])} |" for e in entries]
        listing = "\n\n| Component | Action | Old sha | New sha | Files changed |\n|---|---|---|---|---|\n" + "\n".join(rows)
    return (f"## Summary\n\n{summary}{listing}\n\nPart of {part_of or ref}\n\n## Test plan\n\n"
            "- [ ] every copied file's sha256 matches the scaffold source (per component)\n"
            "- [ ] the repo's verification gate passes\n")


def _ignore_entry(repo: str, state: str, new_sha: str, part_of: Optional[str]) -> Dict[str, object]:
    ref = part_of or f"project-scaffolding@{short(new_sha)}"
    body = ("## Summary\n\nGenerates the marked vendored-copies block in `.ignore` from the `[vendored]` dest paths in "
            "`.fleet.toml`, so agent search skips the vendored copies; hand-written lines are untouched and a direct "
            f"read of a vendored file still works.\n\nPart of {ref}\n\n"
            "## Test plan\n\n- [ ] `ignore_block.py check` reports `IGNORE=ok`\n"
            "- [ ] the repo's verification gate passes\n")
    return {"repo": repo, "state": state, "branch": "chore/vendored-ignore", "worktree_key": "vendored-ignore",
            "commit_subject": "chore: generate .ignore so agent search skips vendored copies",
            "commit_body": "- .ignore: marked block built from the .fleet.toml [vendored] dest paths", "pr_body": body}


def plan_wave(report: Dict[str, object], allow_breaking: FrozenSet[str] = frozenset(),
              part_of: Optional[str] = None) -> Dict[str, object]:
    """The per-repo plan for a `vendored_drift.scan_fleet` report."""
    per_repo: Dict[str, List[Dict[str, object]]] = {}
    held: List[Dict[str, object]] = []
    for row in report["adopters"]:
        entry = _adopter_entry(row)
        if entry is None:
            continue
        removed = row.get("contract", {}).get("removed_selectors") or []
        if row.get("contract", {}).get("breaking") and row["component"] not in allow_breaking:
            held.append({"repo": row["repo"], "component": row["component"], "pinned_sha": row["pinned_sha"],
                         "removed_selectors": removed})
            continue
        per_repo.setdefault(str(row["repo"]), []).append(entry)

    held_carriers: List[Dict[str, object]] = []
    for carrier in report["undeclared_carriers"]:
        if carrier["matches_head"]:
            row = {**carrier, "head_sha": report["head_sha"]}
            per_repo.setdefault(str(carrier["repo"]), []).append(_entry(row, "adopt", "adopt", [], None))
        else:
            held_carriers.append({k: carrier[k] for k in ("repo", "component", "path", "diff_files")})

    head = str(report["head_sha"])
    repos = []
    for repo in sorted(per_repo):
        entries = sorted(per_repo[repo], key=lambda e: str(e["component"]))
        names = [str(e["component"]) for e in entries]
        repos.append({
            "repo": repo, "components": entries,
            "branch": branch_name(names, head), "worktree_key": worktree_key(names, head),
            "commit_subject": commit_subject(names, head), "commit_body": commit_body(entries),
            "pr_body": pr_body(entries, head, part_of),
        })
    ignore_only: List[Dict[str, object]] = []
    held_ignore: List[Dict[str, str]] = []
    for repo, state in sorted(report.get("ignore_blocks", {}).items()):
        if state == "ok" or repo in per_repo:
            continue
        if state in ("missing", "stale"):
            ignore_only.append(_ignore_entry(repo, state, head, part_of))
        else:
            held_ignore.append({"repo": repo, "state": state})
    return {
        "scaffold_sha": head, "repos": repos, "held_contract": held, "held_carriers": held_carriers,
        "ignore_only": ignore_only, "held_ignore": held_ignore,
        "prs": len(repos) + len(ignore_only), "component_prs_before": sum(len(r["components"]) for r in repos),
        "coverage": report["coverage"], "errors": report["errors"],
    }


def render_text(plan: Dict[str, object]) -> str:
    """The dry-run report: per repo, the components it is behind on, then everything held back, then coverage."""
    lines = [f"/propagate-vendored wave plan - scaffold @ {short(str(plan['scaffold_sha']))}", ""]
    for r in plan["repos"]:
        n = len(r["components"])
        lines.append(f"  {r['repo']}   {n} component{'s' if n != 1 else ''} -> 1 PR ({r['branch']})")
        for e in r["components"]:
            if e["action"] == "adopt":
                lines.append(f"    {e['component']}   adopt (identical to HEAD)")
            else:
                pin = short(e["old_sha"])
                lines.append(f"    {e['component']}   pinned {pin}   {e['reason']}   ({len(e['files'])} files)")
    if not plan["repos"]:
        lines.append("  no repo is behind on the selected components")
    lines += ["", f"  PRs this wave opens: {plan['prs']}  (one per component per repo would open {plan['component_prs_before']})", ""]
    for i in plan["ignore_only"]:
        lines.append(f"  {i['repo']}   .ignore block {i['state']} -> 1 PR ({i['branch']})")
    for h in plan["held_ignore"]:
        lines.append(f"  HUMAN CALL (.ignore block {h['state']}): {h['repo']}")
    for h in plan["held_contract"]:
        lines.append(f"  HELD (selectors removed): {h['repo']} {h['component']} @ {short(h['pinned_sha'])}: "
                     + ", ".join(h["removed_selectors"]))
    for c in plan["held_carriers"]:
        lines.append(f"  HUMAN CALL (differs from HEAD): {c['repo']} {c['component']} ({len(c['diff_files'])} files)")
    cov = plan["coverage"]
    unknown = ", ".join(cov["carriers_unknown"]) or "none"
    lines.append(f"  coverage: {cov['declared_adopters']} declared adopters, {cov['undeclared_carriers']} undeclared "
                 f"carriers, unknown: {unknown}; .ignore drift: {len(cov['ignore_drift'])}; catalog {'known' if cov['catalog_known'] else 'NOT KNOWN - carrier detection did not run'}")
    if plan["errors"]:
        lines.append(f"  scan errors: {len(plan['errors'])} (see the JSON `errors`; never read as clean)")
    return "\n".join(lines) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Per-repo wave plan for /propagate-vendored (read-only).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan", help="one entry per repo, every component it is behind on")
    p.add_argument("--scaffold", default=vd.DEFAULT_SCAFFOLD, help="project-scaffolding root (the canonical source)")
    p.add_argument("--component", default=None, help="components separated by commas; omit for every component")
    p.add_argument("--allow-breaking", default="", help="components whose removed selectors a human approved")
    p.add_argument("--part-of", default=None, help="URL the PR body links as 'Part of'")
    p.add_argument("--format", choices=("json", "text"), default="json")
    args = ap.parse_args(argv)

    scaffold = Path(args.scaffold)
    if not scaffold.is_dir():
        sys.stdout.write(json.dumps({"error": f"scaffold not found: {scaffold}"}) + "\n")
        return 0
    report = vd.scan_fleet(scaffold, vd.parse_component_filter(args.component))
    allowed = vd.parse_component_filter(args.allow_breaking) or frozenset()
    plan = plan_wave(report, allowed, args.part_of)
    sys.stdout.write(render_text(plan) if args.format == "text" else json.dumps(plan, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
