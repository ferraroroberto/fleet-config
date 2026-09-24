"""Stage 1 of /design-review — the capture plan (fleet-config#971).

Resolves *what* the walk visits and *where* it runs, from data no session
has to re-derive:

  * the **target**: a fleet repo named as in `hooks/projects.toml` or given
    as a path; its `webapp_port` + `browser_scheme` become the loopback base
    URL (`https://127.0.0.1:8445`), per the first-party screenshot exception
    in `docs/recurring-gotchas.md` — never a third-party site;
  * the optional **`[design.review]`** block of the target's `.fleet.toml`
    (extra safe steps, no-go selectors, a theme storage key);
  * the fixed **device matrix** — the three projections the 2026-09-21 audit
    used (`iphone` WebKit / `android` Chromium / `desktop` Chromium), each
    crossed with `light` and `dark`.

Read-only by construction: the walk only clicks primary tabs, sets
`details.open`, and calls `dialog.showModal()` / `close()`. Anything else
must be declared in `[design.review].extra_steps` — and even those are
limited to opening one `details` and clicking selectors; there is no `fill`,
no `submit`, and every click is refused when its element sits inside a
`no_go` selector (checked with `closest()` at click time, #995).

`[design.review]` keys (all optional):

    theme_storage_key = "app-launcher.theme"   # localStorage key the app's theme boot reads
    tab_selector      = "[role=tab]"             # override primary-tab discovery -- resolved
                                                 # *inside* the first [role=tablist], so never
                                                 # prefix it with "[role=tablist]" (#995)
    no_go             = ["#dangerZone"]          # never clicked, nor anything inside it
    [[design.review.extra_steps]]
    tab      = "board"            # which tab screen the step extends
    id       = "row-kebab"        # suffix on the screen id
    click    = ".row .kebab"      # one selector to click, then measure
    [[design.review.extra_steps]]
    tab      = "claude"
    id       = "project-menu"
    open     = "details.projects" # optional: open this details first (not a click)
    clicks   = [".projects .row-kebab"]   # clicked in order, each vetoed by no_go
"""
from __future__ import annotations

import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

# `fleet_repo_scan` is a sibling top-level module in skills/_lib; reached the
# same way `design_lint/files.py` reaches `git_run`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import fleet_repo_scan  # noqa: E402

LOOPBACK = "127.0.0.1"

# Device profiles: name -> (engine, Playwright device descriptor name or None,
# viewport for the descriptor-less desktop). The names are part of every
# screen id (`iphone-light-board`), so they are stable API for #972–#974.
DEVICES: Dict[str, dict] = {
    "iphone": {"engine": "webkit", "descriptor": "iPhone 15 Pro Max"},
    "android": {"engine": "chromium", "descriptor": "Pixel 7"},
    "desktop": {"engine": "chromium", "viewport": {"width": 1440, "height": 900}},
}
DEFAULT_DEVICES = ("iphone", "desktop", "android")
THEMES = ("light", "dark")


@dataclass
class Target:
    """Where the walk points and what the target declared about itself."""

    name: str
    root: Optional[Path]
    base_url: str
    review: Dict[str, object] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, object]:
        return {
            "name": self.name,
            "root": str(self.root) if self.root else None,
            "base_url": self.base_url,
            "review": self.review,
        }


class PlanError(Exception):
    """A target that cannot be resolved — reported, never guessed around."""


def load_review_block(root: Optional[Path]) -> Dict[str, object]:
    """The target's `[design.review]` table from its `.fleet.toml`, or `{}`.

    A missing file or block is the normal case (generic discovery covers a
    conforming fleet app with zero declarations). An unparseable file is
    reported as an error key rather than silently treated as empty: a typo in
    a `no_go` list must not turn into a click.
    """
    if root is None:
        return {}
    toml_path = Path(root) / ".fleet.toml"
    if not toml_path.is_file():
        return {}
    try:
        data = tomllib.loads(toml_path.read_text(encoding="utf-8", errors="replace"))
    except tomllib.TOMLDecodeError as exc:
        return {"error": f"unparseable .fleet.toml: {exc}"}
    design = data.get("design")
    if not isinstance(design, dict):
        return {}
    review = design.get("review")
    return dict(review) if isinstance(review, dict) else {}


def resolve_target(
    repo: str,
    projects_toml: Optional[Path] = None,
    url_override: Optional[str] = None,
) -> Target:
    """Turn a repo name or path into a `Target` with a loopback base URL.

    `repo` is matched first as a `hooks/projects.toml` table name, then as a
    path whose directory name is one. `url_override` (tests, a static fixture
    page) skips the port lookup entirely but keeps the name/root resolution.
    Raises `PlanError` when nothing declares a `webapp_port` for the repo and
    no override was given — the walk must never fall back to a guessed port.
    """
    tables = fleet_repo_scan.fleet_repo_tables(projects_toml)
    name: Optional[str] = None
    root: Optional[Path] = None
    as_path = Path(repo)
    if repo in tables:
        name = repo
        root = Path(str(tables[repo].get("cwd_prefix", "")))
        if not root.is_dir():
            root = None
    elif as_path.is_dir():
        root = as_path.resolve()
        name = root.name
        # A `<repo>-wt-<N>` worktree measures the repo it belongs to.
        stem = name.rsplit("-wt-", 1)[0]
        if stem in tables:
            name = stem
    else:
        raise PlanError(f"unknown target {repo!r}: not a hooks/projects.toml repo nor a directory")

    review = load_review_block(root)
    if url_override:
        return Target(name=name, root=root, base_url=url_override.rstrip("/"), review=review)

    table = tables.get(name, {})
    port = table.get("webapp_port")
    scheme = table.get("browser_scheme")
    if not port or not scheme:
        raise PlanError(
            f"{name} declares no webapp_port/browser_scheme in hooks/projects.toml — "
            "pass --url for a non-fleet target"
        )
    return Target(name=name, root=root, base_url=f"{scheme}://{LOOPBACK}:{int(port)}", review=review)


def screen_id(device: str, theme: str, view: str) -> str:
    """The stable id every consumer keys on: `iphone-light-board`,
    `desktop-dark-dialog-settings`. Lower-case, hyphen-joined, no spaces."""
    parts = [device, theme] + [p for p in view.split("-") if p]
    return "-".join(_slug(p) for p in parts)


def _slug(text: str) -> str:
    out = "".join(ch if ch.isalnum() else "-" for ch in text.lower()).strip("-")
    while "--" in out:
        out = out.replace("--", "-")
    return out or "x"


def device_list(names: Optional[List[str]]) -> List[str]:
    """Validate a requested device subset against `DEVICES`; default = all."""
    if not names:
        return list(DEFAULT_DEVICES)
    unknown = [n for n in names if n not in DEVICES]
    if unknown:
        raise PlanError(f"unknown device(s) {unknown}; known: {sorted(DEVICES)}")
    return list(names)
