"""Measure a directory tree for the fleet-root move without following reparse points.

One walk yields everything the cut-over needs from a tree: total bytes, file and
directory counts, the heaviest folders two levels down, every reparse point
(junction or symlink) with its target, and every `index.lock` left in a `.git`
folder. Junctions are recorded and never entered, because the tree holds
worktree `.venv` junctions that point back into the same tree and following
them would count the venvs twice.

    python -I measure_tree.py measure <root> [--top N]
    python -I measure_tree.py compare <source> <target>

Both print one JSON object on stdout. `compare` exits 0 only when the two trees
hold the same file count, byte total and set of reparse points with the same
targets; any difference, or a walk error, exits 1.

Runs with the base interpreter (`-I`, stdlib only), not a venv inside the tree,
so nothing in the tree is held open while the cut-over renames it.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path

_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


@dataclass
class TreeReport:
    """What one walk of a tree found."""

    root: str
    bytes: int = 0
    files: int = 0
    dirs: int = 0
    folder_bytes: dict[str, int] = field(default_factory=dict)
    reparse_points: dict[str, str] = field(default_factory=dict)
    index_locks: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _is_reparse(entry: os.DirEntry) -> bool:
    if entry.is_symlink():
        return True
    try:
        attrs = entry.stat(follow_symlinks=False).st_file_attributes  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return False
    return bool(attrs & _REPARSE)


def _link_target(path: str) -> str:
    try:
        return os.readlink(path)
    except OSError as exc:
        return f"<unreadable: {exc.__class__.__name__}>"


def _bucket(rel_parts: list[str]) -> list[str]:
    """The folder keys a file under `rel_parts` counts toward: depth 1 and depth 2."""
    keys = []
    if len(rel_parts) >= 2:
        keys.append(rel_parts[0])
    if len(rel_parts) >= 3:
        keys.append(f"{rel_parts[0]}/{rel_parts[1]}")
    return keys


def measure(root: Path) -> TreeReport:
    """Walk `root` once, iteratively, never entering a reparse point."""
    report = TreeReport(root=str(root))
    stack: list[tuple[str, list[str]]] = [(str(root), [])]
    while stack:
        current, rel = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError as exc:
            report.errors.append(f"{current}: {exc.__class__.__name__}: {exc.strerror}")
            continue
        report.dirs += 1
        for entry in entries:
            parts = rel + [entry.name]
            rel_key = "/".join(parts)
            if _is_reparse(entry):
                report.reparse_points[rel_key] = _link_target(entry.path)
                continue
            try:
                if entry.is_dir(follow_symlinks=False):
                    stack.append((entry.path, parts))
                    continue
                size = entry.stat(follow_symlinks=False).st_size
            except OSError as exc:
                report.errors.append(f"{entry.path}: {exc.__class__.__name__}: {exc.strerror}")
                continue
            report.files += 1
            report.bytes += size
            for key in _bucket(parts):
                report.folder_bytes[key] = report.folder_bytes.get(key, 0) + size
            if entry.name == "index.lock" and len(parts) >= 2 and parts[-2] == ".git":
                report.index_locks.append(rel_key)
    return report


def _gib(n: int) -> float:
    return round(n / 1024 ** 3, 2)


def summary(report: TreeReport, top: int) -> dict[str, object]:
    """The JSON shape `measure` prints: totals, heaviest folders, links, locks."""
    heaviest = sorted(report.folder_bytes.items(), key=lambda kv: kv[1], reverse=True)[:top]
    top_level = sorted(((k, v) for k, v in report.folder_bytes.items() if "/" not in k),
                       key=lambda kv: kv[1], reverse=True)
    return {
        "root": report.root,
        "bytes": report.bytes,
        "gib": _gib(report.bytes),
        "files": report.files,
        "dirs": report.dirs,
        "top_level_gib": {k: _gib(v) for k, v in top_level},
        "heaviest_gib": {k: _gib(v) for k, v in heaviest},
        "reparse_points": report.reparse_points,
        "index_locks": report.index_locks,
        "errors": report.errors,
    }


def compare(source: TreeReport, target: TreeReport) -> dict[str, object]:
    """Differences between two walks; an empty `differences` list means a faithful copy."""
    diffs: list[str] = []
    if source.files != target.files:
        diffs.append(f"files: {source.files} vs {target.files}")
    if source.bytes != target.bytes:
        diffs.append(f"bytes: {source.bytes} vs {target.bytes}")
    for rel in sorted(set(source.reparse_points) | set(target.reparse_points)):
        a = source.reparse_points.get(rel)
        b = target.reparse_points.get(rel)
        if a != b:
            diffs.append(f"reparse point {rel}: {a!r} vs {b!r}")
    diffs.extend(f"walk error: {e}" for e in source.errors + target.errors)
    return {
        "source": {"files": source.files, "bytes": source.bytes, "links": len(source.reparse_points)},
        "target": {"files": target.files, "bytes": target.bytes, "links": len(target.reparse_points)},
        "differences": diffs,
        "match": not diffs,
    }


def main(argv: list[str] | None = None) -> int:
    """CLI entry: `measure <root>` or `compare <source> <target>`."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("measure")
    m.add_argument("root", type=Path)
    m.add_argument("--top", type=int, default=25)
    c = sub.add_parser("compare")
    c.add_argument("source", type=Path)
    c.add_argument("target", type=Path)
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    if args.cmd == "measure":
        if not args.root.is_dir():
            sys.stderr.write(f"not a directory: {args.root}\n")
            return 2
        sys.stdout.write(json.dumps(summary(measure(args.root), args.top), indent=2) + "\n")
        return 0
    for path in (args.source, args.target):
        if not path.is_dir():
            sys.stderr.write(f"not a directory: {path}\n")
            return 2
    result = compare(measure(args.source), measure(args.target))
    sys.stdout.write(json.dumps(result, indent=2) + "\n")
    return 0 if result["match"] else 1


if __name__ == "__main__":
    sys.exit(main())
