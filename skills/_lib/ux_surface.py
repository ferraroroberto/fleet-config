"""UX-surface trigger logic for the issue-* skills (fleet-config#195).

Single source of truth for "did this change touch the web app's UX, and does
the design-conformance gate apply here?" — so `/issue-start`, `/issue-finish`,
and `/issue-yolo` all decide the same way instead of three skills re-deriving a
glob match in prose. Same deterministic-not-LLM principle the gate itself rests
on: the trigger is a glob intersection, not a per-run judgment call.

A repo declares its surface in a `## UX surface` block in its own `CLAUDE.md`
(convention: project-scaffolding#83):

    ## UX surface
    - design spec applies: yes      # `no` for Streamlit spikes / non-web repos
    - paths:
      - app/webapp/static/**/*.css
      - app/webapp/templates/**
      - app/webapp/static/**/*.{js,html}
    - key views:
      - /          (home + bottom nav)
      - /settings

Subcommands:

  applies <repo-root>
      Parse the block only (no git). Prints `SPEC_APPLIES=yes|no` and
      `KEY_VIEWS=...`. Used at `/issue-start`, where no diff exists yet — the
      agent still judges whether the *issue* is UX-likely; this just answers
      "does this repo even have a design-gated UX surface?".

  check <repo-root> [--base <ref>]
      Also diff `<base>...HEAD` and glob-match the changed files against the
      block's `paths`. Prints `SPEC_APPLIES`, `TOUCHED=yes|no`, `MATCHED=<csv>`,
      `KEY_VIEWS`. Used at `/issue-finish` and `/issue-yolo` to gate the design
      check on the actual diff. `--base` defaults to the repo's main branch.

A repo with no `## UX surface` block (or `design spec applies: no`) yields
`SPEC_APPLIES=no` / `TOUCHED=no`, so the gate is a permanent no-op there.

stdlib + the `git` CLI only (matches the _lib module contract).
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional

if hasattr(sys.stdout, "reconfigure"):  # UTF-8 even when stdout is captured (cp1252 fallback)
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[union-attr]


# ---- pure helpers (unit-tested without git) -------------------------------

def parse_ux_surface_block(text: str) -> Optional[Dict[str, object]]:
    """Parse the `## UX surface` block out of a CLAUDE.md body.

    Returns `{"spec_applies": bool, "paths": [...], "key_views": [...]}`, or
    `None` when the file has no such block. Structure is read by indentation:
    top-level `- key:` items at column 0, their list entries indented beneath.
    A trailing `# comment` on any line is ignored. Stops at the next `## `
    heading so a later section is never absorbed.

    Headings inside a fenced code block (```` ``` ```` / `~~~`) are ignored — the
    convention doc and READMEs show this block as a *fenced template*, and a
    repo that merely documents it must not read as if it declared a live block.
    """
    lines = text.splitlines()
    start = None
    in_fence = False
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            continue
        if not in_fence and line.strip() == "## UX surface":
            start = i + 1
            break
    if start is None:
        return None

    spec_applies = False
    paths: List[str] = []
    key_views: List[str] = []
    current: Optional[str] = None

    for line in lines[start:]:
        if line.startswith("## "):  # next section — block is done
            break
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        content = line.strip()
        if not content.startswith("-"):
            continue
        body = content[1:].strip()

        if indent == 0:  # a top-level key
            low = body.lower()
            if low.startswith("design spec applies:"):
                val = body.split(":", 1)[1].split("#")[0].strip().lower()
                spec_applies = val in ("yes", "true")
                current = None
            elif low.startswith("paths:"):
                current = "paths"
            elif low.startswith("key views:"):
                current = "key_views"
            else:
                current = None
        elif current:  # an indented list entry under the active key
            val = body.split("#")[0].strip()
            if not val:
                continue
            if current == "paths":
                paths.append(val)
            else:  # key_views — keep only the leading path token, drop the (desc)
                tok = val.split()[0]
                if tok:
                    key_views.append(tok)

    return {"spec_applies": spec_applies, "paths": paths, "key_views": key_views}


def expand_braces(pattern: str) -> List[str]:
    """Expand one or more `{a,b}` groups into the cross-product of literals.

    `*.{js,html}` -> [`*.js`, `*.html`]. Recurses so multiple/nested groups all
    expand. A pattern with no braces returns itself unchanged.
    """
    m = re.search(r"\{([^{}]*)\}", pattern)
    if not m:
        return [pattern]
    pre, post = pattern[: m.start()], pattern[m.end():]
    out: List[str] = []
    for opt in m.group(1).split(","):
        out.extend(expand_braces(pre + opt + post))
    return out


def _glob_to_regex(glob: str) -> str:
    """Translate a single brace-free glob to an anchored regex.

    `*` matches within a path segment (`[^/]*`); `**` crosses segments
    (`**/` -> zero-or-more dirs, trailing `**` -> anything); `?` is one
    non-slash char. Everything else is literal.
    """
    i, n = 0, len(glob)
    out: List[str] = []
    while i < n:
        c = glob[i]
        if c == "*":
            if i + 1 < n and glob[i + 1] == "*":  # '**'
                i += 2
                if i < n and glob[i] == "/":      # '**/' -> optional dir prefix
                    out.append("(?:.*/)?")
                    i += 1
                else:                              # trailing/standalone '**'
                    out.append(".*")
            else:
                out.append("[^/]*")
                i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    return "^" + "".join(out) + "$"


def matches_any(path: str, patterns: List[str]) -> bool:
    """True if `path` matches any of the (brace-expandable) glob `patterns`."""
    norm = path.replace("\\", "/")
    for pat in patterns:
        for expanded in expand_braces(pat):
            if re.match(_glob_to_regex(expanded), norm):
                return True
    return False


def touched_paths(changed_files: List[str], patterns: List[str]) -> List[str]:
    """The subset of `changed_files` that fall on the declared UX surface."""
    return [f for f in changed_files if matches_any(f, patterns)]


# ---- git-backed CLI -------------------------------------------------------

def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


def _default_base(repo: Path) -> str:
    """The repo's main branch to diff against (prefer the remote's default)."""
    res = _git(repo, "symbolic-ref", "refs/remotes/origin/HEAD")
    ref = res.stdout.strip()
    if res.returncode == 0 and ref:
        return ref.replace("refs/remotes/", "", 1)  # e.g. origin/main
    for cand in ("origin/main", "main", "master"):
        if _git(repo, "rev-parse", "--verify", "--quiet", cand).returncode == 0:
            return cand
    return "main"


def _changed_files(repo: Path, base: str) -> List[str]:
    """Files changed on HEAD since its merge-base with `base` (three-dot)."""
    res = _git(repo, "diff", "--name-only", f"{base}...HEAD")
    if res.returncode != 0:
        return []
    return [ln.strip() for ln in res.stdout.splitlines() if ln.strip()]


def _load_block(repo: Path) -> Optional[Dict[str, object]]:
    claude_md = repo / "CLAUDE.md"
    if not claude_md.is_file():
        return None
    return parse_ux_surface_block(claude_md.read_text(encoding="utf-8", errors="replace"))


def cmd_applies(repo: Path) -> int:
    block = _load_block(repo)
    applies = bool(block and block["spec_applies"])
    views = ",".join(block["key_views"]) if block else ""  # type: ignore[index]
    print(f"SPEC_APPLIES={'yes' if applies else 'no'}")
    print(f"KEY_VIEWS={views}")
    return 0


def cmd_check(repo: Path, base: Optional[str]) -> int:
    block = _load_block(repo)
    applies = bool(block and block["spec_applies"])
    print(f"SPEC_APPLIES={'yes' if applies else 'no'}")
    if not applies:
        print("TOUCHED=no")
        print("MATCHED=")
        print("KEY_VIEWS=")
        return 0
    base = base or _default_base(repo)
    matched = touched_paths(_changed_files(repo, base), block["paths"])  # type: ignore[index]
    print(f"TOUCHED={'yes' if matched else 'no'}")
    print(f"MATCHED={','.join(matched)}")
    print(f"KEY_VIEWS={','.join(block['key_views'])}")  # type: ignore[index]
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="UX-surface trigger for the issue-* skills.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_applies = sub.add_parser("applies", help="does this repo have a design-gated UX surface?")
    p_applies.add_argument("repo", type=Path)

    p_check = sub.add_parser("check", help="did the diff touch the UX surface?")
    p_check.add_argument("repo", type=Path)
    p_check.add_argument("--base", default=None, help="ref to diff against (default: repo main)")

    args = ap.parse_args(argv)
    repo = args.repo.resolve()
    if not repo.is_dir():
        print(f"Not a directory: {repo}", file=sys.stderr)
        return 2
    if args.cmd == "applies":
        return cmd_applies(repo)
    return cmd_check(repo, args.base)


if __name__ == "__main__":
    raise SystemExit(main())
