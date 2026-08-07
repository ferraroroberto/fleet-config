"""Deterministic design-system lint for /design-sync v2 (fleet-config#277).

Pure logic + a small CLI, same `_lib` contract as `cert_drift.py` /
`ux_surface.py`: everything mechanically checkable about a web app's
conformance to the fleet design system lives HERE, not in LLM judgment. The
skill orchestrates; this package measures.

Subcommands (all print JSON):

  tokens    <root>   spec-token → app-var mapping via the built-in alias
                     table, with per-theme drift/missing/unmapped. The LLM
                     resolves only the `unmapped` leftovers.
  adoption  <root>   per-family tokenized/total declaration ratios
                     (color, font-size, radius, spacing) + escapees.
  contracts <root>   greppable design.md v2 component-contract checks
                     (focus ring, reduced motion, desktop measure, switch
                     on-color, native checkboxes, disclosure box, native
                      <dialog>, nav rules, icon-size strays, PWA icon family).
  vendored  <root>   byte-compare the app's _vendored/ copies against
                     project-scaffolding's canonical files.
  siblings  <root>   same-name top-level JS definitions across >=2 files
                     (the 7x-duplicated `schedule(ms)` case).
  all       <root>   every section in one JSON document.

Spec files are read from `~/.claude/design.md` + `design.dark.md` (junctioned
there by install.ps1); override with --spec/--spec-dark for tests. The
scaffold root for `vendored` defaults to E:/automation/project-scaffolding;
override with --scaffold.

Run it as a **directory** — the `__main__.py` beside this file makes the
package itself executable, so the invocation barely changes from the pre-split
single file (only the `.py` goes away):

    <python> C:/Users/rober/.claude/skills/_lib/design_lint all <repo-root>

Layout (fleet-config#564 — this was one 2071-line module carrying five
independent lenses behind a single CLI dispatcher, and the contract set grows
every time design.md gains a rule, so the file only went one way):

  files.py       which files a lens reads; how paths are spelled in findings
  spec.py        design.md frontmatter → a flat token dict
  css.py         comment stripping, @media flattening, custom props, declaration regexes
  markup.py      HTML/JS structural scans (app shell, nav nesting, emoji sites, editor modals)
  selectors.py   CSS selector splitting / compounding / scope resolution
  tokens.py      the `tokens` lens — spec role ↔ app custom property
  adoption.py    the `adoption` lens — per-family tokenized ratios
  contracts/     the `contracts` lens — `_CONTRACT_CHECKS` grouped by concern
  vendored.py    the `vendored` lens — byte-compare against the scaffold
  siblings.py    the `siblings` lens — duplicated top-level JS definitions
  cli.py         argparse + the JSON document assembly

The names below are the public surface, re-exported so `import design_lint`
keeps working unchanged for `design_sweep_scan.py`, `tests/test_design_lint.py`,
and anything else that reached into the single file.
"""
from __future__ import annotations

# Each lens entry point deliberately shadows the submodule of the same name:
# `design_lint.adoption` is the *function*, as it was pre-split, because that is
# the spelling every caller already uses and preserving it is the whole point of
# re-exporting here. Nothing imports the shadowed submodules by attribute.
from .adoption import adoption
from .contracts import contracts
from .css import (
    DARK_SELECTOR_HINTS,
    normalize_value,
    parse_custom_props,
    strip_comments,
)
from .files import (
    SKIP_DIR_PARTS,
    find_vendored_root,
    read_text,
    rel,
    repo_files,
)
from .markup import find_emoji_sites, nav_nested_in_app, standalone_shell_present
from .siblings import siblings
from .spec import parse_spec
from .tokens import ALIASES, OPTIONAL_ROLES, map_tokens
from .vendored import compare_icon_sprite, vendored
from .cli import main

__all__ = [
    "ALIASES",
    "DARK_SELECTOR_HINTS",
    "OPTIONAL_ROLES",
    "SKIP_DIR_PARTS",
    "adoption",
    "compare_icon_sprite",
    "contracts",
    "find_emoji_sites",
    "find_vendored_root",
    "main",
    "map_tokens",
    "nav_nested_in_app",
    "normalize_value",
    "parse_custom_props",
    "parse_spec",
    "read_text",
    "rel",
    "repo_files",
    "siblings",
    "standalone_shell_present",
    "strip_comments",
    "vendored",
]
