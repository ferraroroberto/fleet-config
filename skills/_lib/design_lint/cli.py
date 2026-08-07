"""The `design_lint` command line — one subcommand per lens, JSON on stdout.

Deliberately the only place that knows about argparse, spec-file locations, and
the scaffold root: every lens above is importable and unit-tested without it.
The subcommand names are the public surface /design-sync and /design-sweep
invoke, so they never change when the internals are rearranged.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .adoption import adoption
from .contracts import contracts
from .css import parse_custom_props
from .files import read_text, rel, repo_files
from .siblings import siblings
from .spec import parse_spec
from .tokens import map_tokens
from .vendored import vendored


def _load_specs(args: argparse.Namespace) -> Tuple[Dict[str, str], Dict[str, str]]:
    home = Path.home() / ".claude"
    spec_light = Path(args.spec) if args.spec else home / "design.md"
    spec_dark = Path(args.spec_dark) if args.spec_dark else home / "design.dark.md"
    return parse_spec(read_text(spec_light)), parse_spec(read_text(spec_dark))


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
    ap.add_argument("--spec", help="override light spec path (tests)")
    ap.add_argument("--spec-dark", help="override dark spec path (tests)")
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
        spec_light, spec_dark = _load_specs(args)
    if args.command in ("tokens", "all"):
        app, main_file = _app_props(root, css_files)
        out["tokens"] = map_tokens(spec_light, spec_dark, app, main_file)
    if args.command in ("adoption", "all"):
        out["adoption"] = adoption(root, css_files)
    if args.command in ("contracts", "all"):
        out["contracts"] = contracts(root, css_files, html_files, js_files,
                                     spec_light, spec_dark)
    if args.command in ("vendored", "all"):
        out["vendored"] = vendored(root, Path(args.scaffold))
    if args.command in ("siblings", "all"):
        out["siblings"] = siblings(root, js_files)

    if args.command != "all" and len(out) == 1:
        out = next(iter(out.values()))  # type: ignore[assignment]
    print(json.dumps(out, indent=2, ensure_ascii=True))
    return 0
