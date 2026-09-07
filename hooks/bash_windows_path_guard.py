"""Block unquoted Windows drive-letter backslash paths in Bash commands.

Triggers on `PreToolUse` for `Bash` only (PowerShell handles backslashes
natively — no issue there). **Blocking**, not a nudge: an unquoted drive-letter
path like `E:\\automation` gets silently word-split by Bash — each unescaped
backslash is stripped (`E:\\automation` -> `E:automation`) — producing a broken
command with no warning. That's worse than the cross-shell payload traps
`gh_body_file_guard.py` nudges on, which merely mangle a `gh` body; this one
can point a whole command at the wrong path.

Detection:

1. `DRIVE_PATH_RE` finds drive-letter path tokens (`E:\\automation`,
   `C:\\Users\\rober\\file`).
2. `_safe_mask()` linearly scans the command marking byte offsets "safe":
   inside single quotes, double quotes, or a heredoc body. Bash only strips
   backslashes in unquoted word-splitting context — double-quoted keeps them
   (backslash is only special before `$`/backtick/`"`/backslash inside double
   quotes), single-quoted is always literal, and heredoc bodies are always
   literal for this purpose regardless of whether the delimiter was quoted.
   Heredoc-awareness matters specifically in this repo: `git commit -m
   "$(cat <<'EOF' ... EOF)"` is how every commit message here is built, and
   some legitimately reference example backslash paths as prose.
3. `find_unsafe_drive_paths()` returns regex hits with zero overlap with the
   safe mask — genuinely unquoted matches only.

Known, accepted limitation: this is a heuristic quote/heredoc scanner, not a
full shell parser (process substitution, arrays not modeled) — it fails open
on anything missed, never fails closed on a legitimate command.

Allow-listed (passes through silently): a forward-slash path, a double-quoted
backslash path, a single-quoted backslash path, a backslash path inside a
heredoc body, the same command on `PowerShell` (guard is Bash-only), and a
plain command with no drive path.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib  # noqa: E402


# Drive-letter path token: `E:\automation`, `C:\Users\rober\file.txt`.
DRIVE_PATH_RE = re.compile(r"[A-Za-z]:\\[^\s'\"]*")

# Heredoc opener: `<<EOF`, `<<-EOF`, `<<'EOF'`, `<<"EOF"`.
HEREDOC_TOKEN_RE = re.compile(
    r"<<(?P<dash>-)?\s*(?:'(?P<d1>\w+)'|\"(?P<d2>\w+)\"|(?P<d3>\w+))"
)


def _safe_mask(cmd: str) -> list[bool]:
    """Mark each byte offset in `cmd` as safe (protected from backslash
    stripping) if it's inside single quotes, double quotes, or a heredoc body.
    """
    n = len(cmd)
    safe = [False] * n
    in_single = False
    in_double = False
    i = 0
    while i < n:
        ch = cmd[i]

        if in_single:
            safe[i] = True
            if ch == "'":
                in_single = False
            i += 1
            continue

        if in_double:
            safe[i] = True
            if ch == "\\" and i + 1 < n:
                safe[i + 1] = True
                i += 2
                continue
            if ch == '"':
                in_double = False
            i += 1
            continue

        if ch == "'":
            in_single = True
            safe[i] = True
            i += 1
            continue

        if ch == '"':
            in_double = True
            safe[i] = True
            i += 1
            continue

        if ch == "<" and cmd[i:i + 2] == "<<":
            m = HEREDOC_TOKEN_RE.match(cmd, i)
            if m:
                delim = m.group("d1") or m.group("d2") or m.group("d3")
                strip_tabs = m.group("dash") == "-"
                nl = cmd.find("\n", m.end())
                if nl == -1:
                    i = m.end()
                    continue
                body_begin = nl + 1
                pos = body_begin
                terminator_start = n
                while True:
                    line_end = cmd.find("\n", pos)
                    line = cmd[pos:line_end if line_end != -1 else n]
                    check_line = line.lstrip("\t") if strip_tabs else line
                    if check_line == delim:
                        terminator_start = pos
                        break
                    if line_end == -1:
                        terminator_start = n
                        break
                    pos = line_end + 1
                for k in range(body_begin, terminator_start):
                    safe[k] = True
                i = terminator_start
                continue

        i += 1

    return safe


def find_unsafe_drive_paths(cmd: str) -> "list[re.Match[str]]":
    safe = _safe_mask(cmd)
    hits = []
    for m in DRIVE_PATH_RE.finditer(cmd):
        if any(safe[m.start():m.end()]):
            continue
        hits.append(m)
    return hits


def _trailing_backslash_count(s: str) -> int:
    n = 0
    for ch in reversed(s):
        if ch != "\\":
            break
        n += 1
    return n


def find_broken_quote_path(cmd: str) -> "re.Match[str] | None":
    """Find a drive-letter path immediately followed by a literal `"`, where
    the path itself ends in an ODD number of backslashes (fleet-config#800).

    Inside a double-quoted string, backslashes are consumed in escaping pairs
    (`_safe_mask()` above does the same) -- `\\\\"` (an even count) is a
    literal backslash followed by a real closing quote, but `\\"` (an odd
    count) leaves one backslash to escape the quote itself (`\\"` -> literal
    `"`) instead of closing the string. An odd-count match never actually
    closes: quote parity shifts for the rest of the command, and a later,
    genuinely-quoted path can land unquoted and get flagged instead. Returns
    the first such path, which is the actual defect to fix -- not necessarily
    the one `find_unsafe_drive_paths()` flags.
    """
    for m in DRIVE_PATH_RE.finditer(cmd):
        end = m.end()
        if end < len(cmd) and cmd[end] == '"' and _trailing_backslash_count(m.group(0)) % 2 == 1:
            return m
    return None


def main() -> None:
    payload = _lib.read_stdin_json()
    if _lib.tool_name(payload) != "Bash":
        _lib.allow()

    cmd = _lib.command_string(payload)
    if not cmd:
        _lib.allow()

    hits = find_unsafe_drive_paths(cmd)
    if not hits:
        _lib.allow()

    match = hits[0].group(0)
    mangled = match.replace("\\", "")
    forward = match.replace("\\", "/")
    message = (
        f"Blocked: unquoted Windows path `{match}` in a Bash command — Git Bash "
        f"strips backslashes in unquoted word-splitting context, so this would "
        f"actually run as `{mangled}`. Use forward slashes (`{forward}`) or quote "
        f"the path (single or double quotes)."
    )

    broken = find_broken_quote_path(cmd)
    if broken is not None and broken.start() < hits[0].start():
        broken_path = broken.group(0)
        message += (
            f" Root cause: `{broken_path}` ends in a backslash immediately "
            f"before a closing `\"` — inside double quotes, a trailing `\\` "
            f"escapes that quote instead of closing it, so the string never "
            f"actually closes and quote parity shifts for the rest of the "
            f"command, which is why `{match}` above landed unquoted. Drop the "
            f"trailing backslash (or use a forward slash) at the end of "
            f"`{broken_path}`."
        )

    _lib.block(message)


if __name__ == "__main__":
    main()
