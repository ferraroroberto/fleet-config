"""A generated block in a repo's `.ignore` that hides its vendored copies from agent search.

Vendored scaffold copies out-number a repo's own code in a search, so an agent grepping its
own repo reads the copies first. ripgrep (which the Grep tool runs) honours a `.ignore`
file; git does not, and a direct Read of a listed file is unaffected. The block is built
from the `.fleet.toml` `[vendored]` dest paths and sits between two marker lines, so
hand-written lines elsewhere in the file survive a regeneration.

`/propagate-vendored` runs `write` on every re-vendor, and `vendored_drift.py scan` reports
a repo whose block no longer matches its manifest. Anything that cannot be established
(an unreadable manifest, a block with a single marker) is its own state, never `ok`.

CLI:

  write <repo>
      Regenerate the block from `<repo>/.fleet.toml`; prints `IGNORE=written|unchanged|removed`.
  check <repo>
      Prints `IGNORE=ok|missing|stale|malformed`; exit 0 ok, 1 drift, 3 not established.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fleet_core import fleet_toml  # noqa: E402
from fleet_core.utf8_stdio import ensure_utf8_stdio  # noqa: E402

ensure_utf8_stdio()

IGNORE_FILE = ".ignore"
BEGIN = "# BEGIN vendored-copies (generated from .fleet.toml [vendored]; edit the manifest, not this block)"
END = "# END vendored-copies"
# Characters that would turn a literal path into a pattern or a comment.
_PATTERN_CHARS = frozenset("*?[]!#\\")


class MalformedBlock(ValueError):
    """The file has a lone marker or more than one block, so it cannot be rewritten safely."""


def dest_patterns(dests: Iterable[str]) -> List[str]:
    """Root-anchored, sorted, de-duplicated ignore patterns for manifest dest paths.

    No trailing slash: a dest is a file for some components and a folder for others, and
    a bare anchored name matches both.
    """
    out = set()
    for dest in dests:
        path = dest.replace("\\", "/").strip().removeprefix("./").strip("/")
        parts = path.split("/")
        if not path or ".." in parts or "." in parts or _PATTERN_CHARS & set(path):
            raise ValueError(f"cannot ignore vendored dest {dest!r}: not a plain repo-relative path")
        out.add("/" + path)
    return sorted(out)


def _find_block(lines: Sequence[str]) -> Optional[tuple]:
    begins = [i for i, line in enumerate(lines) if line.strip() == BEGIN]
    ends = [i for i, line in enumerate(lines) if line.strip() == END]
    if not begins and not ends:
        return None
    if len(begins) != 1 or len(ends) != 1 or begins[0] > ends[0]:
        raise MalformedBlock("expected exactly one BEGIN/END vendored-copies marker pair")
    return begins[0], ends[0]


def apply_block(text: str, patterns: Sequence[str]) -> str:
    """`text` with its generated block set to `patterns`; no patterns removes the block."""
    eol = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines(keepends=True)
    found = _find_block(lines)
    block = [BEGIN + eol, *(p + eol for p in patterns), END + eol] if patterns else []
    if found:
        start, end = found
        if not patterns and start > 0 and not lines[start - 1].strip():
            start -= 1
        return "".join(lines[:start] + block + lines[end + 1:])
    if not patterns:
        return text
    head = text if not text or text.endswith(("\n", "\r")) else text + eol
    return head + (eol if head else "") + "".join(block)


def block_state(text: Optional[str], patterns: Sequence[str]) -> str:
    """`ok` when regenerating would change nothing, else `missing`, `stale` or `malformed`."""
    current = text or ""
    try:
        if apply_block(current, patterns) == current:
            return "ok"
        return "stale" if _find_block(current.splitlines(keepends=True)) else "missing"
    except MalformedBlock:
        return "malformed"


def read_dests(data: Optional[Dict[str, Any]]) -> List[str]:
    """The `dest` of every `[vendored]` entry in a parsed `.fleet.toml`."""
    table = (data or {}).get("vendored")
    if not isinstance(table, dict):
        return []
    return [e["dest"] for e in table.values() if isinstance(e, dict) and isinstance(e.get("dest"), str)]


def _read(path: Path) -> Optional[str]:
    try:
        with open(path, encoding="utf-8", newline="") as fh:
            return fh.read()
    except FileNotFoundError:
        return None


def repo_state(repo: Path, dests: Iterable[str]) -> str:
    """`block_state` for the repo's on-disk `.ignore`; `unknown` when it cannot be read."""
    try:
        return block_state(_read(repo / IGNORE_FILE), dest_patterns(dests))
    except (OSError, UnicodeDecodeError, ValueError):
        return "unknown"


def write_repo(repo: Path, dests: Iterable[str]) -> str:
    """Regenerate `<repo>/.ignore`; returns `written`, `unchanged` or `removed`."""
    patterns = dest_patterns(dests)
    path = repo / IGNORE_FILE
    old = _read(path)
    new = apply_block(old or "", patterns)
    if new == (old or ""):
        return "unchanged"
    if not new.strip():
        path.unlink()
        return "removed"
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(new)
    return "written"


def manifest_dests(repo: Path) -> List[str]:
    """The repo's vendored dest paths; raises `ValueError` when the manifest is absent or invalid."""
    data, error = fleet_toml.load_checked(repo)
    if data is None:
        raise ValueError(error or f"no readable {fleet_toml.FILENAME} in {repo}")
    return read_dests(data)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Generate or check a repo's vendored-copies .ignore block.")
    ap.add_argument("cmd", choices=("write", "check"))
    ap.add_argument("repo", type=Path)
    args = ap.parse_args(argv)
    try:
        dests = manifest_dests(args.repo)
        if args.cmd == "write":
            sys.stdout.write(f"IGNORE={write_repo(args.repo, dests)} patterns={len(dest_patterns(dests))}\n")
            return 0
    except (OSError, ValueError) as exc:  # MalformedBlock is a ValueError
        sys.stdout.write(f"IGNORE=unknown reason={exc}\n")
        return 3
    state = repo_state(args.repo, dests)
    sys.stdout.write(f"IGNORE={state}\n")
    return {"ok": 0, "unknown": 3}.get(state, 1)


if __name__ == "__main__":
    raise SystemExit(main())
