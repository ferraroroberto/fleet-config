"""Deterministic discovery + diff-intersection for the `/docs-shots` skill (fleet-config#93).

`/docs-shots` is the judgment/orchestration half of a visual-docs feature
whose deterministic capture engine (Playwright, fail-safe masking, the
manifest, the README generator) lives per-app — pinned against the shipped
reference implementation, `content-management`'s `config/doc_capture/`
(content-management#110). This module owns only the mechanical parts the
skill needs before it can ask a human anything:

  - **Discovery** — a repo opts in by having `docs/screenshots/manifest.json`;
    absent means "no visual docs here", a silent no-op.
  - **Diff intersection** — which manifest features does a changed-file list
    touch (reusing `ux_surface.py`'s glob machinery — the same brace-expand +
    glob-to-regex logic that gates the design-conformance check), and which
    changed files sit under a directory the manifest already covers but match
    no feature at all (a candidate "new/unmapped surface" needing a manifest
    entry, never guessed at by the skill).
  - **README-marker precondition** — the engine's own `readme`/`all` commands
    hard-fail if `<!-- docs-shots:start -->` / `<!-- docs-shots:end -->` are
    missing from the target README; checking this first lets the skill give a
    clean one-line message instead of surfacing the engine's raw traceback.

Everything past this — presenting the stale set, waiting for the human's OK,
invoking the engine, and reading its output back — is the skill's own LLM
judgment layer; this module never runs the engine or writes any file.

Subcommands:

  discover <repo-root>
      Prints `MANIFEST=<path>|absent` and, when present, `FEATURES=<csv>`.
      Used at both entry points to decide the silent-no-op gate.

  check <repo-root> [--base <ref>]
      Also diffs `<base>...HEAD` (default: the repo's main branch) and
      intersects it against the manifest. Prints `MANIFEST`, `STALE=
      <feature:file|file,...;...>`, `UNMAPPED=<csv>`, `README_MARKERS=yes|no`.
      Used by the `/issue-finish` Step 2 sub-step.

stdlib + the `git` CLI only (matches the `_lib` module contract); imports
`ux_surface` for glob matching and `git_run` for the diff, no `gh` dependency.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import git_run  # noqa: E402
from ux_surface import matches_any  # noqa: E402
from utf8_stdio import ensure_utf8_stdio  # noqa: E402

ensure_utf8_stdio()

MANIFEST_REL_PATH = "docs/screenshots/manifest.json"
README_MARKER_START = "<!-- docs-shots:start -->"
README_MARKER_END = "<!-- docs-shots:end -->"


# ---- pure helpers (unit-tested without git) --------------------------------

def find_manifest_path(repo_root: Path) -> Optional[Path]:
    """The manifest path if this repo has opted in, else None."""
    candidate = repo_root / MANIFEST_REL_PATH
    return candidate if candidate.is_file() else None


def load_manifest(manifest_path: Path) -> Dict[str, Any]:
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def feature_globs(manifest: Dict[str, Any]) -> Dict[str, List[str]]:
    """`{feature_name: source_globs}` for every manifest feature."""
    return {
        name: list(entry.get("source_globs", []))
        for name, entry in manifest.get("features", {}).items()
    }


def stale_features(changed_files: List[str], manifest: Dict[str, Any]) -> Dict[str, List[str]]:
    """`{feature_name: [matched changed files]}` for every touched feature.

    A feature with no matched files is omitted entirely — this is the "stale
    set", not the full feature list.
    """
    result: Dict[str, List[str]] = {}
    for name, globs in feature_globs(manifest).items():
        if not globs:
            continue
        matched = [f for f in changed_files if matches_any(f, globs)]
        if matched:
            result[name] = matched
    return result


def _covered_top_dirs(manifest: Dict[str, Any]) -> set:
    """Top-level directory of every glob across every feature's source_globs."""
    dirs = set()
    for globs in feature_globs(manifest).values():
        for g in globs:
            norm = g.replace("\\", "/")
            if "/" in norm:
                dirs.add(norm.split("/", 1)[0])
    return dirs


def unmapped_changed_files(changed_files: List[str], manifest: Dict[str, Any]) -> List[str]:
    """Changed files under a manifest-covered top-level dir that match no feature.

    Conservative by design: a change to `CLAUDE.md`, `tests/`, or any
    directory the manifest has no feature in at all is not flagged — only a
    file sitting alongside genuinely-tracked app source, that no feature's
    `source_globs` happens to cover, is a real "someone added a surface and
    forgot the manifest" candidate.
    """
    covered_dirs = _covered_top_dirs(manifest)
    if not covered_dirs:
        return []
    stale = stale_features(changed_files, manifest)
    matched_files = {f for files in stale.values() for f in files}
    out = []
    for f in changed_files:
        norm = f.replace("\\", "/")
        top = norm.split("/", 1)[0] if "/" in norm else ""
        if top in covered_dirs and f not in matched_files:
            out.append(f)
    return out


def readme_has_markers(readme_text: str) -> bool:
    """True only if both docs-shots markers are present (engine precondition)."""
    return README_MARKER_START in readme_text and README_MARKER_END in readme_text


# ---- git-backed CLI --------------------------------------------------------

def _default_base(repo: Path) -> str:
    """The repo's main branch to diff against (prefer the remote's default)."""
    return git_run.resolve_default_branch_ref(repo)


def _changed_files(repo: Path, base: str) -> List[str]:
    res = git_run.run_git(["-C", str(repo), "diff", "--name-only", f"{base}...HEAD"])
    if res.returncode != 0:
        return []
    return [ln.strip() for ln in res.stdout.splitlines() if ln.strip()]


def cmd_discover(repo: Path) -> int:
    manifest_path = find_manifest_path(repo)
    if manifest_path is None:
        print("MANIFEST=absent")
        print("FEATURES=")
        return 0
    manifest = load_manifest(manifest_path)
    print(f"MANIFEST={manifest_path}")
    print(f"FEATURES={','.join(manifest.get('features', {}).keys())}")
    return 0


def _format_stale(stale: Dict[str, List[str]]) -> str:
    return ";".join(f"{name}:{'|'.join(files)}" for name, files in stale.items())


def cmd_check(repo: Path, base: Optional[str]) -> int:
    manifest_path = find_manifest_path(repo)
    if manifest_path is None:
        print("MANIFEST=absent")
        print("STALE=")
        print("UNMAPPED=")
        print("README_MARKERS=no")
        return 0
    manifest = load_manifest(manifest_path)
    print(f"MANIFEST={manifest_path}")
    base = base or _default_base(repo)
    changed = _changed_files(repo, base)
    print(f"STALE={_format_stale(stale_features(changed, manifest))}")
    print(f"UNMAPPED={','.join(unmapped_changed_files(changed, manifest))}")
    readme_path = repo / "README.md"
    has_markers = readme_path.is_file() and readme_has_markers(
        readme_path.read_text(encoding="utf-8", errors="replace")
    )
    print(f"README_MARKERS={'yes' if has_markers else 'no'}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Discovery + diff-intersection for /docs-shots.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_discover = sub.add_parser("discover", help="does this repo have a docs-shots manifest?")
    p_discover.add_argument("repo", type=Path)

    p_check = sub.add_parser("check", help="which features did the diff touch?")
    p_check.add_argument("repo", type=Path)
    p_check.add_argument("--base", default=None, help="ref to diff against (default: repo main)")

    args = ap.parse_args(argv)
    repo = args.repo.resolve()
    if not repo.is_dir():
        print(f"Not a directory: {repo}", file=sys.stderr)
        return 2
    if args.cmd == "discover":
        return cmd_discover(repo)
    return cmd_check(repo, args.base)


if __name__ == "__main__":
    raise SystemExit(main())
