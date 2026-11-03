"""Refuse a shell command that would print a live secret-bearing file.

`secret_scan_guard`'s third sink. A command that echoes a config file (`cat`,
`grep`, `Get-Content`, an inline Python one-liner) puts every value it holds
into the session transcript, which the chief and anyone reading the session
see. A brief line saying "never print config values" is advice; this is the
enforcement. A command is refused only when all four hold:

1. a printing verb names the file;
2. the file's name is on the pattern list (`.env`, `.env.*`, `*config*` with a
   config extension, `*secret*`, `*token*.json`, `*credential*.json`);
3. the file exists;
4. git does not track it. A tracked file is already in history, so printing it
   leaks nothing new; that also exempts `tsconfig.json` and committed fixtures.
   A file outside any repo is judged by its name alone. When git cannot answer,
   the command is refused with its own message: an unconfirmed status is not a
   pass.

Sample files (`.sample`, `.example`, `.template`, `.dist` in the name) and
keys-only reads (`jq 'keys'`, `grep -o` ending at a key, `grep -c/-l/-q`,
`Select-String -Quiet`, Python that only calls `.keys()`) stay allowed.

Two machine-local switches in `hooks/projects.toml` `[global]`, so a false
positive is fixed at once without a merge: `secret_read_allow` (path globs that
may print) and `secret_read_guard = false` (turns this sink off; the commit and
publish sinks keep running).
"""

from __future__ import annotations

import fnmatch
import glob
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, List, Optional, Sequence, Tuple

import _lib
from secret_scan import shell_paths

# One `git ls-files` per pattern-matched existing file, on a command the
# session is waiting to run; past this the status counts as unknown and refuses.
LS_FILES_TIMEOUT_S = 5

_SAMPLE_MARKERS = (".sample", ".example", ".template", ".dist")
_NAME_GLOBS = ("*secret*", "*token*.json", "*credential*.json") + tuple(
    f"*config*.{ext}" for ext in ("json", "yaml", "yml", "toml", "ini"))

_READ_VERBS = frozenset({"cat", "type", "head", "tail", "less", "more", "gc", "get-content"})
# Search verbs take a pattern or program as their first operand.
_SEARCH_VERBS = frozenset({"grep", "egrep", "fgrep", "rg", "select-string", "sls",
                           "findstr", "sed", "awk", "jq"})
_GREP_VERBS = frozenset({"grep", "egrep", "fgrep", "rg"})
_PYTHON_VERBS = frozenset({"python", "python3", "py", "pythonw"})
_PATH_PARAMS = frozenset({"-path", "-literalpath", "-lp", "-pspath"})
_PATTERN_PARAMS = frozenset({"-e", "--regexp", "-pattern", "-f", "--file", "--from-file"})
_NESTED_FLAGS = frozenset({"-c", "-command", "/c"})

# grep's and rg's no-value output modes: a count, the file names, or nothing.
_SILENT_SHORTS = {"rg": "clq"}
_SILENT_SHORTS_DEFAULT = "clLq"
_SILENT_LONGS = frozenset({"--count", "--files-with-matches", "--files-without-match",
                           "--quiet", "--silent"})
_JQ_KEYS_RE = re.compile(r"^(?:\.[\w.\[\]\"]*\|)?(?:keys_unsorted|keys|\[paths\]|paths)(?:\[\])?$")
_PY_VALUE_RE = re.compile(
    r"\.values\(|\.items\(|\.get\(|\[\s*['\"]|\bread\(\)|readlines|read_text|read_bytes"
    r"|\bdumps?\(|pprint")
_PY_LITERAL_RE = re.compile(r"""(['"])([^'"\n]+)\1""")
_FINDSTR_OPT_RE = re.compile(r"^/[A-Za-z]+(?::.*)?$")
_ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_CONTINUATION_RE = re.compile(r"[\\`]\r?\n")

REFUSAL = (
    "Blocked: this command would print the values of a secret-bearing file ({path}) "
    "into the session transcript, where everyone reading the session sees them. "
    "Inspect its shape without the values: `jq 'keys' {path}` for JSON, "
    "`grep -o '^[A-Za-z_][A-Za-z0-9_]*=' {path}` for a .env, or a Python one-liner "
    "that prints only `.keys()`. A .sample/.example copy prints freely. "
    "A false positive is allowed by adding a path glob to `secret_read_allow` under "
    "`[global]` in hooks/projects.toml."
)
UNCONFIRMED = (
    "Blocked: this command would print a secret-bearing file ({path}), and git could "
    "not confirm whether it is tracked ({reason}). Until git answers, it is treated "
    "as a live secret. Inspect its shape without the values (`jq 'keys' {path}`), "
    "or allow the path with `secret_read_allow` under `[global]` in hooks/projects.toml."
)


@dataclass(frozen=True)
class _Settings:
    enabled: bool
    allow: Sequence[str]


def is_secret_name(name: str) -> bool:
    """True when a file called ``name`` is presumed to hold live secret values."""
    low = name.lower()
    if any(marker in low for marker in _SAMPLE_MARKERS):
        return False
    if low == ".env" or low.startswith(".env."):
        return True
    return any(fnmatch.fnmatchcase(low, pattern) for pattern in _NAME_GLOBS)


def _segments(text: str) -> Iterator[str]:
    """``text`` split at `;`, `|`, `&` and newlines that sit outside quotes."""
    quote: Optional[str] = None
    start = i = 0
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == "\\" and quote == '"':
                i += 1
            elif ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
        elif ch in ";|&\n":
            yield text[start:i]
            start = i + 1
        i += 1
    yield text[start:]


def _heredoc_bodies(text: str) -> List[str]:
    """Every heredoc body in ``text``, in opener order (mirrors `_lib.strip_heredoc_bodies`)."""
    bodies: List[str] = []
    pending: List[str] = []
    current: List[str] = []
    for line in text.split("\n"):
        if pending:
            if line.strip() == pending[0]:
                pending.pop(0)
                bodies.append("\n".join(current))
                current = []
            else:
                current.append(line)
            continue
        pending.extend(match.group("delim") for match in _lib.HEREDOC_RE.finditer(line))
    return bodies


def _verb(word: str) -> str:
    name = re.split(r"[\\/]", word)[-1].lower()
    return name[:-4] if name.endswith(".exe") else name


def _command_words(words: List[str]) -> List[str]:
    """``words`` without the call operator, env assignments and prefix words before the verb."""
    i = 0
    while i < len(words) and (words[i] == "&" or _ENV_ASSIGN_RE.match(words[i])
                              or words[i].lower() in _lib.COMMAND_PREFIX_WORDS):
        i += 1
    return words[i:]


def _split_operands(verb: str, args: List[str]) -> Tuple[Optional[str], List[str]]:
    """``(pattern, file operands)`` of a printing command's arguments."""
    pattern: Optional[str] = None
    files: List[str] = []
    positionals: List[str] = []
    searches = verb in _SEARCH_VERBS
    i = 0
    while i < len(args):
        word, low = args[i], args[i].lower()
        if low in _PATH_PARAMS and i + 1 < len(args):
            files.append(args[i + 1])
            i += 2
            continue
        if searches and low in _PATTERN_PARAMS and i + 1 < len(args):
            pattern = args[i + 1]
            i += 2
            continue
        if searches and low.startswith("--regexp="):
            pattern = word.split("=", 1)[1]
        elif verb == "findstr" and _FINDSTR_OPT_RE.match(word):
            if low[:3] in ("/c:", "/g:"):
                pattern = word[3:]
        elif not (word.startswith("-") and word != "-"):
            positionals.append(word)
        i += 1
    if searches and pattern is None and positionals:
        pattern = positionals.pop(0)
    return pattern, files + positionals


def _keys_only(verb: str, args: List[str], pattern: Optional[str]) -> bool:
    """True when the command prints key names, counts or file names, never values."""
    if verb == "jq":
        return pattern is not None and bool(_JQ_KEYS_RE.match(re.sub(r"\s+", "", pattern)))
    if verb in ("select-string", "sls"):
        return any(a.lower() == "-quiet" for a in args)
    if verb not in _GREP_VERBS:
        return False
    shorts = "".join(a[1:] for a in args if a.startswith("-") and not a.startswith("--"))
    longs = {a.split("=", 1)[0] for a in args if a.startswith("--")}
    silent = _SILENT_SHORTS.get(verb, _SILENT_SHORTS_DEFAULT)
    if any(flag in shorts for flag in silent) or longs & _SILENT_LONGS:
        return True
    only_matching = "o" in shorts or "--only-matching" in longs
    # A dot could let the match run on into the value (`.*=`), so a key pattern names no dot.
    return (only_matching and pattern is not None and "." not in pattern
            and pattern.endswith(('":', "=")))


def _python_keys_only(source: str) -> bool:
    return ".keys()" in source and not _PY_VALUE_RE.search(source)


def _existing_secret_files(raw: str, base: Optional[Path]) -> Iterator[Path]:
    path = shell_paths.resolve(raw, base)
    if path is None:
        return
    paths = [Path(p) for p in glob.glob(str(path))] if any(c in raw for c in "*?[") else [path]
    for candidate in paths:
        if is_secret_name(candidate.name) and candidate.is_file():
            yield candidate


def _python_reads(args: List[str], bodies: List[str], base: Optional[Path]) -> Iterator[Path]:
    """Secret files an inline Python program (`-c` or a heredoc) names and may print."""
    sources = list(bodies)
    if "-c" in args and args.index("-c") + 1 < len(args):
        sources.append(args[args.index("-c") + 1])
    for source in sources:
        if _python_keys_only(source):
            continue
        names = [m.group(2) for m in _PY_LITERAL_RE.finditer(source)]
        names += [a for a in args if a != source and not a.startswith(("-", "<"))]
        for raw in names:
            yield from _existing_secret_files(raw, base)


def _print_reads(verb: str, args: List[str], base: Optional[Path]) -> Iterator[Path]:
    """Secret files a `cat`/`grep`/`Get-Content`-style command prints the values of."""
    if verb == "sed" and any(a.startswith(("-i", "--in-place")) for a in args):
        return  # an in-place edit writes the file back and prints nothing
    pattern, files = _split_operands(verb, args)
    if _keys_only(verb, args, pattern):
        return
    for raw in files:
        yield from _existing_secret_files(raw, base)


def printed_secret_files(command: str, base: Optional[Path], _nested: bool = False) -> Iterator[Path]:
    """Every existing secret-named file ``command`` would print, before the git and allow checks."""
    text = _CONTINUATION_RE.sub(" ", command)
    bodies = iter(_heredoc_bodies(text))
    current = base
    for segment in _segments(_lib.strip_heredoc_bodies(text)):
        seg_bodies = [next(bodies, "") for _ in _lib.HEREDOC_RE.finditer(segment)]
        words = _command_words(_lib.shell_words(segment))
        if not words:
            continue
        verb = _verb(words[0])
        if verb in shell_paths.CD_VERBS or verb in shell_paths.POP_VERBS:
            current = shell_paths.follow_cd([verb, *words[1:]], current)
        elif verb in _PYTHON_VERBS:
            yield from _python_reads(words[1:], seg_bodies, current)
        elif verb in _lib.NESTED_SHELLS and not _nested:
            flags = [i for i, w in enumerate(words) if w.lower() in _NESTED_FLAGS]
            if flags and flags[0] + 1 < len(words):
                yield from printed_secret_files(words[flags[0] + 1], current, _nested=True)
        elif verb in _READ_VERBS or verb in _SEARCH_VERBS:
            yield from _print_reads(verb, words[1:], current)


def _settings() -> _Settings:
    """The two `[global]` switches; an unreadable registry keeps the sink on with no allows."""
    try:
        globals_ = _lib.load_registry().globals
    except (OSError, ValueError) as exc:
        _lib.logger.info("ℹ️ secret_scan_guard: projects.toml unreadable (%s); read sink stays on", exc)
        return _Settings(enabled=True, allow=())
    return _Settings(enabled=globals_.secret_read_guard, allow=globals_.secret_read_allow)


def _allowed(path: Path, allow: Sequence[str]) -> bool:
    text = path.as_posix().lower()
    return any(fnmatch.fnmatchcase(text, pattern.replace("\\", "/").lower())
               or fnmatch.fnmatchcase(path.name.lower(), pattern.lower()) for pattern in allow)


def tracked_status(path: Path) -> Tuple[Optional[bool], str]:
    """``(True, "")`` tracked; ``(False, "")`` untracked or outside any repo; ``(None, why)`` unknown."""
    try:
        res = _lib.run_git(["-C", str(path.parent), "ls-files", "--error-unmatch", "--", path.name],
                           timeout=LS_FILES_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, type(exc).__name__
    if res.returncode == 0:
        return True, ""
    err = (res.stderr or "").strip()
    if (res.returncode == 1 and "did not match" in err) or "not a git repository" in err.lower():
        return False, ""
    return None, (err.splitlines() or [f"git exit {res.returncode}"])[0]


def refusal(command: str, base: Path) -> Optional[str]:
    """The refusal for a command that prints a live secret file, or ``None`` to allow it."""
    candidates = list(dict.fromkeys(printed_secret_files(command, base)))
    if not candidates:
        return None
    settings = _settings()
    if not settings.enabled:
        return None
    unconfirmed: Optional[str] = None
    for path in candidates:
        if _allowed(path, settings.allow):
            continue
        tracked, reason = tracked_status(path)
        if tracked is False:
            return REFUSAL.format(path=path.as_posix())
        if tracked is None and unconfirmed is None:
            unconfirmed = UNCONFIRMED.format(path=path.as_posix(), reason=reason)
    return unconfirmed
