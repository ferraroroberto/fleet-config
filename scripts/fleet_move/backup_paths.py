"""Point the private backup's repo leg at a new source and destination in projects.toml.

The cut-over moves the fleet root to another volume, so the repo leg's
`source_root` and `dest` in the machine-local `[backup]` table change together:
setting only one would put source and destination on the same volume, which the
backup's preflight refuses. Every other line of the file is kept byte for byte;
a missing `[backup]` table is appended, a missing key is added under its header.

    python -I backup_paths.py <projects.toml> --source-root <dir> --dest <dir> [--dry-run]

Before writing, the original is copied to `<projects.toml>.pre-move` (refused if
that copy already exists, so a second run can never overwrite the only record of
the old values). Prints the old and new values as JSON.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - older interpreters
    import tomli as tomllib  # type: ignore[no-redef]

_HEADER = re.compile(r"^\s*\[([^\[\]]+)\]\s*(#.*)?$")


def _toml_string(value: str) -> str:
    return '"' + value.replace("\\", "/").replace('"', '\\"') + '"'


def set_backup_paths(text: str, values: dict[str, str]) -> str:
    """Return `text` with `[backup]`'s keys in `values` set, everything else unchanged."""
    lines = text.splitlines(keepends=True)
    newline = "\r\n" if "\r\n" in text else "\n"
    start = next((i for i, line in enumerate(lines)
                  if (m := _HEADER.match(line)) and m.group(1).strip() == "backup"), None)
    if start is None:
        tail = "" if not text or text.endswith(("\n", "\r\n")) else newline
        block = [f"{newline}[backup]{newline}"] + [f"{k} = {_toml_string(v)}{newline}" for k, v in values.items()]
        return text + tail + "".join(block)
    end = next((i for i in range(start + 1, len(lines)) if lines[i].lstrip().startswith("[")), len(lines))
    missing: list[str] = []
    for key, value in values.items():
        pattern = re.compile(rf"^(\s*){re.escape(key)}\s*=.*?(\r?\n)?$")
        for i in range(start + 1, end):
            m = pattern.match(lines[i])
            if m:
                lines[i] = f"{m.group(1)}{key} = {_toml_string(value)}{m.group(2) or ''}"
                break
        else:
            missing.append(f"{key} = {_toml_string(value)}{newline}")
    lines[start + 1:start + 1] = missing
    return "".join(lines)


def main(argv: list[str] | None = None) -> int:
    """CLI entry: rewrite one projects.toml's `[backup]` source_root and dest."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("projects_toml", type=Path)
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--dest", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    path: Path = args.projects_toml
    if not path.is_file():
        sys.stderr.write(f"projects.toml not found: {path}\n")
        return 2
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    old = tomllib.loads(text).get("backup", {})
    new_text = set_backup_paths(text, {"source_root": args.source_root, "dest": args.dest})
    new = tomllib.loads(new_text).get("backup", {})
    result = {
        "file": str(path),
        "dry_run": args.dry_run,
        "old": {k: old.get(k) for k in ("source_root", "dest")},
        "new": {k: new.get(k) for k in ("source_root", "dest")},
    }
    if not args.dry_run:
        keep = path.with_name(path.name + ".pre-move")
        if keep.exists():
            sys.stderr.write(f"refusing: {keep} already exists\n")
            return 2
        # runtime-write-exempt: the machine-local projects.toml the operator names, edited in place on purpose
        shutil.copy2(path, keep)
        # runtime-write-exempt: the machine-local projects.toml the operator names, edited in place on purpose
        path.write_bytes(new_text.encode("utf-8"))
        result["kept_original"] = str(keep)
    sys.stdout.write(json.dumps(result, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
