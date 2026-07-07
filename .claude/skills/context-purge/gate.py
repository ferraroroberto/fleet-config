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

CLI (stdlib only; run from anywhere):
  gate    [--fleet] [--json]   — print TO_PURGE / UNCHANGED and a summary line
  advance [--fleet] [--date D] — recompute hashes for the surface, merge into
                                 the ledger (entries outside the scanned
                                 surface are preserved), upsert the issue

Pure helpers (`parse_ledger_block`, `render_ledger_block`, `diff_ledger`) are
unit-tested in tests/test_context_purge_gate.py without touching gh.
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
AUDIT_ISSUE = Path.home() / ".claude" / "skills" / "_lib" / "audit_issue.py"
LEDGER_REPO = "ferraroroberto/fleet-config"
KIND = "context-purge"
TITLE = "context-purge ledger"
BLOCK_MARKER = "<!-- context-purge-ledger -->"

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


# ---- surface enumeration ---------------------------------------------------

def _is_linked_worktree(repo_dir: Path) -> bool:
    git = repo_dir / ".git"
    return git.exists() and git.is_file()


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
            if _is_linked_worktree(repo_dir):
                continue
            add(repo_dir / "CLAUDE.md")
            for skill_md in sorted(repo_dir.glob(".claude/skills/*/SKILL.md")):
                add(skill_md)
    return out


def current_hashes(fleet: bool) -> dict[str, str]:
    return {key: file_hash(path.read_bytes()) for key, path in surface_files(fleet).items()}


# ---- ledger issue I/O (via audit_issue.py) ---------------------------------

def _audit_issue(*args: str) -> str:
    res = subprocess.run(
        [sys.executable, str(AUDIT_ISSUE), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
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
    sys.stdout.reconfigure(encoding="utf-8")  # UTF-8 under capture (fleet gotcha)
    ap = argparse.ArgumentParser(description="context-purge skip-unchanged ledger gate")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("gate", help="list files needing assessment vs unchanged")
    g.add_argument("--fleet", action="store_true")
    g.add_argument("--json", action="store_true")
    a = sub.add_parser("advance", help="record current hashes for the surface")
    a.add_argument("--fleet", action="store_true")
    a.add_argument("--date", default=_dt.date.today().isoformat())
    args = ap.parse_args()

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

    # advance: merge scanned keys over the existing ledger, keep foreign keys.
    merged = {**read_ledger(), **current}
    url = write_ledger(merged, args.date)
    print(f"ADVANCED: {len(current)} file(s) recorded — {url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
