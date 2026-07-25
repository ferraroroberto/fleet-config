"""Drift detection for `/propagate-vendored`'s `[vendored]` manifest (fleet-config#338).

A `project-scaffolding` component (a `_vendored/<name>/` UI folder, or a
machine-local single-file primitive like `tray_lifecycle.ps1`) is copied
verbatim into N adopter repos. Each adopter records what it copied and from
where in its own `.fleet.toml`'s `[vendored]` table:

    [vendored]
    nav = { src = "app/webapp/static/_vendored/nav", sha = "<scaffold commit>", dest = "app/webapp/static/_vendored/nav" }

This module answers, deterministically, the two questions that used to require
a manual per-repo audit (`project-scaffolding#144`: six trays silently missed
a fix for months):

  1. **local drift** — does the adopter's own copy still match the bytes it
     claims (`sha`) to have been vendored from? (a hand-edit, a partial copy,
     or a bit-rotted checkout would trip this)
  2. **behind HEAD** — is the pinned `sha` itself stale relative to the
     scaffold's current default-branch tip for that component's path? (the
     "who needs a re-vendor wave" question)

Both `/propagate-vendored --dry-run` and any future audit call `scan_fleet`
directly rather than re-deriving either check by hand.

Hashing is byte-exact (raw `subprocess` around `git show`, not the text-mode
`git_run` wrapper used for path listings) — `hash-verify` in the propagation
flow means "these are the identical bytes", not "these decode to the same
text", so a text round-trip through UTF-8 would be the wrong contract here.

CLI:

  scan [--scaffold <path>] [--component <name>]
      Enumerate every fleet repo's `.fleet.toml` (via the shared
      `fleet_repo_scan.fleet_repos()` list — same membership `/system-map` and
      `/config-map` use, so this never needs its own repo inventory), report
      one JSON object: which repos declare a `[vendored]` entry (with the two
      drift signals per entry), and which don't (informational — "no manifest
      yet", the expected answer for every current fleet repo before this
      skill's first real run). `--component` filters to one component name.

This CLI always exits 0 — the caller reads `errors`/`no_manifest` in the JSON
rather than relying on a process exit code, matching `fleet_audit_scan.py`'s
"one JSON object, orchestrator reads it" shape.

stdlib + the `git` CLI only (matches the `skills/_lib` module contract).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fleet_repo_scan  # noqa: E402
import git_run  # noqa: E402
from no_window import NO_WINDOW  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):  # UTF-8 even when stdout is captured (cp1252 fallback)
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[union-attr]


# ---- pure helpers (unit-tested without git) --------------------------------

def parse_vendored_manifest(toml_text: str) -> Dict[str, Dict[str, str]]:
    """Parse a `.fleet.toml`'s optional `[vendored]` table.

    Returns ``{component: {"src": ..., "sha": ..., "dest": ...}}``; ``{}``
    when the repo has no `[vendored]` table at all (the state of every fleet
    repo today — no adopter has run `/propagate-vendored` yet) or the table is
    present but empty. An entry that isn't a table (malformed TOML authored by
    hand) is dropped rather than raising — this is a read-only detector, not
    the schema validator; a malformed entry just can't be drift-checked and
    surfaces as "missing src/sha/dest" from the caller instead.
    """
    data = tomllib.loads(toml_text)
    vendored = data.get("vendored")
    if not isinstance(vendored, dict):
        return {}
    return {name: dict(entry) for name, entry in vendored.items() if isinstance(entry, dict)}


def diff_hashes(a: Dict[str, str], b: Dict[str, str]) -> List[str]:
    """Sorted relpaths where the two hash maps disagree — present in only one
    side, or present in both with a different hash. Empty on a byte-identical
    match (including the degenerate case where both sides are empty)."""
    keys = set(a) | set(b)
    return sorted(k for k in keys if a.get(k) != b.get(k))


def classify_adopter(local: Dict[str, str], pinned: Dict[str, str], head: Dict[str, str]) -> Dict[str, object]:
    """Given three relpath->sha256 maps — the adopter's local copy, the
    scaffold at the pinned `sha`, and the scaffold at its current HEAD for the
    same path — report the two independent drift signals.

    They are independent on purpose: a repo can be freshly re-vendored (no
    `local_drift`) yet already behind a same-day scaffold fix (`behind_head`),
    or vice versa (a local hand-edit on top of an otherwise up-to-date pin).
    """
    local_diff = diff_hashes(local, pinned)
    head_diff = diff_hashes(pinned, head)
    return {
        "local_drift": bool(local_diff),
        "local_diff_files": local_diff,
        "behind_head": bool(head_diff),
        "behind_diff_files": head_diff,
    }


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---- IO layer ---------------------------------------------------------------

def hash_dir_local(path: Path) -> Dict[str, str]:
    """relpath (posix, `path.name` for a single-file component) -> sha256 hex
    for every file under `path`. `{}` when `path` doesn't exist — the manifest
    says it was vendored but the dest was never actually written, or has since
    been deleted; that shows up as 100% `local_diff_files` from the caller,
    which is the correct signal."""
    if path.is_file():
        return {path.name: sha256_bytes(path.read_bytes())}
    if not path.is_dir():
        return {}
    out: Dict[str, str] = {}
    for p in sorted(path.rglob("*")):
        if p.is_file():
            out[p.relative_to(path).as_posix()] = sha256_bytes(p.read_bytes())
    return out


def _git_show_bytes(repo: Path, ref: str, relpath: str) -> Optional[bytes]:
    """Raw bytes of `relpath` as committed at `ref` — deliberately bypasses
    `git_run` (text-mode, UTF-8-decoded) so a hash comparison is byte-exact.
    `None` on any git failure (bad ref, path absent at that commit, ...)."""
    proc = subprocess.run(
        ["git", "-C", str(repo), "show", f"{ref}:{relpath}"],
        capture_output=True, check=False, creationflags=NO_WINDOW,
    )
    return proc.stdout if proc.returncode == 0 else None


def hash_dir_at_ref(scaffold_root: Path, ref: str, subpath: str) -> Dict[str, str]:
    """relpath -> sha256 hex for every file under `subpath` as committed at
    `ref` in the scaffold repo. Works for both a directory-shaped component
    (`app/webapp/static/_vendored/nav`, keyed by path relative to `subpath`)
    and a single-file one (`app/tray/tray_lifecycle.ps1`, keyed by basename —
    matching `hash_dir_local`'s single-file case). `{}` when `subpath` didn't
    exist at `ref` (or any other git failure) — a legitimate answer, not an
    error: the caller reports it as 100%-differing rather than raising."""
    listing = git_run.run_git(["-C", str(scaffold_root), "ls-tree", "-r", "--name-only", ref, "--", subpath])
    if listing.returncode != 0:
        return {}
    out: Dict[str, str] = {}
    prefix = subpath.rstrip("/") + "/"
    for rel in (ln.strip() for ln in listing.stdout.splitlines()):
        if not rel:
            continue
        blob = _git_show_bytes(scaffold_root, ref, rel)
        if blob is None:
            continue
        relpath = rel[len(prefix):] if rel.startswith(prefix) else Path(rel).name
        out[relpath] = sha256_bytes(blob)
    return out


def resolve_ref_sha(repo: Path, ref: str) -> Optional[str]:
    """Full commit sha `ref` currently resolves to, or `None` on failure."""
    res = git_run.run_git(["-C", str(repo), "rev-parse", ref])
    return res.stdout.strip() if res.returncode == 0 else None


def scan_fleet(
    scaffold_root: Path,
    component_filter: Optional[str] = None,
    repos: Optional[Dict[str, Path]] = None,
) -> Dict[str, object]:
    """Enumerate every fleet repo's `[vendored]` manifest and report drift.

    `repos` defaults to `fleet_repo_scan.fleet_repos()` (the same
    `hooks/projects.toml`-derived, `architecture_ignore`-respecting membership
    `/system-map` and `/config-map` use) — injectable for hermetic testing
    over a synthetic repo set. The scaffold repo itself is skipped (never its
    own adopter, even if it happens to be in the fleet list).

    Reads each repo's **working-tree** `.fleet.toml` (not committed-at-ref) —
    deliberately: this is a live local-fleet drift check a human/skill runs
    against the actual checkouts on disk, unlike the map-build's
    committed-snapshot read (`system-map/build_data.py` reads `git show
    <ref>:.fleet.toml` because it renders what's *shipped*, not what's
    mid-edit).
    """
    if repos is None:
        repos = fleet_repo_scan.fleet_repos()
    scaffold_resolved = scaffold_root.resolve()
    head_ref = fleet_repo_scan.default_ref(scaffold_root) or "HEAD"
    head_sha = resolve_ref_sha(scaffold_root, head_ref) or head_ref
    head_cache: Dict[str, Dict[str, str]] = {}

    adopters: List[Dict[str, object]] = []
    no_manifest: List[str] = []
    errors: List[Dict[str, str]] = []

    for repo_name, repo_dir in sorted(repos.items()):
        if repo_dir.resolve() == scaffold_resolved:
            continue
        toml_path = repo_dir / ".fleet.toml"
        if not toml_path.is_file():
            no_manifest.append(repo_name)
            continue
        try:
            manifest = parse_vendored_manifest(toml_path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            errors.append({"repo": repo_name, "error": f"unreadable/invalid .fleet.toml: {exc}"})
            continue
        if not manifest:
            no_manifest.append(repo_name)
            continue

        for component, entry in manifest.items():
            if component_filter and component != component_filter:
                continue
            src, sha, dest = entry.get("src"), entry.get("sha"), entry.get("dest")
            if not (src and sha and dest):
                errors.append({
                    "repo": repo_name, "component": component,
                    "error": "manifest entry missing src/sha/dest",
                })
                continue

            pinned = hash_dir_at_ref(scaffold_root, sha, src)
            if src not in head_cache:
                head_cache[src] = hash_dir_at_ref(scaffold_root, head_ref, src)
            local = hash_dir_local(repo_dir / dest)
            result = classify_adopter(local, pinned, head_cache[src])
            adopters.append({
                "repo": repo_name, "component": component,
                "src": src, "dest": dest,
                "pinned_sha": sha, "head_sha": head_sha,
                **result,
            })

    return {
        "scaffold": str(scaffold_root),
        "head_ref": head_ref,
        "head_sha": head_sha,
        "repos_scanned": len(repos),
        "adopters": adopters,
        "no_manifest": sorted(no_manifest),
        "errors": errors,
    }


# ---- CLI ---------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Vendored-component [vendored]-manifest drift detector for /propagate-vendored."
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_scan = sub.add_parser("scan", help="whole-fleet [vendored] drift report (JSON)")
    p_scan.add_argument("--scaffold", default="E:/automation/project-scaffolding",
                         help="project-scaffolding root (the canonical source)")
    p_scan.add_argument("--component", default=None, help="restrict to one component name")

    args = ap.parse_args(argv)
    if args.cmd == "scan":
        scaffold = Path(args.scaffold)
        if not scaffold.is_dir():
            print(json.dumps({"error": f"scaffold not found: {scaffold}"}))
            return 0
        print(json.dumps(scan_fleet(scaffold, args.component), indent=2))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
