"""Does the target's cache-busting cover the JS import graph? (fleet-config#1140)

Read-only: parses the target's `src/static_versioning.py` (the fleet's per-app
copy, in about ten repos) and names its stamping strategy from the functions
it defines, never from words in comments or docstrings.

  fleet-hash   one hash over every asset (`fleet_hash_of`): any edit rotates every
               stamp, so a cached importer can never keep an old import URL.
  graph-hash   per-file stamps hashed over the file plus every module it
               transitively imports (a `*graph*hash*` function).
  per-file     per-file stamps from a file's own bytes only. Behind a long
               immutable cache, editing a nested module leaves a cached importer
               on its old import URLs (voice-transcriber#220/#221). Unsafe.
  unknown      the module exists but its shape is not recognised.
  none         no such module: nothing to check.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

MODULE = Path("src") / "static_versioning.py"
PER_FILE_FUNCS = {"asset_hash", "_short_hash", "compute_asset_hashes"}


def classify(root: Path) -> dict:
    """`{"strategy": fleet-hash|graph-hash|per-file|unknown|none}` for the repo at `root`."""
    path = Path(root) / MODULE
    if not path.is_file():
        return {"strategy": "none"}
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (SyntaxError, ValueError):
        return {"strategy": "unknown"}
    funcs = {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    if "fleet_hash_of" in funcs:
        return {"strategy": "fleet-hash"}
    if any(re.search(r"graph.*hash|hash.*graph", name) for name in funcs):
        return {"strategy": "graph-hash"}
    if funcs & PER_FILE_FUNCS:
        return {"strategy": "per-file"}
    return {"strategy": "unknown"}
