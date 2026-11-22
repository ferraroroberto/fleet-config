"""Copy pre-move `E--automation-*` Claude project folders into their `C--automation-*` twins.

Claude Code keys each project's history folder by the working-directory path.
Since `E:/automation` became a junction to `C:/automation`, sessions write to
`~/.claude/projects/C--automation-*`, while history and per-project memory from
before the move stay in the matching `E--automation-*` folders. This copies the
old into the new and never moves, deletes or overwrites anything, so the `E--`
folders stay until the owner deletes them.

    python -I copy_claude_history.py            # dry run: lists what would be copied
    python -I copy_claude_history.py --apply    # copies

Rules, per source folder:

- A file whose relative path already exists in the twin is skipped.
- `memory/MEMORY.md` is merged: source lines the twin lacks are appended.
- Any other `memory/` file that exists in both with different bytes is a clash:
  the newer file (by modified time) wins and the clash is listed. Only this case
  replaces an existing file.
- Links and junctions inside a folder are reported and not followed.

`--apply` refuses to run while a Claude Code session is open, because live
sessions write into the `C--` folders. A dry run only reports how many are open.
Output is counts and file names only, never file contents.

Exits 0 when done, 1 when any file failed, 2 on a bad projects directory, 3 when
`--apply` was refused over an open session.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path

# The one process-liveness check (never os.kill on Windows) lives with the hooks.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "hooks"))
from lane_compact import pid_alive

SOURCE_PREFIX = "E--automation"
TARGET_PREFIX = "C--automation"
MEMORY_DIR = "memory"
MEMORY_INDEX = "MEMORY.md"

_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


@dataclass
class FolderResult:
    """What one source folder's copy did, or would do."""

    source: str
    target: str
    new_target: bool = False
    copied: int = 0
    skipped: int = 0
    index_lines_added: int = 0
    clashes: list[str] = field(default_factory=list)
    links_skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def twin_name(name: str) -> str | None:
    """The `C--` folder name for an `E--automation[-x]` folder name, else None."""
    if name == SOURCE_PREFIX or name.startswith(SOURCE_PREFIX + "-"):
        return TARGET_PREFIX + name[len(SOURCE_PREFIX):]
    return None


def _is_link(path: Path) -> bool:
    st = os.stat(path, follow_symlinks=False)
    return stat.S_ISLNK(st.st_mode) or bool(getattr(st, "st_file_attributes", 0) & _REPARSE)


def live_session_pids(sessions_dir: Path) -> list[int] | None:
    """Pids of running Claude Code sessions from `<pid>.json` registry files.

    Returns None when the registry directory is missing, so the caller can say the
    check could not be made rather than report zero open sessions.
    """
    if not sessions_dir.is_dir():
        return None
    pids: list[int] = []
    for entry in sorted(sessions_dir.glob("*.json")):
        try:
            pid = json.loads(entry.read_text(encoding="utf-8")).get("pid")
        except (OSError, ValueError, AttributeError):
            # An unreadable registry file may belong to a live session; count it open.
            pids.append(-1)
            continue
        if isinstance(pid, int) and not isinstance(pid, bool) and pid_alive(pid):
            pids.append(pid)
    return pids


def _walk_files(root: Path, result: FolderResult) -> list[Path]:
    """Every regular file under `root` as a relative path, never following links."""
    found: list[Path] = []
    for current, dirnames, filenames in os.walk(root):
        here = Path(current)
        for name in list(dirnames):
            if _is_link(here / name):
                result.links_skipped.append(str((here / name).relative_to(root)))
                dirnames.remove(name)
        for name in filenames:
            if _is_link(here / name):
                result.links_skipped.append(str((here / name).relative_to(root)))
            else:
                found.append((here / name).relative_to(root))
    return sorted(found)


def merge_index(source_text: str, target_text: str) -> tuple[str, int]:
    """`target_text` plus the source lines it lacks (compared after stripping), and how many were added."""
    have = {line.strip() for line in target_text.splitlines()}
    additions: list[str] = []
    for line in source_text.splitlines():
        if line.strip() and line.strip() not in have:
            have.add(line.strip())
            additions.append(line.rstrip())
    if not additions:
        return target_text, 0
    base = target_text if not target_text or target_text.endswith("\n") else target_text + "\n"
    return base + "\n".join(additions) + "\n", len(additions)


def _write_atomic(dest: Path, data: bytes, mtime: float | None = None) -> None:
    """Write `data` to `dest` through a sibling temp file so a crash never leaves a half file that a rerun would skip."""
    tmp = dest.with_name("." + dest.name + ".copy-tmp")
    try:
        # runtime-write-exempt: the operator-run copy into the Claude profile is this script's whole purpose
        tmp.write_bytes(data)
        if mtime is not None:
            os.utime(tmp, (mtime, mtime))
        # must-use-exempt: byte-exact copy of binary transcripts; atomic_write_text is text-only
        os.replace(tmp, dest)
    finally:
        if tmp.exists():
            tmp.unlink()


def _copy_file(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)  # runtime-write-exempt: see _write_atomic
    _write_atomic(dest, src.read_bytes(), src.stat().st_mtime)


def _handle_memory_file(src: Path, dest: Path, rel: Path, apply: bool, result: FolderResult) -> None:
    """Apply the memory rules to one file that exists in the source's `memory/` folder."""
    if not dest.exists():
        result.copied += 1
        if apply:
            _copy_file(src, dest)
        return
    if rel.name == MEMORY_INDEX:
        merged, added = merge_index(src.read_text(encoding="utf-8"), dest.read_text(encoding="utf-8"))
        result.index_lines_added += added
        if added and apply:
            _write_atomic(dest, merged.encode("utf-8"))
        return
    if src.read_bytes() == dest.read_bytes():
        result.skipped += 1
        return
    newer_source = src.stat().st_mtime > dest.stat().st_mtime
    result.clashes.append(f"{rel.as_posix()} (kept {'source' if newer_source else 'target'}, the newer)")
    if newer_source and apply:
        _copy_file(src, dest)


def copy_folder(src: Path, dst: Path, apply: bool) -> FolderResult:
    """Copy one `E--` folder into its twin under the rules in the module docstring."""
    result = FolderResult(source=src.name, target=dst.name, new_target=not dst.exists())
    for rel in _walk_files(src, result):
        dest = dst / rel
        try:
            if rel.parts[0] == MEMORY_DIR:
                _handle_memory_file(src / rel, dest, rel, apply, result)
            elif dest.exists():
                result.skipped += 1
            else:
                result.copied += 1
                if apply:
                    _copy_file(src / rel, dest)
        except (OSError, UnicodeDecodeError) as exc:
            result.errors.append(f"{rel.as_posix()}: {exc.__class__.__name__}")
    return result


def run(projects_dir: Path, apply: bool) -> list[FolderResult]:
    """Process every `E--automation*` folder that is a real directory, in name order."""
    results: list[FolderResult] = []
    for entry in sorted(projects_dir.iterdir(), key=lambda p: p.name):
        twin = twin_name(entry.name)
        if twin is None or not entry.is_dir() or _is_link(entry):
            continue
        results.append(copy_folder(entry, projects_dir / twin, apply))
    return results


def format_report(results: list[FolderResult], apply: bool) -> list[str]:
    """One line per folder, a line per clash or error, then a summary."""
    verb = "copied" if apply else "would copy"
    lines: list[str] = []
    for r in results:
        lines.append(
            f"{r.source} -> {r.target}{' (new folder)' if r.new_target else ''}: "
            f"{verb} {r.copied}, skipped {r.skipped}, clashes {len(r.clashes)}, "
            f"MEMORY.md lines {r.index_lines_added}"
        )
        lines.extend(f"  clash: {c}" for c in r.clashes)
        lines.extend(f"  link not followed: {link}" for link in r.links_skipped)
        lines.extend(f"  error: {e}" for e in r.errors)
    lines.append(
        f"{'APPLIED' if apply else 'DRY RUN (nothing written)'}: {len(results)} folders, "
        f"{verb} {sum(r.copied for r in results)}, skipped {sum(r.skipped for r in results)}, "
        f"clashes {sum(len(r.clashes) for r in results)}, "
        f"MEMORY.md lines {sum(r.index_lines_added for r in results)}, "
        f"errors {sum(len(r.errors) for r in results)}"
    )
    return lines


def main(argv: list[str] | None = None) -> int:
    """CLI entry: dry run by default, `--apply` to copy."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="copy; without it nothing is written")
    parser.add_argument("--projects-dir", type=Path, default=Path.home() / ".claude" / "projects")
    parser.add_argument("--sessions-dir", type=Path, default=Path.home() / ".claude" / "sessions")
    args = parser.parse_args(argv)
    if not args.projects_dir.is_dir():
        sys.stderr.write(f"projects directory not found: {args.projects_dir}\n")
        return 2
    live = live_session_pids(args.sessions_dir)
    if live is None:
        sys.stdout.write(f"note: session registry {args.sessions_dir} not found, open sessions not checked\n")
    elif live and args.apply:
        sys.stderr.write(f"refusing --apply: {len(live)} Claude Code session(s) open; close them all and rerun\n")
        return 3
    elif live:
        sys.stdout.write(f"note: {len(live)} Claude Code session(s) open; --apply would refuse\n")
    if args.apply and live is None:
        sys.stderr.write("refusing --apply: open sessions could not be checked (registry directory missing)\n")
        return 3
    results = run(args.projects_dir, args.apply)
    sys.stdout.write("\n".join(format_report(results, args.apply)) + "\n")
    return 1 if any(r.errors for r in results) else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
