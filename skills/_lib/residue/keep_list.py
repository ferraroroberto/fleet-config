"""The owner's keep-list: branches `branch_residue.py` must never delete.

A machine-local text file in the hooks state dir (`branch-keep-list.txt`, so
`CLAUDE_HOOKS_STATE_DIR` moves it and it is never tracked). One entry per line:

  <branch-glob>          applies in every repo
  <repo>:<branch-glob>   applies in the repo whose primary checkout folder is <repo>
  # comment              blank lines and `#` comments are ignored

A glob is `fnmatch` syntax, case-sensitive (git branch names are), and `*`
crosses `/`. A missing file is an empty list; a file that cannot be read or has
a malformed line is reported as its own state so the caller deletes nothing,
never read as "nothing is kept".

stdlib only.
"""

from __future__ import annotations

from fnmatch import fnmatchcase
from pathlib import Path
from typing import NamedTuple, Optional, Tuple

from fleet_core.hooks_state import state_dir

KEEP_LIST_FILENAME = "branch-keep-list.txt"


class KeepEntry(NamedTuple):
    repo: str  # "" = every repo
    pattern: str
    text: str  # the line as written, for the reason


class KeepList(NamedTuple):
    entries: Tuple[KeepEntry, ...] = ()
    error: str = ""  # non-empty = the list could not be established

    def match(self, repo_name: str, branch: str) -> Optional[KeepEntry]:
        for entry in self.entries:
            if entry.repo and entry.repo.lower() != repo_name.lower():
                continue
            if fnmatchcase(branch, entry.pattern):
                return entry
        return None


def keep_list_path() -> Path:
    return state_dir() / KEEP_LIST_FILENAME


def parse(text: str) -> KeepList:
    entries = []
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        repo, sep, pattern = line.partition(":")
        if not sep:
            repo, pattern = "", line
        repo, pattern = repo.strip(), pattern.strip()
        if not pattern or (sep and not repo):
            return KeepList(error=f"line {number} is not `<branch-glob>` or `<repo>:<branch-glob>`: {line!r}")
        entries.append(KeepEntry(repo, pattern, line))
    return KeepList(tuple(entries))


def load(path: Optional[Path] = None) -> KeepList:
    """The keep-list, an empty one when the file does not exist, `error` set when it cannot be read."""
    target = path or keep_list_path()
    try:
        text = target.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return KeepList()
    except (OSError, UnicodeDecodeError) as exc:
        return KeepList(error=f"{target.name} could not be read: {exc}")
    parsed = parse(text)
    return parsed._replace(error=f"{target.name}: {parsed.error}") if parsed.error else parsed
