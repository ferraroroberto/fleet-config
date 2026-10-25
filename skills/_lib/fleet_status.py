"""Read and flip a repo's fleet scope: `active`, or `dormant` (out of fleet-wide runs).

The switch is `hooks/projects.toml`'s `[global] architecture_ignore` array: a
repo listed there is dormant. The file is machine-local and gitignored, so a
flip changes this machine only and never needs a commit. See
`docs/fleet-scope.md`.

`set` is the one writer. It edits only the array's own lines, so every comment
and every other byte of the file survives; it re-parses the result, confirms
that the array is the only thing that changed, and replaces the file through a
temp file. App Launcher loads this module by path and calls `list_status()` and
`set_status()`.

CLI
---
  fleet_status.py list
  fleet_status.py get <repo>
  fleet_status.py set <repo> active|dormant
  (each takes --projects-toml <path> to work on another copy)

Exit 0 on success, 2 on a refusal (unknown repo, invalid value, a file that is
missing or does not parse, an array it cannot edit safely).

stdlib only.
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fleet_core import fleet_repo_scan  # noqa: E402
from fleet_core import hooks_state  # noqa: E402

ACTIVE = "active"
DORMANT = "dormant"
STATUSES = (ACTIVE, DORMANT)

_ARRAY_START = re.compile(r"^\s*architecture_ignore\s*=\s*\[(.*)$")
_ENTRY = re.compile(r'^\s*"([^"]+)"\s*,?\s*(#.*)?$')
_TABLE_HEADER = re.compile(r"^\s*\[")


class FleetStatusError(ValueError):
    """A refused read or write; the message names what is wrong."""


def _path(projects_toml: Optional[Path]) -> Path:
    return Path(projects_toml) if projects_toml else fleet_repo_scan._DEFAULT_PROJECTS_TOML


def _load(path: Path) -> dict:
    try:
        return fleet_repo_scan.load_projects_toml(path)
    except fleet_repo_scan.ProjectsTomlMissing as exc:
        raise FleetStatusError(str(exc)) from exc
    except tomllib.TOMLDecodeError as exc:
        raise FleetStatusError(f"{path} does not parse as TOML ({exc}); fix it by hand first") from exc


def _status(name: str, toml: dict) -> str:
    return DORMANT if name in fleet_repo_scan._ignored(toml) else ACTIVE


def _known(repo: str, toml: dict, path: Path) -> None:
    if repo not in fleet_repo_scan._tables(toml):
        raise FleetStatusError(f"unknown repo {repo!r}: no [{repo}] table with a cwd_prefix in {path}")


def list_status(projects_toml: Optional[Path] = None) -> List[Dict[str, str]]:
    """Every declared repo with its status, in name order."""
    toml = _load(_path(projects_toml))
    return [{"name": n, "status": _status(n, toml)} for n in sorted(fleet_repo_scan._tables(toml))]


def get_status(repo: str, projects_toml: Optional[Path] = None) -> str:
    path = _path(projects_toml)
    toml = _load(path)
    _known(repo, toml, path)
    return _status(repo, toml)


def _array_span(lines: List[str]) -> tuple[int, int]:
    """Line indexes of `architecture_ignore = [` and its closing `]` inside `[global]`."""
    in_global = False
    for i, line in enumerate(lines):
        if _TABLE_HEADER.match(line):
            in_global = line.strip() == "[global]"
            continue
        m = _ARRAY_START.match(line) if in_global else None
        if not m:
            continue
        if m.group(1).split("#", 1)[0].strip():
            raise FleetStatusError("[global] architecture_ignore must be a multi-line array, one repo per line")
        for j in range(i + 1, len(lines)):
            body = lines[j].strip()
            if body.startswith("]"):
                return i, j
            if body and not body.startswith("#") and not _ENTRY.match(lines[j].rstrip("\r\n")):
                raise FleetStatusError(f"cannot edit architecture_ignore line {j + 1}: {body!r}")
        break
    raise FleetStatusError("no multi-line [global] architecture_ignore array found")


def set_status(repo: str, value: str, projects_toml: Optional[Path] = None) -> str:
    """Set `repo` to `active` or `dormant`. Returns `changed` or `unchanged`."""
    if value not in STATUSES:
        raise FleetStatusError(f"invalid status {value!r}: use {ACTIVE} or {DORMANT}")
    path = _path(projects_toml)
    before = _load(path)
    _known(repo, before, path)
    if _status(repo, before) == value:
        return "unchanged"

    text = path.read_bytes().decode("utf-8")
    lines = text.splitlines(keepends=True)
    start, end = _array_span(lines)
    if value == DORMANT:
        newline = "\r\n" if lines[start].endswith("\r\n") else "\n"
        lines.insert(end, f'  "{repo}",{newline}')
    else:
        hits = [k for k in range(start + 1, end)
                if (m := _ENTRY.match(lines[k].rstrip("\r\n"))) and m.group(1) == repo]
        if len(hits) != 1:
            raise FleetStatusError(f"expected one {repo!r} line in architecture_ignore, found {len(hits)}")
        del lines[hits[0]]
    new_text = "".join(lines)

    after = tomllib.loads(new_text)
    ignore = list(before["global"]["architecture_ignore"])
    expected = dict(before, **{"global": dict(before["global"], architecture_ignore=(
        ignore + [repo] if value == DORMANT else [r for r in ignore if r != repo]))})
    if after != expected:
        raise FleetStatusError("the edit changed more than architecture_ignore; file left untouched")

    # `newline=""`: the file's own line endings were kept in `new_text`.
    hooks_state.atomic_write_text(path, new_text, newline="")
    return "changed"


def main(argv: Optional[List[str]] = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Read or flip a repo's fleet scope.")
    parser.add_argument("--projects-toml", type=Path, help="work on this copy instead of hooks/projects.toml")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    sub.add_parser("get").add_argument("repo")
    s = sub.add_parser("set")
    s.add_argument("repo")
    s.add_argument("status")
    args = parser.parse_args(argv)
    try:
        if args.cmd == "list":
            rows = list_status(args.projects_toml)
            for row in rows:
                print(f"{row['status']:<8} {row['name']}")
            dormant = sum(r["status"] == DORMANT for r in rows)
            print(f"{len(rows) - dormant} active, {dormant} dormant")
        elif args.cmd == "get":
            print(get_status(args.repo, args.projects_toml))
        else:
            outcome = set_status(args.repo, args.status, args.projects_toml)
            print(f"{args.repo}: {args.status} ({outcome})")
    except FleetStatusError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
