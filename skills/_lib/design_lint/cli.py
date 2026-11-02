"""The `design_lint` command line — one subcommand per lens, JSON on stdout.

Deliberately the only place that knows about argparse, spec-file locations, and
the scaffold root: every lens above is importable and unit-tested without it.
The subcommand names are the public surface /design-sync and /design-sweep
invoke, so they never change when the internals are rearranged.

`--surface` picks the spec and the lens set: `web-app` (the default) reads
`design.md` + `design.dark.md`, `web-page` reads `design.web.md`. `--spec`
names the spec file instead of the user-scope copy. A spec that is not there
is its own outcome, `spec_status: not_found` with exit 3, never an empty lint
that would read as clean.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .accepted import apply_accepted
from .adoption import adoption
from .contracts import contracts
from .css import parse_custom_props
from .files import read_text, rel, repo_files
from .siblings import siblings
from .spec import parse_spec
from .tokens import map_tokens
from .vendored import vendored
from .web_page import adopted_only, web_contracts, web_tokens, without_build_copies

WEB_APP = "web-app"
WEB_PAGE = "web-page"
SPEC_NOT_FOUND_EXIT = 3


def _spec_paths(args: argparse.Namespace) -> List[Path]:
    """The spec files this run reads, in order: light then dark, or the one web-page spec."""
    home = Path.home() / ".claude"
    if args.surface == WEB_PAGE:
        return [Path(args.spec) if args.spec else home / "design.web.md"]
    return [Path(args.spec) if args.spec else home / "design.md",
            Path(args.spec_dark) if args.spec_dark else home / "design.dark.md"]


def _app_props(root: Path, css_files: List[Path]) -> Tuple[Dict[str, Dict[str, Tuple[str, int]]], str]:
    """Merge custom props across stylesheets; the file with the most wins naming."""
    merged: Dict[str, Dict[str, Tuple[str, int]]] = {"light": {}, "dark": {}}
    main_file, main_count = "", -1
    for path in css_files:
        themes = parse_custom_props(read_text(path), path.name)
        count = len(themes["light"]) + len(themes["dark"])
        if count > main_count:
            main_file, main_count = rel(root, path), count
        for theme in ("light", "dark"):
            for k, v in themes[theme].items():
                merged[theme].setdefault(k, v)
    return merged, main_file


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Deterministic design-system lint for /design-sync v2.")
    ap.add_argument("command", choices=["tokens", "adoption", "contracts", "vendored", "siblings", "all"])
    ap.add_argument("root", help="target repo root")
    ap.add_argument("--surface", choices=[WEB_APP, WEB_PAGE], default=WEB_APP,
                    help="which spec and lens set: a web app (design.md) or a web page (design.web.md)")
    ap.add_argument("--spec", help="spec file (default: the user-scope copy under ~/.claude)")
    ap.add_argument("--spec-dark", help="dark spec file for a web app (default: ~/.claude/design.dark.md)")
    # no-drive-path-exempt: fleet-root default for this machine; a flag or config overrides it
    ap.add_argument("--scaffold", default="E:/automation/project-scaffolding",
                    help="project-scaffolding root for the vendored byte-compare")
    args = ap.parse_args(argv)

    root = Path(args.root).resolve()
    if not root.is_dir():
        print(json.dumps({"error": f"not a directory: {root}"}))
        return 2

    css_files = repo_files(root, (".css",))
    html_files = repo_files(root, (".html",))
    js_files = repo_files(root, (".js",))

    out: Dict[str, object] = {}
    if args.command in ("tokens", "contracts", "all"):
        paths = _spec_paths(args)
        absent = [str(p) for p in paths if not p.is_file()]
        if absent:
            print(json.dumps({"surface": args.surface, "spec_status": "not_found", "spec": absent}))
            return SPEC_NOT_FOUND_EXIT
        texts = [read_text(p) for p in paths]
        specs = [parse_spec(t) for t in texts]
        out["surface"] = args.surface
        out["spec"] = [str(p) for p in paths]
    web_page = args.surface == WEB_PAGE
    if args.command in ("tokens", "all"):
        app, main_file = _app_props(root, css_files)
        out["tokens"] = (web_tokens(texts[0], specs[0], app, main_file) if web_page
                         else map_tokens(specs[0], specs[1], app, main_file))
    if args.command in ("adoption", "all"):
        out["adoption"] = adoption(root, css_files)
    if args.command in ("contracts", "all"):
        found = (web_contracts(root, css_files, html_files, js_files, specs[0]) if web_page
                 else contracts(root, css_files, html_files, js_files, specs[0], specs[1]))
        out["contracts"] = apply_accepted(root, found)
    if args.command in ("vendored", "all"):
        report = vendored(root, Path(args.scaffold), args.surface)
        out["vendored"] = adopted_only(report) if web_page else report
    if args.command in ("siblings", "all"):
        dupes = siblings(root, js_files)
        out["siblings"] = without_build_copies(root, dupes) if web_page else dupes

    if args.command != "all":
        out = out[args.command]  # type: ignore[assignment]
    print(json.dumps(out, indent=2, ensure_ascii=True))
    return 0
