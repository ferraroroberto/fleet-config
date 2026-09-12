"""Shared state and result helpers for the individual contract checks.

`_ContractsCtx` is built once per run by `contracts()`; `_result` is the single
shape every check returns; `_evidence`/`_loc_at` turn a position in one of the
concatenated `/*FILE ...*/`-delimited blobs back into a `file:line`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class _ContractsCtx:
    """Shared, precomputed state read by every individual contract check —
    built once in `contracts()` so each `_check_*` function stays a thin,
    independently readable unit instead of a 600-line inline body."""
    root: Path
    css_all: str
    markup_all: str
    # markup_all minus third-party `vendor/` bundles — what the *app*
    # authored, for checks that judge the app's own design choices.
    markup_own: str
    spec_light: Dict[str, str]
    spec_dark: Optional[Dict[str, str]]
    html_files: List[Path]
    js_files: List[Path]
    index_files: List[Path]
    vendored_root: Optional[Path]
    modals: List[dict]


def _evidence(blob: str, pattern: str, flags: int = 0) -> Optional[str]:
    m = re.search(pattern, blob, flags)
    if not m:
        return None
    return _loc_at(blob, m.start())


def _loc_at(blob: str, pos: int) -> str:
    fh = blob.rfind("/*FILE ", 0, pos)
    if fh < 0:
        fname, start = "?", 0
    else:
        end = blob.find("*/", fh)
        fname = blob[fh + 7:end]
        # Content starts after the newline that ends the marker line — that
        # newline belongs to the marker, not the file (fleet-config#858).
        start = blob.find("\n", end) + 1
    line = blob.count("\n", start, pos) + 1
    return f"{fname}:{line}"


def _result(check_id: str, status: str, detail: str, ev: Optional[str] = None) -> dict:
    return {"id": check_id, "status": status, "detail": detail, "evidence": ev}
