"""Ledger gate for `/context-purge` — skip files unmodified since the last run.

The purge must never rewrite the same unchanged file week after week. This
gate keys on **content hashes** (sha256, first 12 hex chars) rather than git
SHAs so it works identically across every fleet repo and survives rebases:
a file re-enters the to-purge list only when its bytes actually changed since
it was last assessed (purged *or* deliberately judged already-lean — both
count as assessed and advance the ledger).

The ledger is one GitHub issue in `ferraroroberto/fleet-config` — title
`context-purge ledger`, label `audit-meta`, `kind=context-purge` — managed
through `skills/_lib/audit_issue.py` like every other fleet ledger. Its body
carries a machine-readable block:

    <!-- context-purge-ledger -->
    last-run-at: <YYYY-MM-DD>
    fleet-config/global-CLAUDE.md: a1b2c3d4e5f6
    <repo>/<relpath>: <hash12>
    ...

Surface enumeration:
  default  — the fleet-config-owned surface: global-CLAUDE.md, CLAUDE.md,
             skills/*/SKILL.md, .claude/skills/*/SKILL.md.
  --fleet  — additionally every sister repo under E:\\automation\\: its
             CLAUDE.md + any .claude/skills/*/SKILL.md. Linked worktrees
             (`.git` is a file) are skipped.

CLI (run from anywhere; `reconcile` also shells out to `gh`):
  gate      [--fleet] [--json]   — print TO_PURGE / UNCHANGED and a summary
  advance   [--fleet] [--date D] — recompute hashes for the surface, merge
                                    into the ledger (entries outside the
                                    scanned surface are preserved), upsert
  reconcile [--fleet] [--dry-run] [--json]
      — sync the ledger against every `chore/context-purge-*` PR's actual
        outcome (fleet-config#757): a merged PR's files get re-hashed to
        their content *at that PR's own merge commit* (never `main`'s
        ever-moving tip); a closed-unmerged PR's files have their
        (never-landed) ledger entry dropped so they re-enter `to_purge`;
        an open PR is left untouched and reported in the backlog instead.
        Run before `gate` — an abandoned or still-open purge PR must not
        keep suppressing its files forever.

Pure helpers (`parse_ledger_block`, `render_ledger_block`, `diff_ledger`,
`plan_reconcile`, `classify_pr`) are unit-tested in
tests/test_context_purge_gate.py without touching gh.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent
REPO_ROOT = SKILL_DIR.parents[2]
FLEET_ROOT = REPO_ROOT.parent

sys.path.insert(0, str(REPO_ROOT / "skills" / "_lib"))
import git_run  # noqa: E402
from fleet_repo_scan import is_linked_worktree  # noqa: E402
from no_window import NO_WINDOW  # noqa: E402
from utf8_stdio import ensure_utf8_stdio  # noqa: E402

# Repo-relative (fleet-config#502), matching every other _lib cross-reference
# in the tree (e.g. .claude/skills/learning-log/gather.py's HELPER) — the
# home-junction form would execute whatever's junctioned into ~/.claude
# rather than the checkout this script itself lives in, so a run from one of
# this repo's own <repo>-wt-N worktrees would silently use main's helper.
AUDIT_ISSUE = REPO_ROOT / "skills" / "_lib" / "audit_issue.py"
LEDGER_REPO = "ferraroroberto/fleet-config"
KIND = "context-purge"
TITLE = "context-purge ledger"
BLOCK_MARKER = "<!-- context-purge-ledger -->"
# fleet-config#757: the only signal tying a ledger entry back to the PR that
# assessed it — the ledger itself records no PR/branch metadata, so this
# prefix (the one `/context-purge`'s SKILL.md commits every purge branch to)
# is how `reconcile()` finds candidate PRs at all.
PURGE_BRANCH_PREFIX = "chore/context-purge-"

_ENTRY_RE = re.compile(r"^([^:#\s][^:]*?):[ \t]*([0-9a-f]{12})[ \t]*$", re.MULTILINE)
_RUN_AT_RE = re.compile(r"^last-run-at:[ \t]*(\S+)[ \t]*$", re.MULTILINE)


# ---- pure helpers (unit-tested without gh) --------------------------------

def file_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:12]


def parse_ledger_block(body: str) -> dict[str, str]:
    """`<path>: <hash12>` entries after the block marker; {} when absent."""
    idx = body.find(BLOCK_MARKER)
    if idx == -1:
        return {}
    return {m.group(1).strip(): m.group(2) for m in _ENTRY_RE.finditer(body[idx:])}


def render_ledger_block(hashes: dict[str, str], run_at: str) -> str:
    lines = [BLOCK_MARKER, f"last-run-at: {run_at}"]
    lines += [f"{path}: {digest}" for path, digest in sorted(hashes.items())]
    return "\n".join(lines) + "\n"


def diff_ledger(current: dict[str, str], ledger: dict[str, str]) -> tuple[list[str], list[str]]:
    """(to_purge, unchanged): new-or-modified files vs hash-identical ones."""
    to_purge = [p for p, h in sorted(current.items()) if ledger.get(p) != h]
    unchanged = [p for p, h in sorted(current.items()) if ledger.get(p) == h]
    return to_purge, unchanged


def select_assessed(current: dict[str, str], only: list[str] | None) -> dict[str, str]:
    """Narrow `current` to the files actually assessed this run.

    A fleet run is normally partial — the skill says to skip already-lean
    files and to degrade rather than block — so recording the whole surface
    would mark never-read files as assessed and silently suppress them from
    every future run. `advance --only` records just what was assessed.
    Unknown keys are an error, not a silent no-op (a typo'd path would
    otherwise record nothing and read as success).
    """
    if only is None:
        return current
    unknown = sorted(set(only) - set(current))
    if unknown:
        raise KeyError(f"not in the scanned surface: {', '.join(unknown)}")
    return {k: current[k] for k in only}


# ---- PR reconciliation (fleet-config#757) ----------------------------------
#
# `advance` upserts unconditionally, with no reference to whether the PR that
# assessed a file ever reached `main` — a purge PR that is abandoned still
# marks its files assessed, silently suppressing them from every future gate
# forever. `plan_reconcile` is the pure decision core (unit-tested without gh
# or git): given the ledger and every already-fetched purge-branch PR (each
# carrying its changed files, and — for a merged PR — the file's hash *at
# that PR's own merge commit*, not wherever `main`'s tip has since moved to),
# it decides what the ledger should become. The impure `reconcile()` below
# does the fetching and applies the plan.


def is_purge_branch(head_ref: str) -> bool:
    return bool(head_ref) and head_ref.startswith(PURGE_BRANCH_PREFIX)


def classify_pr(pr: dict) -> str:
    """`merged` | `closed` | `open` from a `gh pr list --json ...` record."""
    if pr.get("state") == "MERGED" or pr.get("mergedAt"):
        return "merged"
    if pr.get("state") == "CLOSED":
        return "closed"
    return "open"


def pr_age_days(created_at: str, today: _dt.date) -> "int | None":
    """Whole days since a PR's ISO-8601 UTC `createdAt`, or None if unparseable."""
    if not created_at:
        return None
    try:
        created = _dt.datetime.fromisoformat(created_at.replace("Z", "+00:00")).date()
    except ValueError:
        return None
    return (today - created).days


def plan_reconcile(ledger: dict[str, str], prs: list[dict], today: _dt.date,
                    surface: "set[str] | None" = None) -> dict:
    """Decide ledger updates + the open-PR backlog from already-fetched PRs.

    `surface`, when given, restricts every file-level update to ledger keys
    gate.py actually tracks (`surface_files()`'s keys). A purge PR's diff can
    include files outside that surface — project-scaffolding PR #251 also
    touched `docs/agents/CLAUDE.master.md`, which isn't a `CLAUDE.md`/
    `SKILL.md` the gate scans at all — and `advance --only` already refuses
    an unknown path outright (`select_assessed`); reconcile must hold the
    same line rather than quietly inventing a ledger entry `gate`/`advance`
    would never have created on their own (fleet-config#757).

    `prs` — one dict per PR, already scoped to `is_purge_branch`-matching
    branches: `{"repo": <ledger-key repo segment>, "number", "title", "url",
    "created_at", "state", "mergedAt", "files": [<repo-relative path>, ...],
    "main_hashes": {<path>: <hash12>}}` (`main_hashes` only needed/used for a
    merged PR — its absence for other states is not a bug).

    Returns `{"updates": {<ledger key>: <hash12> | None}, "backlog": [...]}`.
    `updates[key] is None` means "delete this ledger entry" (an abandoned
    PR's assessment must not keep suppressing the file). A merged PR's file
    is keyed to its hash *at that PR's own merge commit* (`main_hashes`),
    never to whatever hash the original `advance` call happened to record
    mid-flight, and never to whatever `main`'s tip happens to hold when this
    runs — an active fleet has unrelated commits landing on `main`
    constantly, and crediting a purge PR with content it never touched would
    silently mark a since-drifted file "unchanged" again.

    Two files can each be touched by more than one purge PR over time, and
    the two orderings matter differently:

    - **Same file, one merged + one closed** — the merged result always
      wins, independent of fetch/iteration order (an abandoned attempt, e.g.
      fleet-config PR #702 closed-unmerged on `CLAUDE.md`, does not get to
      erase a later successful merge, e.g. PR #754, of the same file).
    - **Same file, two merged PRs** — the *chronologically later* merge
      wins (a second, more recent purge legitimately supersedes an earlier
      one's hash). `prs` is sorted by `mergedAt`/`created_at` before
      processing so plain dict-overwrite resolves this correctly.

    Both orderings were real bugs caught by running this against live fleet
    data before shipping it (fleet-config#757) — the second is exactly why
    `main_hashes` must be commit-anchored rather than tip-anchored: three
    sister repos each had an old (2026-08-15) *merged* purge alongside a
    newer (2026-08-22) *abandoned* one for the same `CLAUDE.md`, and a
    tip-anchored hash could not tell "the merge is still what's on main"
    apart from "an unrelated later commit changed it again".
    """
    ordered = sorted(prs, key=lambda pr: pr.get("mergedAt") or pr.get("created_at") or "")
    merged_updates: dict[str, str] = {}
    closed_keys: set[str] = set()
    backlog: list[dict] = []
    for pr in ordered:
        repo = pr["repo"]
        kind = classify_pr(pr)
        if kind == "merged":
            for path in pr.get("files", []):
                key = f"{repo}/{path}"
                if surface is not None and key not in surface:
                    continue
                h = (pr.get("main_hashes") or {}).get(path)
                if h is not None:
                    merged_updates[key] = h
        elif kind == "closed":
            for path in pr.get("files", []):
                key = f"{repo}/{path}"
                if surface is not None and key not in surface:
                    continue
                closed_keys.add(key)
        else:  # open — safe default is to re-offer, never silently suppress
            backlog.append({
                "repo": repo, "number": pr.get("number"), "title": pr.get("title"),
                "url": pr.get("url"), "created_at": pr.get("created_at"),
                "age_days": pr_age_days(pr.get("created_at", ""), today),
            })
    updates: dict[str, "str | None"] = {
        key: None for key in closed_keys if key in ledger and key not in merged_updates
    }
    updates.update(merged_updates)
    return {"updates": updates, "backlog": backlog}


# ---- surface enumeration ---------------------------------------------------

def surface_files(fleet: bool) -> dict[str, Path]:
    """{ledger key: absolute path} for the requested surface."""
    out: dict[str, Path] = {}

    def add(path: Path) -> None:
        if path.is_file():
            out[path.relative_to(FLEET_ROOT).as_posix()] = path

    add(REPO_ROOT / "global-CLAUDE.md")
    add(REPO_ROOT / "CLAUDE.md")
    for tier in (REPO_ROOT / "skills", REPO_ROOT / ".claude" / "skills"):
        for skill_md in sorted(tier.glob("*/SKILL.md")):
            add(skill_md)

    if fleet:
        for repo_dir in sorted(p for p in FLEET_ROOT.iterdir() if p.is_dir()):
            if repo_dir == REPO_ROOT or not (repo_dir / ".git").exists():
                continue
            if is_linked_worktree(repo_dir):
                continue
            add(repo_dir / "CLAUDE.md")
            for skill_md in sorted(repo_dir.glob(".claude/skills/*/SKILL.md")):
                add(skill_md)
    return out


def current_hashes(fleet: bool) -> dict[str, str]:
    return {key: file_hash(path.read_bytes()) for key, path in surface_files(fleet).items()}


def _repo_dirs(fleet: bool) -> dict[str, Path]:
    """{repo dir name: repo dir} for every repo in scope for reconciliation.

    Same repos `surface_files` draws from (their first ledger-key segment is
    always the repo dir name), so a PR touching a file the ledger already
    tracks is always found under a repo this enumerates.
    """
    out = {REPO_ROOT.name: REPO_ROOT}
    if fleet:
        for key in surface_files(True):
            name = key.split("/", 1)[0]
            out.setdefault(name, FLEET_ROOT / name)
    return out


def _commit_hashes(repo_dir: Path, commit_sha: str, relpaths: list[str]) -> dict[str, "str | None"]:
    """Every path's hash as it would check out from one specific commit.

    Deliberately **not** "whatever `main` holds right now": `main` moves —
    unrelated commits land on it constantly in an active fleet — so anchoring
    to the *current* tip would credit a merged purge PR's file with whatever
    unrelated edits happened to land on it afterward, silently marking a
    since-drifted file "unchanged" (caught live: several fleet-config files
    the ledger already had correct hashes for showed as "changed" against
    `origin/main` purely because unrelated PRs — #760/#761/#762/#763 — merged
    in between, fleet-config#757). The merge commit itself is the one point
    in history that's actually provable as "this is what got assessed".

    **Also deliberately not `git show <sha>:<path>`** (the raw blob): a repo
    with `core.autocrlf=true` (life-os, confirmed live) stores blobs LF-only
    and smudges to CRLF only on checkout, while `current_hashes()` (the rest
    of this module, unchanged) hashes the real **working-tree** bytes —
    CRLF, for such a repo. Hashing the raw blob here would silently diverge
    from what `gate`/`advance` will ever compute again, permanently
    re-flagging every such file `to_purge` after this "fix" runs (caught
    live: every single merged-PR file in every repo showed as "changed" this
    way, which is what a systematic hashing bug looks like, not real
    fleet-wide drift). A throwaway detached worktree is git itself doing the
    checkout-time smudge, so the hash this produces is exactly what
    `current_hashes()` would compute if this commit were checked out for
    real — one `worktree add`/`remove` for the whole commit, not per file.
    """
    out: dict[str, "str | None"] = {p: None for p in relpaths}
    tmp = Path(tempfile.mkdtemp(prefix="ctxpurge_reconcile_"))
    tmp.rmdir()  # `worktree add` wants to create the leaf itself
    added = git_run.run_git(
        ["-C", str(repo_dir), "worktree", "add", "--detach", "--force", str(tmp), commit_sha],
        timeout=180,
    )
    if added.returncode != 0:
        return out
    try:
        for relpath in relpaths:
            p = tmp / relpath
            if p.is_file():
                out[relpath] = file_hash(p.read_bytes())
    finally:
        git_run.run_git(["-C", str(repo_dir), "worktree", "remove", "--force", str(tmp)], timeout=60)
    return out


def _fetch_purge_prs(repo_name: str, repo_dir: Path) -> list[dict]:
    """Every `chore/context-purge-*` PR (any state) for one repo, `gh`-fetched.

    One `gh pr list` call per repo carries `files` inline — no per-PR
    `gh pr view` round trip needed. A merged PR's files are additionally
    resolved to their hash *at that PR's own merge commit* here (the only
    place that needs a repo checkout), so `plan_reconcile` stays git/gh-free.
    """
    res = git_run.run_gh(
        ["pr", "list", "--repo", f"ferraroroberto/{repo_name}", "--state", "all",
         "--json", "number,title,url,headRefName,state,mergedAt,createdAt,mergeCommit,files",
         "--limit", "200"],
        timeout=120,
    )
    if res.returncode != 0:
        raise RuntimeError(f"gh pr list --repo ferraroroberto/{repo_name} failed: "
                            f"{(res.stderr or res.stdout).strip()}")
    out: list[dict] = []
    for pr in json.loads(res.stdout or "[]"):
        if not is_purge_branch(pr.get("headRefName", "")):
            continue
        entry = {
            "repo": repo_name, "number": pr.get("number"), "title": pr.get("title"),
            "url": pr.get("url"), "created_at": pr.get("createdAt"),
            "state": pr.get("state"), "mergedAt": pr.get("mergedAt"),
            "files": [f["path"] for f in pr.get("files", [])],
        }
        merge_sha = (pr.get("mergeCommit") or {}).get("oid")
        if classify_pr(entry) == "merged" and merge_sha:
            entry["main_hashes"] = _commit_hashes(repo_dir, merge_sha, entry["files"])
        out.append(entry)
    return out


def reconcile(fleet: bool, dry_run: bool = False) -> dict:
    """Fetch purge PRs fleet-wide (or fleet-config-only), plan, and apply.

    Applying means: a merged PR's files get their ledger hash refreshed to
    what that PR's own merge commit put there (so they correctly read
    `unchanged` next week, unless a *later* unrelated commit moved them on
    again — in which case that's exactly what should re-surface as
    `to_purge`); a closed-unmerged PR's files have their ledger entry dropped (so
    they re-enter `to_purge` instead of staying silently suppressed). An
    open PR touches nothing — it is reported in `backlog` instead, the
    "safe default is to re-offer, so don't touch it while a human might
    still be reviewing it" middle state (fleet-config#757).
    """
    ledger = read_ledger()
    surface = set(surface_files(fleet))
    prs: list[dict] = []
    for name, repo_dir in sorted(_repo_dirs(fleet).items()):
        if not (repo_dir / ".git").exists():
            continue
        prs.extend(_fetch_purge_prs(name, repo_dir))
    plan = plan_reconcile(ledger, prs, _dt.date.today(), surface=surface)
    plan["applied"] = False
    if plan["updates"] and not dry_run:
        merged = dict(ledger)
        for key, value in plan["updates"].items():
            if value is None:
                merged.pop(key, None)
            else:
                merged[key] = value
        plan["ledger_url"] = write_ledger(merged, _dt.date.today().isoformat())
        plan["applied"] = True
    return plan


# ---- ledger issue I/O (via audit_issue.py) ---------------------------------

def _audit_issue(*args: str) -> str:
    res = subprocess.run(
        [sys.executable, str(AUDIT_ISSUE), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        creationflags=NO_WINDOW,
    )
    if res.returncode != 0:
        raise RuntimeError(f"audit_issue.py {args[0]} failed: {(res.stderr or res.stdout).strip()}")
    return res.stdout


def read_ledger() -> dict[str, str]:
    data = json.loads(_audit_issue("get", "--repo", LEDGER_REPO, "--kind", KIND))
    return parse_ledger_block(data.get("body") or "")


def write_ledger(hashes: dict[str, str], run_at: str) -> str:
    body = (
        f"Machine-readable state for `/context-purge`'s skip-unchanged gate — one `<path>: <hash>` line per assessed file (sha256/12). "
        f"Managed by `.claude/skills/context-purge/gate.py`; do not hand-edit the block.\n\n"
        f"{render_ledger_block(hashes, run_at)}"
    )
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as fh:
        fh.write(body)
        tmp = fh.name
    out = _audit_issue(
        "upsert", "--repo", LEDGER_REPO, "--kind", KIND,
        "--label", "audit-meta", "--title", TITLE, "--body-file", tmp,
    )
    Path(tmp).unlink(missing_ok=True)
    return out.strip()


# ---- CLI --------------------------------------------------------------------

def main() -> int:
    ensure_utf8_stdio()  # UTF-8 under capture (fleet gotcha) — stdout + stderr
    ap = argparse.ArgumentParser(description="context-purge skip-unchanged ledger gate")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("gate", help="list files needing assessment vs unchanged")
    g.add_argument("--fleet", action="store_true")
    g.add_argument("--json", action="store_true")
    a = sub.add_parser("advance", help="record current hashes for the surface")
    a.add_argument("--fleet", action="store_true")
    a.add_argument("--date", default=_dt.date.today().isoformat())
    a.add_argument(
        "--only", nargs="+", metavar="PATH",
        help="record only these ledger keys (the files actually assessed this "
             "run); default records the whole scanned surface",
    )
    r = sub.add_parser(
        "reconcile",
        help="sync the ledger against merged/closed purge PRs; report the open backlog",
    )
    r.add_argument("--fleet", action="store_true")
    r.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    r.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.cmd == "reconcile":
        plan = reconcile(args.fleet, dry_run=args.dry_run)
        if args.json:
            print(json.dumps(plan, indent=2))
            return 0
        merged_syncs = sum(1 for v in plan["updates"].values() if v is not None)
        closed_clears = sum(1 for v in plan["updates"].values() if v is None)
        for backlog in plan["backlog"]:
            age = backlog["age_days"]
            age_str = "unknown" if age is None else f"{age}d"
            print(f"OPEN_PURGE_PR  {backlog['repo']}#{backlog['number']} "
                  f"({age_str} old) — {backlog['url']}")
        verb = "would record" if args.dry_run else "recorded"
        print(f"RECONCILE: {verb} {merged_syncs} merged sync(s), "
              f"cleared {closed_clears} abandoned entry(ies), "
              f"{len(plan['backlog'])} PR(s) still open"
              + (f" — {plan['ledger_url']}" if plan.get("ledger_url") else ""))
        return 0

    current = current_hashes(args.fleet)

    if args.cmd == "gate":
        ledger = read_ledger()
        to_purge, unchanged = diff_ledger(current, ledger)
        if args.json:
            print(json.dumps({"to_purge": to_purge, "unchanged": unchanged}, indent=2))
        else:
            for p in to_purge:
                print(f"TO_PURGE  {p}")
            print(f"SUMMARY: to_purge={len(to_purge)} unchanged={len(unchanged)} surface={len(current)}")
        return 0

    # advance: merge assessed keys over the existing ledger, keep foreign keys.
    try:
        assessed = select_assessed(current, args.only)
    except KeyError as exc:
        print(f"advance: {exc}", file=sys.stderr)
        return 1
    merged = {**read_ledger(), **assessed}
    url = write_ledger(merged, args.date)
    print(f"ADVANCED: {len(assessed)} file(s) recorded — {url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
