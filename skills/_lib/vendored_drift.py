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

Both questions are about repos that *declared* the component. A third one is
about the repos that didn't (project-scaffolding#230):

  3. **undeclared carriers** — which repos hold a known component's files at
     the scaffold's own path while declaring nothing? They are invisible to
     `/propagate-vendored`, whose adopter list comes from `[vendored]` entries,
     so a wave re-vendors the declarers, reports success, and leaves the rest
     stale with nobody told. `#228` reached one repo out of seven that way.
     Answering it needs the other half of the manifest picture: the scaffold's
     own `[components]` catalog, the canonical `key -> src` map of what it
     publishes (`parse_scaffold_catalog`).

Both `/propagate-vendored --dry-run` and any future audit call `scan_fleet`
directly rather than re-deriving any of these checks by hand.

Hashing is byte-exact (`git_run.run_git_bytes` around `git show`, not the
text-mode `git_run.run_git` used for path listings) — `hash-verify` in the propagation
flow means "these are the identical bytes", not "these decode to the same
text", so a text round-trip through UTF-8 would be the wrong contract here.

CLI:

  scan [--scaffold <path>] [--component <name>]
      Enumerate every fleet repo's `.fleet.toml` (via the shared
      `fleet_repo_scan.fleet_repos()` list — same membership `/system-map` and
      `/config-map` use, so this never needs its own repo inventory), report
      one JSON object: which repos declare a `[vendored]` entry (with the two
      drift signals per entry), which don't (informational — "no manifest
      yet", the expected answer for every current fleet repo before this
      skill's first real run), which carry a catalogued component without
      declaring it (`undeclared_carriers`), and a `coverage` block a caller
      must state out loud so a partial wave can never read as a complete one.
      `--component` filters to one component name.

This CLI always exits 0 — the caller reads `errors`/`no_manifest` in the JSON
rather than relying on a process exit code, matching `fleet_audit_scan.py`'s
"one JSON object, orchestrator reads it" shape.

stdlib + the `git` CLI only (matches the `skills/_lib` module contract).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tomllib
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fleet_repo_scan  # noqa: E402
import git_run  # noqa: E402
from utf8_stdio import ensure_utf8_stdio  # noqa: E402

ensure_utf8_stdio()


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


def parse_scaffold_catalog(toml_text: str) -> Dict[str, str]:
    """Parse `project-scaffolding`'s `[components]` catalog -> ``{key: src}``.

    The mirror image of `parse_vendored_manifest`: an adopter's `[vendored]`
    table declares what it *copied*, the scaffold's `[components]` table
    declares what it *publishes* (project-scaffolding#230). Only the scaffold
    carries this table, and it is the sole machine-readable answer to "which
    paths are known components" — without it, a repo carrying an undeclared
    component is indistinguishable from one that never adopted it.

    `{}` when the table is absent or empty (an older scaffold checkout). The
    caller must surface that as *unknown* rather than "no carriers found";
    a malformed entry is dropped for the same read-only-detector reason
    `parse_vendored_manifest` drops one.
    """
    data = tomllib.loads(toml_text)
    table = data.get("components")
    if not isinstance(table, dict):
        return {}
    out: Dict[str, str] = {}
    for name, entry in table.items():
        if isinstance(entry, dict) and isinstance(entry.get("src"), str) and entry["src"]:
            out[name] = entry["src"]
    return out


def scaffold_catalog(scaffold_root: Path) -> tuple[Dict[str, str], Optional[str]]:
    """`parse_scaffold_catalog` over the scaffold's working-tree `.fleet.toml`.

    Returns `(catalog, error)`. `error` is a human-readable reason the catalog
    could not be established — a missing file, invalid TOML, or no `[components]`
    table — and is `None` only on a real, non-empty catalog. Two return values
    rather than one, because an empty dict has to mean *unknown* here, never
    *nothing to find*.
    """
    path = scaffold_root / ".fleet.toml"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return {}, f"cannot read the scaffold catalog at {path}: {exc}"
    try:
        catalog = parse_scaffold_catalog(text)
    except tomllib.TOMLDecodeError as exc:
        return {}, f"invalid TOML in {path}: {exc}"
    if not catalog:
        return {}, (
            f"no [components] table in {path} — undeclared-carrier detection cannot run "
            "(update the project-scaffolding checkout; project-scaffolding#230)"
        )
    return catalog, None


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


def hash_component_local(repo_dir: Path, relpath: str) -> Dict[str, str]:
    """A repo's own copy of a component, hashed from its **committed** blobs.

    Both sides of every comparison here must read the same population of bytes.
    The scaffold side reads committed blobs (`git show`); reading the adopter
    side off the filesystem does not match that on this fleet, for two reasons
    that both produce false drift:

      * **Line endings.** These checkouts store LF and check out CRLF, so a
        working-tree read of any text file differs from its own blob in every
        line. Byte-exactness is the right contract for a hash-verify, which is
        precisely why the two sides cannot be read through different filters:
        every text component compared as drifted no matter how byte-perfect
        the vendored copy was, and the one signal that matters — "identical,
        therefore safe to adopt" — could never fire.
      * **Untracked artefacts.** `__pycache__/`, `.pyc` and friends sit inside
        a vendored package directory and exist on no git side at all.

    Falls back to the plain filesystem walk when git cannot answer — a non-git
    directory (the hermetic fixtures), or a path present in the tree but not
    committed. Falling back can only *over*-report drift or a carrier, never
    hide one, which is the right direction for a detector built on the premise
    that silence must not be mistaken for coverage.
    """
    committed = hash_dir_at_ref(repo_dir, "HEAD", relpath)
    return committed or hash_dir_local(repo_dir / relpath)


def _git_show_bytes(repo: Path, ref: str, relpath: str) -> Optional[bytes]:
    """Raw bytes of `relpath` as committed at `ref` — via `git_run.run_git_bytes`,
    the bytes-mode wrapper, so a hash comparison stays byte-exact *without*
    hand-rolling a spawn that opts out of `git_env()` (fleet-config#677).
    `None` on any git failure (bad ref, path absent at that commit, ...)."""
    proc = git_run.run_git_bytes(["-C", str(repo), "show", f"{ref}:{relpath}"])
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

    Alongside the two per-adopter drift signals it answers a third question the
    manifest alone cannot (project-scaffolding#230): **who carries a component
    without declaring it?** `/propagate-vendored` derives its adopter list from
    `[vendored]` entries, so a repo holding the files but no entry is invisible
    — the wave re-vendors whoever declared the component, reports success, and
    silently leaves the rest on stale bytes. See `scan_undeclared_carriers`.
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
    manifests: Dict[str, Dict[str, Dict[str, str]]] = {}
    unreadable: List[str] = []

    for repo_name, repo_dir in sorted(repos.items()):
        if repo_dir.resolve() == scaffold_resolved:
            continue
        toml_path = repo_dir / ".fleet.toml"
        if not toml_path.is_file():
            no_manifest.append(repo_name)
            manifests[repo_name] = {}
            continue
        try:
            manifest = parse_vendored_manifest(toml_path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            errors.append({"repo": repo_name, "error": f"unreadable/invalid .fleet.toml: {exc}"})
            unreadable.append(repo_name)
            continue
        manifests[repo_name] = manifest
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
            local = hash_component_local(repo_dir, dest)
            result = classify_adopter(local, pinned, head_cache[src])
            adopters.append({
                "repo": repo_name, "component": component,
                "src": src, "dest": dest,
                "pinned_sha": sha, "head_sha": head_sha,
                **result,
            })

    catalog, catalog_error = scaffold_catalog(scaffold_root)
    if catalog_error:
        errors.append({"repo": "project-scaffolding", "error": catalog_error})
    carriers = scan_undeclared_carriers(
        scaffold_root, repos, manifests, catalog, head_ref, head_cache,
        component_filter=component_filter, scaffold_resolved=scaffold_resolved,
    )

    return {
        "scaffold": str(scaffold_root),
        "head_ref": head_ref,
        "head_sha": head_sha,
        "repos_scanned": len(repos),
        "adopters": adopters,
        "no_manifest": sorted(no_manifest),
        "errors": errors,
        "catalog": {
            "components": sorted(catalog),
            "count": len(catalog),
            "known": catalog_error is None,
            "error": catalog_error,
        },
        "undeclared_carriers": carriers,
        # The coverage line every caller must state out loud. `carriers_unknown`
        # is deliberately its own number rather than folded into either bucket:
        # a repo whose `.fleet.toml` could not be parsed is not "declared" and
        # not "a carrier" — it is unestablished, and reporting it as clean is
        # the exact failure this whole feature exists to stop.
        "coverage": {
            "declared_adopters": len({str(a["repo"]) for a in adopters}),
            "undeclared_carriers": len({str(c["repo"]) for c in carriers}),
            "carriers_unknown": sorted(unreadable),
            "catalog_known": catalog_error is None,
        },
    }


def scan_undeclared_carriers(
    scaffold_root: Path,
    repos: Dict[str, Path],
    manifests: Dict[str, Dict[str, Dict[str, str]]],
    catalog: Dict[str, str],
    head_ref: str,
    head_cache: Dict[str, Dict[str, str]],
    component_filter: Optional[str] = None,
    scaffold_resolved: Optional[Path] = None,
) -> List[Dict[str, object]]:
    """Repos holding a catalogued component's files without declaring it.

    For every component in the scaffold's `[components]` catalog, look at the
    conventional destination — the same relative path the scaffold keeps it at,
    which is what `/propagate-vendored` defaults `dest` to — in every fleet repo
    that has no `[vendored]` entry for it. A path that exists there is a
    **carrier**: the repo has the component, the manifest doesn't say so, and
    every propagation wave has been skipping it silently.

    Each finding carries `matches_head`, and that distinction is the point:

    * `True`  — byte-identical to the scaffold's current tip. Nothing to decide;
                this is a repo that simply never recorded what it copied, and
                `/propagate-vendored`'s ADOPT step can add the entry.
    * `False` — present but different. "Never declared it" and "deliberately
                forked it" are indistinguishable from the bytes alone, and only
                a human knows which, so this reports and never rewrites
                (project-scaffolding#230's explicit constraint).

    An adopter that legitimately keeps the component somewhere other than the
    scaffold's path is found by its own `dest` instead, and is already covered
    by the `adopters` list — so a miss here is a false *negative*, never a false
    positive that would push a re-vendor at a repo that never adopted anything.

    Returns `[]` when the catalog is empty (an older scaffold checkout with no
    `[components]` table). The caller reports that as `catalog_known: false` —
    "we could not look", not "there is nothing to find".
    """
    scaffold_at = scaffold_resolved or scaffold_root.resolve()
    carriers: List[Dict[str, object]] = []
    for repo_name, repo_dir in sorted(repos.items()):
        if repo_dir.resolve() == scaffold_at:
            continue
        declared = manifests.get(repo_name)
        if declared is None:  # unreadable .fleet.toml — reported as unknown, not clean
            continue
        for component, src in sorted(catalog.items()):
            if component_filter and component != component_filter:
                continue
            if component in declared:
                continue
            local = hash_component_local(repo_dir, src)
            if not local:
                continue
            if src not in head_cache:
                head_cache[src] = hash_dir_at_ref(scaffold_root, head_ref, src)
            diff = diff_hashes(local, head_cache[src])
            carriers.append({
                "repo": repo_name,
                "component": component,
                "path": src,
                "matches_head": not diff,
                "diff_files": diff,
            })
    return carriers


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
