"""The program a shell command runs, as one safe word for an error signature.

A command is model-written text and a signature reaches a published page, so
only a name from `PROGRAMS` ever leaves this module: another script is
`<script>`, any other word `<other>`.

A log records one exit code for the whole line, so the command named is the one
whose status a shell returns: the last real one. Setup and output commands
(`cd`, `echo`, `exit`), output filters after a pipe (`| tail`) and wrappers
(`timeout`, `env`) are skipped, and a variable set on the same line is read
through. It is a good reading, not proof of which step failed. stdlib only.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# The shell tokeniser and wrapper vocabulary the hooks already share.
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "hooks"))
import _lib  # noqa: E402

PROGRAMS = frozenset("""
    git gh python pip pytest ruff mypy uv node npm npx pnpm yarn tsc eslint playwright deno bun
    cargo rustc go java mvn gradle dotnet make cmake docker kubectl
    bash sh zsh powershell cmd ssh scp rsync curl wget ping netstat nc openssl tailscale
    ls cat head tail grep rg find sed awk sort uniq wc cut tr xargs diff cmp tee touch mkdir rmdir
    rm cp mv ln chmod stat du df tar zip unzip 7z jq yq sqlite3 psql ffmpeg magick which where file
    date basename dirname realpath readlink mktemp od xxd base64 sha256sum md5sum seq kill ps taskkill
    tasklist schtasks sc reg icacls robocopy findstr winget choco code claude codex pi copilot
    sleep test [ [[ cd pushd popd echo printf export set source
    get-childitem get-content set-content add-content out-file select-string select-object
    where-object foreach-object test-path get-item new-item remove-item copy-item move-item
    rename-item resolve-path join-path split-path get-process stop-process start-process
    get-service get-ciminstance get-wmiobject get-nettcpconnection get-winevent add-type
    invoke-webrequest invoke-restmethod invoke-expression convertfrom-json convertto-json
    measure-object sort-object group-object format-table format-list get-command get-date
    get-filehash get-scheduledtask test-netconnection start-sleep set-location write-output write-host
    tee-object
""".split())  # sorted by family, not by name: shells and tools first, then PowerShell cmdlets
ALIASES = {"py": "python", "pip3": "pip", "pwsh": "powershell", "chdir": "cd", "sl": "cd"}
#: Commands that only prepare or report; named only when nothing else runs.
_SETUP = _lib.CD_VERBS | _lib.PUSH_VERBS | _lib.POP_VERBS | {
    "export", "set", "unset", "source", ".", "echo", "printf", "write-output", "write-host",
    "true", ":", "sleep", "start-sleep", "test", "[", "[[", "test-path", "exit", "return", "wait", "break",
    "continue",
}
#: After a pipe these only reshape output and do not decide the line's status.
_FILTERS = {
    "head", "tail", "tee", "sort", "uniq", "wc", "cut", "tr", "cat", "select-object", "where-object",
    "foreach-object", "sort-object", "group-object", "measure-object", "format-table", "format-list",
    "tee-object", "out-string", "out-null", "out-file", "convertfrom-json", "convertto-json",
}
#: Shell syntax that heads a stage without running anything.
_SYNTAX = {"for", "foreach", "case", "switch", "try", "catch", "finally", "done", "fi", "esac", "in"}
_WRAPPERS = _lib.COMMAND_PREFIX_WORDS | {"timeout", "stdbuf"}
#: Wrapper options whose value is the next word, so it is not read as the program.
_VALUE_OPTS = {
    "timeout": {"-k", "-s", "--kill-after", "--signal"},
    "env": {"-u", "-C", "--unset", "--chdir"},
    "nice": {"-n"},
    "sudo": {"-u"},
    "xargs": {"-I", "-n", "-P", "-d"},
}
_SCRIPT_FLAGS = {"-c", "-lc", "-command", "/c", "//c"}
_PS_OPERATORS = {"-eq", "-ne", "-like", "-notlike", "-match", "-notmatch", "-and", "-or", "-gt", "-lt",
                 "-ge", "-le", "-contains", "-in", "-replace", "-split", "-join", "+", "-"}
_SCRIPT_SUFFIXES = (".sh", ".ps1", ".py", ".js", ".mjs", ".cjs", ".bat")
_ASSIGN = re.compile(r"\$?(?:env:)?([A-Za-z_]\w*)\+?=(.*)", re.S)
_VARIABLE = re.compile(r"\$(?:env:)?([A-Za-z_]\w*)")
#: A name holding none of these is a number, an operator or other data, never a program.
_NAMEABLE = re.compile(r"[A-Za-z\[.:]")
_DURATION = re.compile(r"\d+(?:\.\d+)?[smhd]?")
#: `2>&1` and `&>` hold an `&` that separates nothing.
_REDIRECT_AMP = re.compile(r"\d*>&\d*|&>>?")
_COMMENT_LINE = re.compile(r"^\s*#.*$", re.M)
#: `${F}` and `${F%.md}` read as `$F`, so their braces split nothing.
_BRACED_VAR = re.compile(r"\$\{(\w+)[^{}]*\}")


def program(command: Any) -> str:
    """The program `command` (a shell string, or an argv list) runs, or empty when none is read."""
    if isinstance(command, list):
        words = [str(w) for w in command]
        script = _nested_script(words)
        command = script if script is not None else " ".join(
            f'"{w}"' if re.search(r"[\s;&|(){}]", w) else w for w in words)
    return _read(command, nested=False) if isinstance(command, str) else ""


def _read(command: str, nested: bool) -> str:
    text = _lib._PS_HERE_STRING_RE.sub("''", _lib.strip_heredoc_bodies(command.replace("\r\n", "\n")))
    text = _COMMENT_LINE.sub("", text.replace("\\\n", " "))
    text = _BRACED_VAR.sub(r"$\1", _REDIRECT_AMP.sub(" ", text))
    named = setup = unread = ""
    variables: Dict[str, str] = {}
    for pipeline in _pipelines(text):
        decided = ""
        for position, stage in enumerate(pipeline):
            words = _expand(_command_words(stage, variables), variables)
            if not words:
                continue
            if _VARIABLE.fullmatch(words[0].strip("\"'")):
                unread = "<other>"
                continue
            name = _name(words[0])
            if name in _SETUP:
                setup = ALIASES.get(name, name)
            elif _NAMEABLE.search(name) and not (position and name in _FILTERS):
                decided = _shown(name, words, nested)
        named = decided or named
    return named or unread or (setup if setup in PROGRAMS else "")


def _pipelines(text: str) -> List[List[List[str]]]:
    """The command line as pipelines of stages of words, in order."""
    pipelines: List[List[List[str]]] = [[[]]]
    for token in _lib._COMMIT_TOKEN_RE.findall(text):
        if token == "|":
            pipelines[-1].append([])
        elif token in _lib._COMMIT_SEPARATORS:
            pipelines.append([[]])
        else:
            pipelines[-1][-1].append(token)
    return pipelines


def _shown(name: str, words: List[str], nested: bool) -> str:
    """The safe word for one command, reading once into a nested shell's script."""
    if name in _lib.NESTED_SHELLS and not nested:
        script = _nested_script(words)
        inner = _read(script, nested=True) if script is not None else ""
        if inner and inner != "<other>":
            return inner
    return name if name in PROGRAMS else "<script>" if name.endswith(_SCRIPT_SUFFIXES) else "<other>"


def _expand(words: List[str], variables: Dict[str, str]) -> List[str]:
    """`$PY x.py` as the command the same line assigned to `PY`; other words unchanged."""
    found = _VARIABLE.fullmatch(words[0].strip("\"'")) if words else None
    value = variables.get(found.group(1).lower()) if found else None
    if value is None:
        return words
    return _command_words([*_lib._COMMIT_TOKEN_RE.findall(value), *words[1:]], variables)


def _command_words(words: List[str], variables: Dict[str, str]) -> List[str]:
    """One stage from its command word on, noting its assignments; empty when it runs no command."""
    wrapper: Optional[str] = None
    i = 0
    while i < len(words):
        word = words[i]
        assigned = _ASSIGN.match(word)
        stored = _VARIABLE.fullmatch(word) if words[i + 1:i + 2] == ["="] else None
        if wrapper and word.startswith("-"):
            i += 2 if word in _VALUE_OPTS.get(wrapper, ()) else 1
        elif assigned:
            if assigned.group(2):
                variables[assigned.group(1).lower()] = assigned.group(2).strip("\"'")
            i += 1
        elif wrapper and _DURATION.fullmatch(word):
            i += 1
        elif word.lower() in _WRAPPERS:
            wrapper = word.lower()
            i += 1
        elif stored:
            # PowerShell `$x = git status` runs git; `$x = '1'` only stores a value.
            value = words[i + 2] if words[i + 2:i + 3] else ""
            if value[:1] in ("'", '"', "$", "@", "[") or value[:1].isdigit():
                variables[stored.group(1).lower()] = value.strip("\"'")
                return []
            i += 2
        else:
            break
    rest = words[i:]
    if not rest or rest[0].lower() in _SYNTAX or rest[1:2] == ["in"]:
        return []
    return [] if _is_value(rest, variables) else rest


def _is_value(rest: List[str], variables: Dict[str, str]) -> bool:
    """True for a stage that is data: a lone string, a member access, or an expression on a variable."""
    head = rest[0]
    if re.fullmatch(r"\.[A-Za-z]\w*", head):  # `(Get-Content x).Count`
        return True
    if head.startswith("$_") or (head.startswith("$") and rest[1:2] and rest[1] in _PS_OPERATORS):
        return True
    if len(rest) > 1 or not head.startswith(("$", "'", '"')):
        return False
    found = _VARIABLE.fullmatch(head)
    if found:
        return found.group(1).lower() not in variables
    # A quoted path to something runnable runs even with no argument.
    return not re.search(r"\.(?:exe|cmd|bat|ps1|sh)[\"']?$", head, re.I)


def _name(word: str) -> str:
    """The bare, lowercased program name of a command word."""
    name = word.strip("\"'`").replace("\\", "/").rsplit("/", 1)[-1].lower()
    name = re.sub(r"\.(?:exe|cmd)$", "", name)
    if re.fullmatch(r"python\d+(?:\.\d+)?", name):
        return "python"
    return ALIASES.get(name, name)


def _nested_script(words: List[str]) -> Optional[str]:
    """The command line a `bash -c` or `powershell -Command` runs, or None for any other command."""
    if not words or _name(words[0]) not in _lib.NESTED_SHELLS:
        return None
    for i, word in enumerate(words[1:-1], start=1):
        if word.lower() in _SCRIPT_FLAGS:
            script = words[i + 1]
            quoted = len(script) >= 2 and script[0] == script[-1] and script[0] in "'\""
            return script[1:-1] if quoted else script
    return None
