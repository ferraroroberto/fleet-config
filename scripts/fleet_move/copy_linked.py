"""Copy the private backup's snapshot store to a new volume, keeping its hardlinks.

Each dated snapshot hardlinks every file unchanged since the day before, and
`latest/` is a hardlink mirror of the newest one, so the store's unique bytes are
a fraction of what a link-unaware copy (robocopy, Copy-Item) would write. This
copier keys every file on its (device, inode) pair: the first path seen is
copied, every later path with the same key becomes a hardlink to that copy, so
the destination keeps the same sharing and the nightly run can keep hardlinking
against yesterday's snapshot.

    python -I copy_linked.py <source> <dest> [--dry-run]

Refuses a destination that already exists and is not empty. Never follows a
reparse point; one in the store is reported and skipped. Prints one JSON object;
exits 0 when every file was copied or linked, 1 when any failed.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import sys
from pathlib import Path

_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def _is_reparse(st: os.stat_result) -> bool:
    if stat.S_ISLNK(st.st_mode):
        return True
    return bool(getattr(st, "st_file_attributes", 0) & _REPARSE)


def copy_linked(source: Path, dest: Path, dry_run: bool) -> dict[str, object]:
    """Copy `source` into `dest`, recreating hardlinks between files that share an inode."""
    first_copy: dict[tuple[int, int], Path] = {}
    copied = linked = unique_bytes = 0
    skipped: list[str] = []
    errors: list[str] = []
    for current, dirnames, filenames in os.walk(source):
        here = Path(current)
        rel_dir = here.relative_to(source)
        for name in list(dirnames):
            if _is_reparse(os.stat(here / name, follow_symlinks=False)):
                skipped.append(str(rel_dir / name))
                dirnames.remove(name)
            elif not dry_run:
                # runtime-write-exempt: operator-named snapshot-store destination on another volume, its whole purpose
                (dest / rel_dir / name).mkdir(parents=True, exist_ok=True)
        for name in filenames:
            src = here / name
            out = dest / rel_dir / name
            try:
                st = os.stat(src, follow_symlinks=False)
                if _is_reparse(st):
                    skipped.append(str(rel_dir / name))
                    continue
                key = (st.st_dev, st.st_ino)
                prior = first_copy.get(key)
                if prior is not None:
                    linked += 1
                    if not dry_run:
                        os.link(prior, out)
                    continue
                first_copy[key] = out
                copied += 1
                unique_bytes += st.st_size
                if not dry_run:
                    # runtime-write-exempt: operator-named snapshot-store destination on another volume, its whole purpose
                    shutil.copy2(src, out)
            except OSError as exc:
                errors.append(f"{rel_dir / name}: {exc.__class__.__name__}: {exc.strerror}")
    return {
        "source": str(source),
        "dest": str(dest),
        "dry_run": dry_run,
        "copied": copied,
        "linked": linked,
        "unique_bytes": unique_bytes,
        "skipped_reparse_points": skipped,
        "errors": errors,
        "ok": not errors,
    }


def main(argv: list[str] | None = None) -> int:
    """CLI entry: copy one snapshot store, or list what a copy would do with --dry-run."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", type=Path)
    parser.add_argument("dest", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    if not args.source.is_dir():
        sys.stderr.write(f"source is not a directory: {args.source}\n")
        return 2
    if args.dest.exists() and any(args.dest.iterdir()):
        sys.stderr.write(f"destination exists and is not empty: {args.dest}\n")
        return 2
    if not args.dry_run:
        # runtime-write-exempt: operator-named snapshot-store destination on another volume, its whole purpose
        args.dest.mkdir(parents=True, exist_ok=True)
    result = copy_linked(args.source, args.dest, args.dry_run)
    sys.stdout.write(json.dumps(result, indent=2) + "\n")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
