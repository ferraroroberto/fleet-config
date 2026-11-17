"""UX-surface trigger logic for the issue-* skills.

Single source of truth for "did this change touch the web app's UX, and does
the design-conformance gate apply here?" — so `/issue-start`, `/issue-finish`,
and `/issue-yolo` all decide the same way instead of three skills re-deriving a
glob match in prose. Same deterministic-not-LLM principle the gate itself rests
on: the trigger is a glob intersection, not a per-run judgment call.

A repo declares its surface in a `## UX surface` block in its own `CLAUDE.md`
(project-scaffolding's UX-surface convention):

    ## UX surface
    - design spec applies: yes      # `web-page` for a public static site; `no` for spikes / non-web repos
    - paths:
      - app/webapp/static/**/*.css
      - app/webapp/templates/**
      - app/webapp/static/**/*.{js,html}
    - key views:
      - /          (home + bottom nav)
      - /settings

Subcommands:

  applies <repo-root>
      Parse the block only (no git). Prints `SPEC_APPLIES=yes|no`,
      `SPEC_KIND=web-app|web-page`, `KEY_VIEWS=...` and `SURFACE_MAP_FILE=<path>|none`. Used at `/issue-start`, where no diff exists yet — the
      agent still judges whether the *issue* is UX-likely; this just answers
      "does this repo even have a design-gated UX surface?".

  check <repo-root> [--base <ref>]
      Also diff `<base>...HEAD` and glob-match the changed files against the
      block's `paths` plus `DEFAULT_UI_PATTERNS` (view JS, templates, Streamlit
      pages: what renders, not only the CSS a block names). Prints `SPEC_APPLIES`, `TOUCHED=yes|no|unknown`,
      `MATCHED=<csv>`, `KEY_VIEWS`. Used at `/issue-finish` and `/issue-yolo`
      to gate the design check on the actual diff. `--base` defaults to the
      repo's main branch. `TOUCHED=unknown` means the diff itself could not be
      taken (bad base ref, unreadable repo) — the gate treats it as touched,
      never as a silent `no`.

  surface-map <repo> [--base <ref>]
      Hold the repo's UX surface map (`docs/ux-surfaces.yaml`, the source; `docs/ux-surface-map.md` is generated) to its
      `data-surface` ids by running the repo's own `scripts/ux_surface_ratchet.py`
      when `.fleet.toml` enables `[ux_surface_ratchet]` and the diff touches the
      UX surface or the map. Prints `SURFACE_MAP=in-sync|refused|not-adopted|
      not-applicable|not-confirmed` (+ the ratchet's hits, quoted); exit 0 for the
      first, third and fourth, 1 refused, 3 not-confirmed. A repo that has not
      adopted the map is `not-adopted`, never `in-sync`.

A repo with no `## UX surface` block (or `design spec applies: no`) yields
`SPEC_APPLIES=no` / `TOUCHED=no`, so the gate is a permanent no-op there.

`SPEC_KIND` names which spec a design check reads: `web-page` (`design.web.md`,
a public static site) when the block answers `web-page` or `.fleet.toml`
declares `shape = "static-site"`, else `web-app` (`design.md` +
`design.dark.md`). It is printed whether or not the gate applies, because the
periodic `/design-sync` lint picks its spec from it without consulting the gate.

stdlib + the `git` CLI only (matches the _lib module contract).
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import git_run  # noqa: E402
from fleet_core import fleet_toml  # noqa: E402
from fleet_core.utf8_stdio import ensure_utf8_stdio  # noqa: E402
from no_window import NO_WINDOW  # noqa: E402

ensure_utf8_stdio()


# ---- pure helpers (unit-tested without git) -------------------------------

# The section heading may carry a descriptive suffix — `## UX surface — diff-keyed
# design-conformance gate` (as the canonical scaffold does) or `## UX surface: home
# views` — as long as it still starts with the exact `## UX surface` token. A word
# boundary after `surface` keeps `## UX surfaces` (and other words) from matching.
_UX_HEADING = re.compile(r"^##\s+UX surface(?:\b.*)?$")

_FENCE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")


def fenced_mask(lines: List[str]) -> List[bool]:
    """Per-line "is this inside a fenced code block?" mask.

    Shared by every CLAUDE.md-body parser in this repo that must skip headings
    or bullets that live inside a documented (fenced) example rather than a
    live declaration (`ux_surface`, `deploy_coverage`, `e2e_test_audit`).

    Tracks paired ``` / ~~~ fences (CommonMark: a closing fence uses the same
    character and is at least as long as the opener, so a ```` ```markdown ````
    block containing a shorter fence is not closed early). Both the delimiter
    lines and everything between them are masked True.
    """
    mask: List[bool] = []
    fence_char = ""
    fence_len = 0
    for line in lines:
        m = _FENCE_RE.match(line)
        if not fence_char:
            if m:
                fence_char, fence_len = m.group(1)[0], len(m.group(1))
                mask.append(True)
                continue
            mask.append(False)
        else:
            mask.append(True)
            if m and m.group(1)[0] == fence_char and len(m.group(1)) >= fence_len:
                # A closing fence carries no info text after the delimiter.
                if not line.strip()[fence_len:].strip():
                    fence_char, fence_len = "", 0
    return mask


WEB_APP = "web-app"
WEB_PAGE = "web-page"


def parse_ux_surface_block(text: str) -> Optional[Dict[str, object]]:
    """Parse the `## UX surface` block out of a CLAUDE.md body.

    The heading may carry a descriptive suffix (e.g. `## UX surface — diff-keyed
    design-conformance gate`), so the canonical scaffold can keep its rich heading
    on a *live* block; `## UX surfaces` and unrelated words do not match.

    Returns `{"spec_applies": bool, "kind": "web-app"|"web-page"|None,
    "paths": [...], "key_views": [...]}`, or
    `None` when the file has no such block. Structure is read by indentation:
    top-level `- key:` items at column 0, their list entries indented beneath.
    A trailing `# comment` on any line is ignored. Stops at the next `## `
    heading so a later section is never absorbed.

    Headings inside a fenced code block (```` ``` ```` / `~~~`) are ignored — the
    convention doc and READMEs show this block as a *fenced template*, and a
    repo that merely documents it must not read as if it declared a live block.
    """
    lines = text.splitlines()
    mask = fenced_mask(lines)
    start = None
    for i, line in enumerate(lines):
        if not mask[i] and _UX_HEADING.match(line.strip()):
            start = i + 1
            break
    if start is None:
        return None

    spec_applies = False
    kind: Optional[str] = None
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
                # The leading word is the answer; a note after it (`yes (the app, not the stage)`)
                # is the author's, not a `no`.
                val = body.split(":", 1)[1].split("#")[0].strip().lower()
                if re.match(r"web-page\b", val):
                    spec_applies, kind = True, WEB_PAGE
                else:
                    spec_applies = re.match(r"(yes|true)\b", val) is not None
                    kind = WEB_APP if spec_applies else None
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

    return {"spec_applies": spec_applies, "kind": kind, "paths": paths, "key_views": key_views}


def spec_kind(block: Optional[Dict[str, object]], shape: Optional[str]) -> str:
    """Which design spec a repo is held to: `web-page` or `web-app`.

    The block's own `web-page` answer wins; otherwise a `.fleet.toml`
    `shape = "static-site"` still marks a public static site, so a site whose
    gate is off is never linted against the PWA spec.
    """
    if block and block.get("kind") == WEB_PAGE:
        return WEB_PAGE
    return WEB_PAGE if shape == "static-site" else WEB_APP


def repo_spec_kind(repo: Path) -> str:
    """`spec_kind` for a checkout: its CLAUDE.md block plus its `.fleet.toml` shape."""
    shape = (fleet_toml.load(repo) or {}).get("shape")
    return spec_kind(_load_block(repo), shape if isinstance(shape, str) else None)


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


# A repo's declared `paths` often name only its CSS and tokens, yet a change to
# view JS, a template or a Streamlit page alters what renders just as much. These
# always count once the spec applies, on top of whatever the block declares.
DEFAULT_UI_PATTERNS = [
    "**/*.css",
    "**/static/**/*.{js,mjs,html}",
    "**/templates/**",
    "**/*.{html,jinja,jinja2,j2}",
    "**/pages/**/*.py",
    "**/streamlit_app.py",
]
# What a phone shows: the installed-PWA shell (its manifest and home-screen
# icons, which no CSS or view change touches) and native iOS/Android views.
# Native code counts only under an `ios/` or `android/` app tree, so a
# headless Swift or Kotlin worker elsewhere in a repo is not mistaken for UI.
MOBILE_UI_PATTERNS = [
    "**/*.webmanifest",
    "**/static/**/manifest.json",
    "**/static/**/{icon,apple-touch-icon}*.png",
    "**/*.{xib,storyboard}",
    "**/ios/**/*.swift",
    "**/android/**/res/**",
    "**/android/**/*.{kt,java}",
]
# Test fixtures and docs carry HTML and page modules that never ship to a user.
_DEFAULT_EXCLUDES = ["tests/**", "**/tests/**", "docs/**", "**/node_modules/**"]


def ui_paths(changed_files: List[str], declared: List[str]) -> List[str]:
    """`changed_files` on the UX surface: the declared globs plus the web and mobile defaults.

    A declared glob always counts; a default one skips test fixtures and docs.
    """
    defaults = DEFAULT_UI_PATTERNS + MOBILE_UI_PATTERNS
    return [
        f for f in changed_files
        if matches_any(f, declared)
        or (matches_any(f, defaults) and not matches_any(f, _DEFAULT_EXCLUDES))
    ]


# ---- git-backed CLI -------------------------------------------------------

def _default_base(repo: Path) -> str:
    """The repo's main branch to diff against (prefer the remote's default)."""
    return git_run.resolve_default_branch_ref(repo)


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
    print(f"SPEC_KIND={repo_spec_kind(repo)}")
    print(f"KEY_VIEWS={views}")
    print(f"SURFACE_MAP_FILE={surface_map_file(repo) or 'none'}")
    return 0


@dataclass(frozen=True)
class Trigger:
    """The UX-surface decision for one repo: `touched` is `yes`, `no` or `unknown`; `key_views` is empty unless the spec applies."""

    applies: bool
    touched: str
    matched: List[str]
    key_views: List[str]


def trigger(repo: Path, base: Optional[str] = None, changed: Optional[List[str]] = None,
            diff: bool = True) -> Trigger:
    """Does the design spec apply to `repo`, and did the change touch its UX surface?

    `changed` is the file list to match when the caller already has it (a PR's
    file list); otherwise `<base>...HEAD` is diffed, `base` defaulting to the
    repo's main branch. A diff that cannot be taken is `touched=unknown`, never
    `no`. `diff=False` answers `applies` alone and leaves a spec-applying repo
    at `unknown`, for a caller that has not yet picked what to diff.
    """
    block = _load_block(repo)
    if not (block and block["spec_applies"]):
        return Trigger(False, "no", [], [])
    views = list(block["key_views"])  # type: ignore[arg-type]
    if changed is None and diff:
        changed = git_run.changed_files(repo, base or _default_base(repo))
    if changed is None:
        return Trigger(True, "unknown", [], views)
    matched = ui_paths(changed, block["paths"])  # type: ignore[arg-type]
    return Trigger(True, "yes" if matched else "no", matched, views)


def cmd_check(repo: Path, base: Optional[str]) -> int:
    found = trigger(repo, base)
    print(f"SPEC_APPLIES={'yes' if found.applies else 'no'}")
    print(f"SPEC_KIND={repo_spec_kind(repo)}")
    print(f"TOUCHED={found.touched}")
    print(f"MATCHED={','.join(found.matched)}")
    print(f"KEY_VIEWS={','.join(found.key_views)}")
    return 0


# ---- UX surface map (project-scaffolding docs/ux-surface-map.md; the source is docs/ux-surfaces.yaml) ----

RATCHET_SCRIPT = "scripts/ux_surface_ratchet.py"
DEFAULT_MAP = "docs/ux-surfaces.yaml"
# The generated page; also the whole map of an app that has not moved to the YAML source yet.
DEFAULT_PAGE = "docs/ux-surface-map.md"
# The ratchet gets a minute: it reads the app's HTML/JS once and never starts the app.
RATCHET_TIMEOUT_S = 120


def _ratchet_table(repo: Path) -> "tuple[Optional[Dict[str, object]], Optional[str]]":
    """`(table, error)`: `[ux_surface_ratchet]` when it enables the stage, else `(None, None)`.

    A malformed `.fleet.toml` is an error string, never a quiet "not adopted".
    """
    data, error = fleet_toml.load_checked(repo)
    if error:
        return None, error
    table = (data or {}).get("ux_surface_ratchet")
    if isinstance(table, dict) and table.get("enabled") is True:
        return table, None
    return None, None


def surface_map_file(repo: Path) -> Optional[str]:
    """The repo's UX surface map path (repo-relative) when the file exists, else `None`.

    The table's `map` key, else the YAML source; an app not yet converted has only the Markdown page, which is
    returned so `/issue-start` still reads it."""
    table, _ = _ratchet_table(repo)
    name = table.get("map") if table else None
    if isinstance(name, str) and name.strip():
        rel = name.replace("\\", "/").strip("/")
        return rel if (repo / rel).is_file() else None
    return next((rel for rel in (DEFAULT_MAP, DEFAULT_PAGE) if (repo / rel).is_file()), None)


@dataclass(frozen=True)
class SurfaceMap:
    """One surface-map decision: `state` is one of the `SURFACE_MAP=` words, `lines` the ratchet's output."""

    state: str
    reason: str = ""
    lines: tuple = ()

    @property
    def exit_code(self) -> int:
        return {"refused": 1, "not-confirmed": 3}.get(self.state, 0)


def surface_map(repo: Path, base: Optional[str] = None, changed: Optional[List[str]] = None) -> SurfaceMap:
    """Run the repo's UX surface ratchet when the change can have moved the map.

    `not-adopted` when `[ux_surface_ratchet]` is not enabled (no `.fleet.toml`, no
    table, `enabled = false`); `not-applicable` when the design spec does not apply
    or the diff touched neither the UX surface nor the map; otherwise the ratchet
    runs and exit 0 is `in-sync`, 1 `refused`, and anything else (a missing script,
    bad config, exit 2 or 3, a timeout, an unreadable diff) `not-confirmed`.
    """
    table, error = _ratchet_table(repo)
    if error:
        return SurfaceMap("not-confirmed", f".fleet.toml unreadable: {error}")
    if table is None:
        return SurfaceMap("not-adopted", "no [ux_surface_ratchet] enabled in .fleet.toml")
    found = trigger(repo, base, changed)
    if not found.applies:
        return SurfaceMap("not-applicable", "the design spec does not apply to this repo")
    map_rel = surface_map_file(repo) or str(table.get("map") or DEFAULT_MAP)
    page_rel = str(table.get("page") or DEFAULT_PAGE).replace("\\", "/")
    if found.touched == "no":
        # trigger() skips docs/**, but a map or page edit can still drift from the code.
        files = changed if changed is not None else git_run.changed_files(repo, base or _default_base(repo))
        if files is not None and not {map_rel, page_rel} & {f.replace("\\", "/") for f in files}:
            return SurfaceMap("not-applicable", "the diff touches neither the UX surface nor the map")
    script = repo / RATCHET_SCRIPT
    if not script.is_file():
        return SurfaceMap("not-confirmed", f"{RATCHET_SCRIPT} is not vendored here; the stage is enabled but cannot run")
    try:
        done = subprocess.run(
            [sys.executable, "-P", str(script)], cwd=str(repo), capture_output=True, timeout=RATCHET_TIMEOUT_S,
            env={**os.environ, "PYTHONUTF8": "1"}, creationflags=NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return SurfaceMap("not-confirmed", f"the ratchet did not finish: {exc}")
    out = (done.stdout or b"").decode("utf-8", errors="replace") + (done.stderr or b"").decode("utf-8", errors="replace")
    lines = tuple(line.rstrip() for line in out.splitlines() if line.strip())
    if done.returncode == 0:
        return SurfaceMap("in-sync", "", lines)
    if done.returncode == 1:
        return SurfaceMap("refused", "the map and the data-surface ids disagree", lines)
    reason = f"the ratchet exited {done.returncode} (2 bad config, 3 could not read)"
    if map_rel.lower().endswith(".md"):
        reason += f"; the map is Markdown ({map_rel}): convert it to {DEFAULT_MAP}, the source the ratchet reads"
    return SurfaceMap("not-confirmed", reason, lines)


def cmd_surface_map(repo: Path, base: Optional[str]) -> int:
    found = surface_map(repo, base)
    print(f"SURFACE_MAP={found.state}")
    if found.reason:
        print(f"REASON={found.reason}")
    for line in found.lines:
        print(f"RATCHET: {line}")
    return found.exit_code


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="UX-surface trigger for the issue-* skills.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_applies = sub.add_parser("applies", help="does this repo have a design-gated UX surface?")
    p_applies.add_argument("repo", type=Path)

    p_check = sub.add_parser("check", help="did the diff touch the UX surface?")
    p_check.add_argument("repo", type=Path)
    p_check.add_argument("--base", default=None, help="ref to diff against (default: repo main)")

    p_map = sub.add_parser("surface-map", help="does the UX surface map still match the data-surface ids?")
    p_map.add_argument("repo", type=Path)
    p_map.add_argument("--base", default=None, help="ref to diff against (default: repo main)")

    args = ap.parse_args(argv)
    repo = args.repo.resolve()
    if not repo.is_dir():
        print(f"Not a directory: {repo}", file=sys.stderr)
        return 2
    if args.cmd == "applies":
        return cmd_applies(repo)
    if args.cmd == "surface-map":
        return cmd_surface_map(repo, args.base)
    return cmd_check(repo, args.base)


if __name__ == "__main__":
    raise SystemExit(main())
