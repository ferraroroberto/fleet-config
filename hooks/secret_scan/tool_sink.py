"""Refuse a Claude `Read` or `Grep` call that would print a live secret-bearing file.

These two tools print a file without a shell, so the shell read sink never sees
them. What counts as a secret-bearing file, the tracked check and the
`[global]` switches are `read_sink`'s, applied unchanged to the files a call
names:

- `Read` prints its `file_path`.
- `Grep` prints values only in `output_mode: content`; `files_with_matches`
  (the default) and `count` pass, like `grep -l`/`-c`. A `path` that is a file
  is that file. A `glob` (braces expanded) is expanded under the search root
  one directory level per path component, as the shell sink expands `cat
  config/*.json`; `**` counts as one level. A recursive walk is out of reach:
  a repo's gitignored state folders hold tens of thousands of files, and a cold
  walk of them takes seconds a hook on every Grep cannot spend. A directory
  search with no glob is not judged: ripgrep skips gitignored files there,
  which is where live secret files sit.
"""

from __future__ import annotations

import glob
import re
from pathlib import Path
from typing import Any, Iterator, List, Mapping, Optional

import _lib

from secret_scan import read_sink

_BRACE_RE = re.compile(r"\{([^{}]*)\}")


def _tool_path(raw: Any, base: Path) -> Optional[Path]:
    if not isinstance(raw, str) or not raw.strip():
        return None
    path = Path(_lib.msys_to_drive(raw.strip())).expanduser()
    return path if path.is_absolute() else base / path


def _expand_braces(pattern: str) -> List[str]:
    """``a.{json,toml}`` as ``[a.json, a.toml]``, as ripgrep reads a glob."""
    match = _BRACE_RE.search(pattern)
    if not match:
        return [pattern]
    head, tail = pattern[:match.start()], pattern[match.end():]
    return [out for part in match.group(1).split(",") for out in _expand_braces(head + part + tail)]


def _printed_files(tool: str, tool_input: Mapping[str, Any], base: Path) -> Iterator[Path]:
    """Every path a `Read` or content-mode `Grep` call would print, existing or not."""
    if tool == "Read":
        path = _tool_path(tool_input.get("file_path"), base)
        if path is not None:
            yield path
        return
    if tool != "Grep" or tool_input.get("output_mode") != "content":
        return
    root = _tool_path(tool_input.get("path"), base) or base
    if root.is_file():
        yield root
        return
    glob_text = tool_input.get("glob")
    if not (isinstance(glob_text, str) and glob_text.strip() and root.is_dir()):
        return
    for pattern in _expand_braces(glob_text.strip()):
        rel = pattern.lstrip("/").removeprefix("./")
        for match in glob.glob(str(root / rel), include_hidden=True):
            yield Path(match)


def tool_refusal(tool: str, tool_input: Mapping[str, Any], base: Path) -> Optional[str]:
    """The refusal for a `Read`/`Grep` call that prints a live secret file, or ``None``."""
    return read_sink.judge(path for path in _printed_files(tool, tool_input, base)
                           if read_sink.is_secret_name(path.name) and path.is_file())
