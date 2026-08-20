"""Deploy-coverage declaration parsing for /issue-finish (fleet-config#459).

`project-scaffolding#199`/`#200` established the convention: a change is not
shipped merely because it merged. A repo whose standard restart/deploy does
not reach every runtime component it owns (an out-of-tree target, or an
in-tree process deliberately excluded from the restart — e.g. app-launcher's
`:8446` session-host, `app-launcher#611`/`#615`) declares each such component
in its own `CLAUDE.md`, `## <component name>` heading, four bullets:

    ## <component name>
    - what/why: <what this is; why it's excluded, or where it lives>
    - update command: `<the one supported command>`
    - liveness signal: `<field or probe>` — e.g. `GET /api/version`'s `<x>.stale`
    - NOT restarted/deployed by: `<the standard restart/finish flow>`

That template has no structured `paths:` list (unlike the sibling `## UX
surface` convention `ux_surface.py` reads) — "touched by this diff" has to be
inferred from backtick-quoted path-looking tokens in the `what/why` and
`NOT restarted/deployed by` prose. That is a real limitation, not an
oversight: a future reword of either bullet can silently change what this
detects. When a declared component yields zero parseable path tokens — **or
when the diff itself could not be taken at all** (`git_run.changed_files()`
returning `None`; fleet-config#681) — this module reports `TOUCHED=unknown`
rather than `no`: a flow that cannot tell whether it was touched must not
silently assume it wasn't (the exact failure shape `project-scaffolding#199`
exists to close).

Subcommand:

  check <repo-root> [--base <ref>]
      Prints `DECLARED=yes|no`. If `yes`, one stanza per declared component:
      `COMPONENT=`, `TOUCHED=yes|no|unknown`, `LIVENESS=`, `UPDATE_CMD=`.
      `DECLARED=no` is a single line, no git invoked — the common case (a repo
      with no declared components) costs nothing beyond one file read.

Headings inside a fenced code block are ignored, so a repo's CLAUDE.md merely
*documenting* the template (as project-scaffolding's own CLAUDE.md does) is
never mistaken for a live declaration.

stdlib + the `git` CLI only (matches the _lib module contract).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import git_run  # noqa: E402
from utf8_stdio import ensure_utf8_stdio  # noqa: E402

ensure_utf8_stdio()


# ---- pure helpers (unit-tested without git) -------------------------------

_BULLET_RE = re.compile(r"^-\s*([A-Za-z /]+?):\s*(.*)$")
_PATH_TOKEN_RE = re.compile(r"`([^`]+)`")


_KNOWN_FILE_EXTENSIONS = {
    "py", "js", "ts", "jsx", "tsx", "css", "html", "htm", "json", "yaml", "yml",
    "toml", "ini", "cfg", "md", "mmd", "sh", "bat", "ps1", "txt", "cs", "go",
    "rs", "java", "rb", "php", "sql", "env", "config",
}


def _looks_like_path(token: str) -> bool:
    """Crude filter for a backtick span that reads as a file/dir path rather
    than a command, flag, or a dotted field/JSON-key name — a `/` anywhere, or
    a trailing extension from a known-code/config whitelist (a generic
    "any short dotted suffix" test would also match `session_host.stale`, a
    field name, so it isn't used). No spaces, except a leading HTTP-verb
    prefix (`GET /api/version`) is unwrapped first."""
    token = token.strip()
    if not token or " " in token:
        parts = token.split(" ", 1)
        if len(parts) == 2 and parts[0].isupper() and parts[1].startswith("/"):
            token = parts[1]
        else:
            return False
    if "/" in token:
        return True
    ext = token.rsplit(".", 1)[-1].lower() if "." in token else ""
    return ext in _KNOWN_FILE_EXTENSIONS


def parse_components(text: str) -> List[Dict[str, object]]:
    """Parse every declared not-fully-covered component block out of a
    CLAUDE.md body.

    A component is any `## <name>` section whose bullets include a
    `liveness signal:` line — the one bullet every declared component must
    carry per the scaffold's template. Returns a list of
    `{"name", "liveness_signal", "update_command", "not_restarted_by", "paths"}`.
    """
    lines = text.splitlines()
    fenced = [False] * len(lines)  # True for any line inside (or delimiting) a fenced block
    in_fence = False
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            fenced[i] = True
            in_fence = not in_fence
            continue
        fenced[i] = in_fence

    sections: List[tuple] = []  # (heading, start_line, end_line)
    heading: Optional[str] = None
    start = 0
    for i, line in enumerate(lines):
        if fenced[i]:
            continue
        if line.startswith("## "):
            if heading is not None:
                sections.append((heading, start, i))
            heading = line[3:].strip()
            start = i + 1
    if heading is not None:
        sections.append((heading, start, len(lines)))

    components: List[Dict[str, object]] = []
    for name, s, e in sections:
        bullets: Dict[str, str] = {}
        for idx in range(s, e):
            if fenced[idx]:
                continue
            stripped = lines[idx].strip()
            if not stripped.startswith("-"):
                continue
            m = _BULLET_RE.match(stripped)
            if not m:
                continue
            bullets[m.group(1).strip().lower()] = m.group(2).strip()
        if "liveness signal" not in bullets:
            continue
        what_why = bullets.get("what/why", "")
        not_restarted = bullets.get("not restarted/deployed by", "")
        candidates = _PATH_TOKEN_RE.findall(what_why) + _PATH_TOKEN_RE.findall(not_restarted)
        paths = [t for t in candidates if _looks_like_path(t)]
        components.append({
            "name": name,
            "liveness_signal": bullets.get("liveness signal", ""),
            "update_command": bullets.get("update command", ""),
            "not_restarted_by": not_restarted,
            "paths": paths,
        })
    return components


def component_touch_status(comp: Dict[str, object], changed_files: List[str]) -> str:
    """`yes`/`no`/`unknown` for whether `changed_files` touched `comp`.

    `unknown` when the component's declaration yielded zero parseable path
    tokens — the flow must not silently read that as "not touched"."""
    paths = comp.get("paths") or []
    if not paths:
        return "unknown"
    return "yes" if touched_by(changed_files, paths) else "no"  # type: ignore[arg-type]


def touched_by(changed_files: List[str], path_tokens: List[str]) -> List[str]:
    """The subset of `changed_files` that fall under any of `path_tokens`.

    Tokens are plain paths, not globs (the declaration prose has no glob
    syntax): a token ending in `/` matches by prefix (a directory), anything
    else must match a full path segment exactly (the file itself, or that
    filename at the end of a longer path)."""
    norm_changed = [f.replace("\\", "/") for f in changed_files]
    hits: List[str] = []
    for f in norm_changed:
        for tok in path_tokens:
            tok_n = tok.replace("\\", "/").lstrip("/")
            if tok_n.endswith("/"):
                if f.startswith(tok_n) or f == tok_n.rstrip("/"):
                    hits.append(f)
                    break
            elif f == tok_n or f.endswith("/" + tok_n):
                hits.append(f)
                break
    return hits


# ---- git-backed CLI -------------------------------------------------------

def _default_base(repo: Path) -> str:
    return git_run.resolve_default_branch_ref(repo)


def cmd_check(repo: Path, base: Optional[str]) -> int:
    claude_md = repo / "CLAUDE.md"
    text = claude_md.read_text(encoding="utf-8", errors="replace") if claude_md.is_file() else ""
    components = parse_components(text)
    if not components:
        print("DECLARED=no")
        return 0

    print("DECLARED=yes")
    changed = git_run.changed_files(repo, base or _default_base(repo))
    for comp in components:
        # A diff that could not be taken is `unknown`, exactly as a component
        # with no parseable path token is — never `no` (fleet-config#681).
        touched = "unknown" if changed is None else component_touch_status(comp, changed)
        print(f"COMPONENT={comp['name']}")
        print(f"TOUCHED={touched}")
        print(f"LIVENESS={comp['liveness_signal']}")
        print(f"UPDATE_CMD={comp['update_command']}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Deploy-coverage declaration check for /issue-finish.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_check = sub.add_parser("check", help="does this repo declare not-fully-covered components, and did the diff touch one?")
    p_check.add_argument("repo", type=Path)
    p_check.add_argument("--base", default=None, help="ref to diff against (default: repo main)")

    args = ap.parse_args(argv)
    repo = args.repo.resolve()
    if not repo.is_dir():
        print(f"Not a directory: {repo}", file=sys.stderr)
        return 2
    return cmd_check(repo, args.base)


if __name__ == "__main__":
    raise SystemExit(main())
