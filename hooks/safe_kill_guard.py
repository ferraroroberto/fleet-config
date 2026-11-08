"""Block dangerous kill / push / commit-bypass patterns.

Triggers on `PreToolUse` for `Bash` and `PowerShell`. Blocks:

  * Blanket `python(w?)` kills that would nuke unrelated sister hubs:
      - `Stop-Process -Name python` / `pythonw`
      - `taskkill /IM python.exe` / `pythonw.exe`
      - `Get-Process python* | Stop-Process`
      - `pkill -f python` / `killall python`

  * Port-scoped kills targeting a port in `[global].never_kill_ports`
    (sister hubs like :8000 LLM hub, :8090 whisper, :8446 session-host).

  * `git push --force[-with-lease]` (or the short `-f`) to `main` or `master` —
    decided from the *refspec being pushed* (`HEAD:main`, `+refs/heads/master`,
    …), or from the checked-out branch when the push names none.

  * Git safety bypass flags: `--no-verify`, `--no-gpg-sign`,
    `-c commit.gpgsign=false`.

  * Taking the host down: `shutdown` with `/s /r /h /p /g`, `Stop-Computer`,
    `Restart-Computer`, `rundll32 powrprof.dll,…`, `psshutdown`, `wmic os call
    Win32Shutdown`/`Invoke-CimMethod … Shutdown`, and `powercfg` changes to the
    button or sleep actions — also launched through `Start-Process` or a nested
    shell. A target that is clearly another machine passes.

Allow-listed (passes through):
  * Port-scoped kills against ports NOT in `never_kill_ports` —
    `Get-NetTCPConnection -LocalPort 8445 | ... Stop-Process` works fine.
  * `git push --force` to a feature branch (not main/master) — including one
    whose *name contains* `main`/`master`, e.g. `chore/rename-main-config-loader`.
  * A search, commit message, `echo` or `Get-Help` that only names a power
    command: the power rule reads commands in command position only.
"""

from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib  # noqa: E402

# Blanket-python-kill patterns, split by which shell can actually execute them.
# A `Stop-Process` literal inside a Bash `echo '...'` is a string, not a kill -
# only flag PowerShell patterns when the tool is PowerShell, and vice-versa for
# Bash patterns. `taskkill` is valid in either shell.
POWERSHELL_BLANKET_KILL = (
    r"\bStop-Process\b[^\n|;]*-Name\s+['\"]?pythonw?['\"]?(?!\.exe)",
    r"\bStop-Process\b[^\n|;]*-Name\s+['\"]?python['\"]?\b",
    r"\bGet-Process\b[^\n|;]*\bpython[w\*]*\b[^\n]*\|\s*Stop-Process",
)
BASH_BLANKET_KILL = (
    r"\bpkill\b[^\n]*\bpython(?:3|w)?\b",
    r"\bkillall\b[^\n]*\bpython(?:3|w)?\b",
)
COMMON_BLANKET_KILL = (
    r"\btaskkill\b[^\n]*\s/IM\s+pythonw?\.exe",
)

# ----- git safety bypasses -----
# Flagged only as arguments of a real `git` invocation at command position, so a
# read-only search that merely *mentions* a flag is not refused.
# The old check was a line regex that matched `git` and the flag anywhere on the line.
GIT_BYPASS_FLAGS = ("--no-verify", "--no-gpg-sign")
GIT_BYPASS_CONFIG = "commit.gpgsign=false"

# ----- git force-push to main/master -----
# The predicate is the *ref actually being pushed*, never a `main`/`master` word
# anywhere on the line. The old lookahead — `(?=[^\n;|&]*\b(?:main|master)\b)` —
# was a word-boundary search over the whole command, so it refused
# `git push --force origin chore/rename-main-config-loader`: a legitimate
# feature-branch force-push this module's own docstring promises to allow.
# A fleet-wide guard that blocks valid work is the expensive
# kind of wrong — an earlier over-broad force-push pattern was reverted within the hour for exactly that.
# Git's global options may sit between `git` and the subcommand
# (`git -C <dir> push …`), so a push is found by walking those
# options token by token (`_lib.GIT_GLOBAL_OPTS_WITH_VALUE`), not by a
# `git\s+push` adjacency regex.
# `--force`, `--force-with-lease[=ref]`, and short-flag clusters carrying `f`
# (`-f`, `-fu`, `-uf`). Anchored to a token start so `--foo` can't match.
FORCE_FLAG_RE = re.compile(
    r"(?:^|\s)(?:--force(?:-with-lease)?(?:=\S*)?|-[a-z]*f[a-z]*)(?=\s|$)",
    re.IGNORECASE,
)
PROTECTED_BRANCHES = {"main", "master"}

_ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def _split_segments(cmd: str) -> list[str]:
    """Split on `; | & newline` outside quotes, so a quoted `;` stays in its string."""
    segments: list[str] = []
    buf: list[str] = []
    quote = ""
    for ch in cmd:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
            buf.append(ch)
        elif ch in ";|&\n":
            segments.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    segments.append("".join(buf))
    return [seg for seg in segments if seg.strip()]


def _command_index(tokens: list[str]) -> Optional[int]:
    """Index of the token that is the command word of a segment, skipping leading
    `VAR=value` assignments, subshell/`$(` punctuation and `_lib.COMMAND_PREFIX_WORDS`."""
    skipping_options = False
    for i, raw in enumerate(tokens):
        token = raw.lstrip("({`$").strip("\"'")
        if not token:
            continue
        if _ENV_ASSIGN_RE.match(token):
            continue
        if skipping_options and token.startswith("-"):
            continue
        if token.lower() in _lib.COMMAND_PREFIX_WORDS:
            skipping_options = True
            continue
        return i
    return None


def _git_bypass_in_tokens(tokens: list[str], start: int) -> Optional[str]:
    """The bypass flag among the arguments of the `git` at `tokens[start]`, if any."""
    parsed = _lib.git_globals(tokens, start + 1)
    if any(value.lower() == GIT_BYPASS_CONFIG for value in parsed.config):
        return "-c " + GIT_BYPASS_CONFIG
    for j in range(parsed.subcommand + 1, len(tokens)):
        low = tokens[j].lower()
        if low in GIT_BYPASS_FLAGS:
            return low
        if low == "-c" and j + 1 < len(tokens) and tokens[j + 1].lower() == GIT_BYPASS_CONFIG:
            return "-c " + GIT_BYPASS_CONFIG
    return None


def git_bypass_flag(cmd: str, _nested: bool = False) -> Optional[str]:
    """The git safety-bypass flag `cmd` passes to a real `git` invocation, or ``None``.

    Walks each shell segment like `_push_args` does — global options before the
    subcommand don't hide it — but only a `git` in command position counts.
    The one place a quoted string *is* a command is a nested shell
    (`bash -c "git …"`), so those arguments are re-checked once.
    """
    for segment in _split_segments(cmd):
        raw = _lib.shell_tokens(segment)
        tokens = [t.strip("\"'") for t in raw]
        at = _command_index(raw)
        if at is None:
            continue
        word = tokens[at].lstrip("({`$")
        if _lib.GIT_TOKEN_RE.search(word):
            hit = _git_bypass_in_tokens(tokens, at)
            if hit:
                return hit
        elif not _nested and Path(word).stem.lower() in _lib.NESTED_SHELLS:
            for r, t in zip(raw[at + 1:], tokens[at + 1:]):
                if r[:1] in "\"'" and (hit := git_bypass_flag(t, _nested=True)):
                    return hit
    return None



def destination_branch(refspec: str) -> str:
    """The branch a refspec writes to: `HEAD:main` → `main`, `+refs/heads/main`
    → `main`, `feature/x` → `feature/x`. The destination is the part after the
    last `:` (a refspec is `<src>:<dst>`), minus the force-`+` and the
    `refs/heads/` prefix."""
    dest = refspec.rsplit(":", 1)[-1].lstrip("+")
    prefix = "refs/heads/"
    return dest[len(prefix):] if dest.startswith(prefix) else dest


def _push_args(segment: str) -> Optional[tuple[list[str], Optional[str]]]:
    """For a `git [global options] push …` segment: the tokens after `push` and
    the directory its global options name (``None`` when absent; several `-C`s
    compose, as in git). ``None`` when the segment isn't a push."""
    tokens = [t.strip("\"'") for t in _lib.shell_tokens(segment)]
    for start, token in enumerate(tokens):
        if not _lib.GIT_TOKEN_RE.search(token):
            continue
        parsed = _lib.git_globals(tokens, start + 1)
        i = parsed.subcommand
        if i < len(tokens) and tokens[i].lower() == "push":
            return tokens[i + 1:], parsed.workdir or None
    return None


def _parse_forced_push(cmd: str) -> Optional[tuple[list[str], Optional[str]]]:
    """`(refspecs, -C dir)` of a forced `git push`, or ``None`` when `cmd` isn't one.

    An **empty refspec list** means the push named no refspec (`git push --force`,
    `git push -f origin`), so the destination is whatever branch is checked
    out — the caller resolves that separately rather than guessing.

    With no force flag, a refspec with a leading `+` is still forced, for that
    refspec alone (`git push origin +HEAD:main`), so only those refspecs are
    returned. Git's global options before `push` (`-C <dir>`, `-c k=v`,
    `--git-dir`, `--work-tree`) don't hide it.
    """
    for segment in re.split(r"[\n;|&]+", cmd):
        parsed = _push_args(segment)
        if parsed is None:
            continue
        args, git_dir = parsed
        # First positional after `push` is the remote; the rest are refspecs.
        positional = [t for t in args if not t.startswith("-")]
        if FORCE_FLAG_RE.search(" ".join(args)):
            return positional[1:], git_dir
        plus = [r for r in positional[1:] if r.startswith("+")]
        if plus:
            return plus, git_dir
    return None


def _current_branch(cwd_path: Path) -> str:
    """The checked-out branch of `cwd_path`, or `""` when it can't be resolved.

    Only consulted for a forced push that names no refspec. Unresolvable is
    reported as unresolvable (empty string) — the caller then allows, the same
    fail-open every guard in this directory takes when it cannot establish a
    fact, rather than blocking on a guess.

    `symbolic-ref --short HEAD`, not `rev-parse --abbrev-ref HEAD`: it answers
    on an unborn branch (a fresh `git init` before the first commit, where
    rev-parse errors), and it reports *no branch* rather than the literal
    string `HEAD` when the tree is detached.
    """
    try:
        res = _lib.run_git(["-C", str(cwd_path), "symbolic-ref", "--short", "HEAD"], timeout=5)
    except (OSError, subprocess.SubprocessError):
        return ""
    return (res.stdout or "").strip() if res.returncode == 0 else ""


def forced_push_hits_protected(cmd: str, cwd_path: Path) -> bool:
    """True when `cmd` force-pushes to `main`/`master`."""
    parsed = _parse_forced_push(cmd)
    if parsed is None:
        return False
    refspecs, git_dir = parsed
    # A refspec-less push runs in the repo `-C` names, relative to the payload cwd.
    repo = cwd_path / _lib.msys_to_drive(git_dir) if git_dir else cwd_path
    targets = [destination_branch(r) for r in refspecs] or [_current_branch(repo)]
    return any(t.lower() in PROTECTED_BRANCHES for t in targets if t)


# ----- port-scoped kills (used to match `LocalPort N`) -----
LOCALPORT_RE = re.compile(r"-LocalPort\s+(\d+)", re.IGNORECASE)
NETSTAT_PORT_RE = re.compile(r":(\d{2,5})\b")

# Statement separators only — never `|`, which chains a single logical
# PowerShell pipeline (`Get-NetTCPConnection -LocalPort N | ... | Stop-Process`
# is one command whose `-LocalPort` clause and `Stop-Process` clause
# legitimately live in different pipe segments; heuristic 1 relies on that).
STATEMENT_SPLIT_RE = re.compile(r"[\n;]+|&&|\|\|")


def _scan_port_kills(cmd: str) -> list[int]:
    """Return the list of ports a port-scoped kill is targeting (heuristic)."""
    ports: list[int] = []

    # Heuristic 1: PowerShell `-LocalPort N` clauses anywhere a kill is present.
    if re.search(r"\bStop-Process\b", cmd, re.IGNORECASE) or re.search(r"\bkill\b", cmd, re.IGNORECASE):
        ports.extend(int(p) for p in LOCALPORT_RE.findall(cmd))

    # Heuristic 2: a netstat-y `:PORT` reference in the SAME statement as the
    # kill — not just anywhere on the line. Whole-command matching let a
    # PID-scoped kill that merely *mentions* a protected port in a later
    # clause ("Stop-Process -Id 1234 -Force; curl http://127.0.0.1:8446/")
    # get refused as though it targeted that port.
    for segment in STATEMENT_SPLIT_RE.split(cmd):
        if re.search(r"\bStop-Process\b", segment, re.IGNORECASE):
            ports.extend(int(p) for p in NETSTAT_PORT_RE.findall(segment))

    return ports


# ----- commands in command position -----
# A line continuation, Bash `\` or PowerShell backtick before the newline: the
# next line's words still belong to the same command.
_LINE_CONTINUATION_RE = re.compile(r"[\\`]\r?\n")
_EVAL_SHELLS = {"eval", "iex", "invoke-expression"}
# After one of these, a nested shell's remaining words are the command it runs.
_NESTED_COMMAND_FLAGS = {"-c", "/c", "//c", "/k", "//k", "-command", "-commandwithargs"}
# Two levels covers `powershell -Command "cmd /c '…'"`, and stops a crafted
# command from recursing without end.
_NESTED_DEPTH = 2


def command_stem(word: str) -> str:
    """The lowercased program name a command word runs: `C:/Windows/System32/shutdown.exe` → `shutdown`."""
    name = re.split(r"[\\/]", word)[-1].lower()
    return name[:-4] if name.endswith(".exe") else name


def _unquote_word(token: str) -> str:
    token = token.strip("`")
    return _lib._QUOTED_RUN_RE.sub(lambda m: m.group(1) if m.group(1) is not None else m.group(2), token)


def command_invocations(cmd: str, _depth: int = 0) -> list[list[str]]:
    """Every command `cmd` runs, each as its unquoted words, command word first.

    The same reading as `_lib.runs_git_commit`, on its tokenizer: heredoc and here-string bodies are
    data, a quoted string is one word, and leading `VAR=value` assignments and
    `_lib.COMMAND_PREFIX_WORDS` (with their options) are skipped so the command after
    them leads. Braces split commands too, so a PowerShell script block's first
    word is in command position. A nested shell's command line (`bash -c "…"`,
    `cmd //c …`, `iex '…'`) is read as well.
    """
    text = _LINE_CONTINUATION_RE.sub(" ", _lib._PS_HERE_STRING_RE.sub("''", _lib.strip_heredoc_bodies(cmd)))
    found: list[list[str]] = []
    raw: list[str] = []
    after_prefix = False
    for token in [*_lib._COMMIT_TOKEN_RE.findall(text), "\n"]:
        if token in _lib._COMMIT_SEPARATORS:
            if raw:
                words = [_unquote_word(t) for t in raw]
                found.append(words)
                if _depth < _NESTED_DEPTH:
                    for line in _nested_command_lines(raw, words):
                        found.extend(command_invocations(line, _depth + 1))
            raw, after_prefix = [], False
            continue
        if not raw:
            if _lib._COMMIT_ENV_ASSIGN_RE.match(token):
                continue
            if token in _lib.COMMAND_PREFIX_WORDS:
                after_prefix = True
                continue
            if after_prefix and token.startswith("-"):
                continue
        raw.append(token)
    return found


def _nested_command_lines(raw: list[str], words: list[str]) -> list[str]:
    """The command lines a nested shell invocation runs: its quoted arguments,
    and everything after its command flag (`cmd //c shutdown …` is unquoted)."""
    stem = command_stem(words[0])
    if stem not in _lib.NESTED_SHELLS:
        return []
    lines = [word for token, word in zip(raw[1:], words[1:]) if token[:1] in "\"'"]
    rest: list[str] = []
    if stem in _EVAL_SHELLS:
        rest = raw[1:]
    else:
        for i, word in enumerate(words[1:], start=1):
            if word.lower() in _NESTED_COMMAND_FLAGS:
                rest = raw[i + 1:]
                break
    # One quoted word is already in `lines`; joined, it would read as one word.
    if rest and not (len(rest) == 1 and rest[0][:1] in "\"'"):
        lines.append(" ".join(rest))
    return lines


# ----- host power: off, restart, sleep, hibernate -----
# Unattended agent lanes run on this host; one that takes it down stops every
# other lane until someone is back at the machine. Each command is read in
# command position (`command_invocations`), so a search, a commit message
# or `Get-Help Stop-Computer` that only names one of these is not refused.
SHUTDOWN_ACTION_FLAGS = {"s", "r", "h", "p", "g", "sg"}
POWER_CMDLETS = {"stop-computer", "restart-computer"}
PSSHUTDOWN = {"psshutdown", "psshutdown64"}
WMI_POWER_METHODS = {"shutdown", "reboot", "win32shutdown", "win32shutdowntracker"}
CIM_CMDLETS = {"invoke-cimmethod", "invoke-wmimethod"}
# `powercfg /setacvalueindex <scheme> <subgroup> …`: the button and sleep
# subgroups, by alias and by GUID.
POWERCFG_SLEEP_SUBGROUPS = {
    "sub_buttons", "4f971e89-eebd-4455-a8de-9e59040e7347",
    "sub_sleep", "238c9fa8-0aad-41ed-83f4-97be242c8f20",
}
POWER_PROGRAMS = {"shutdown", "rundll32", "wmic", "powercfg"} | POWER_CMDLETS | PSSHUTDOWN | CIM_CMDLETS
LAUNCHERS = {"start-process", "saps", "start"}
# `Start-Process` parameter names, dropped before its target's own arguments
# are read, so `-ArgumentList` is not taken for a `shutdown` flag.
LAUNCHER_PARAMS = {
    "-filepath", "-argumentlist", "-args", "-wait", "-nonewwindow", "-passthru",
    "-windowstyle", "-verb", "-workingdirectory", "-credential", "-loaduserprofile",
}
_FLAG_RE = re.compile(r"(?:/{1,2}|-)([a-z]+)")


def _flag(word: str) -> Optional[str]:
    """`/s`, `//s` (Git Bash) or `-s` → `s`; anything else → None."""
    match = _FLAG_RE.fullmatch(word.lower())
    return match.group(1) if match else None


def _is_remote(target: str) -> bool:
    """True only for a literal machine name that is not this host. A variable
    or expression (`$env:COMPUTERNAME`, `$_`) is not clearly remote."""
    name = target.strip().strip("\\/").lower()
    if not name or name[0] in "$(@":
        return False
    own = {socket.gethostname().lower(), os.environ.get("COMPUTERNAME", "").lower()} - {""}
    return name not in ({".", "localhost", "127.0.0.1", "::1"} | own) and name.split(".")[0] not in own


def _all_remote(targets: list[str]) -> bool:
    return bool(targets) and all(_is_remote(t) for t in targets)


def _param_values(args: list[str], spells: Callable[[str], bool]) -> list[str]:
    """Comma-split values of the PowerShell parameter whose name `spells(name)`
    accepts, written `-Name value` or `-Name:value`."""
    values: list[str] = []
    for i, arg in enumerate(args):
        name, sep, inline = arg.partition(":")
        if not name.startswith("-") or not spells(name[1:].lower()):
            continue
        value = inline if sep else (args[i + 1] if i + 1 < len(args) else "")
        values.extend(v for v in value.split(",") if v.strip())
    return values


def _is_computername(name: str) -> bool:
    return name == "cn" or (len(name) >= 4 and "computername".startswith(name))


def _is_methodname(name: str) -> bool:
    return name in {"name", "mn"} or (len(name) >= 2 and "methodname".startswith(name))


def _powercfg_changes_sleep(args: list[str]) -> bool:
    low = [a.lower() for a in args]
    verb = next((f for f in map(_flag, low) if f), None)
    if verb in {"setacvalueindex", "setdcvalueindex"}:
        return any(a in POWERCFG_SLEEP_SUBGROUPS for a in low)
    if verb in {"change", "x"}:
        return any(a.lstrip("-/").startswith(("standby-timeout", "hibernate-timeout")) for a in low)
    return verb in {"hibernate", "h"}


def _power_hit(words: list[str]) -> Optional[str]:
    """The power command `words` runs against this host, or None."""
    stem = command_stem(words[0])
    args = words[1:]
    low = [a.lower() for a in args]
    flags = [_flag(a) for a in low]
    if stem == "shutdown":
        target = [low[i + 1] for i, f in enumerate(flags[:-1]) if f == "m"]
        hit = SHUTDOWN_ACTION_FLAGS.intersection(flags) and not _all_remote(target)
    elif stem in POWER_CMDLETS:
        hit = not _all_remote(_param_values(args, _is_computername))
    elif stem == "rundll32":
        hit = any("powrprof" in a for a in low)
    elif stem in PSSHUTDOWN:
        hit = "a" not in flags and not _all_remote([a for a in args if a.startswith("\\\\")])
    elif stem == "wmic":
        nodes = [a.split(":", 1)[1] for a in low if a.startswith(("/node:", "-node:"))]
        on_os = "os" in low or any("win32_operatingsystem" in a for a in low)
        hit = on_os and WMI_POWER_METHODS.intersection(low) and not _all_remote(nodes)
    elif stem in CIM_CMDLETS:
        methods = {m.lower() for m in _param_values(args, _is_methodname)}
        hit = WMI_POWER_METHODS & methods and not _all_remote(_param_values(args, _is_computername))
    elif stem == "powercfg":
        hit = _powercfg_changes_sleep(args)
    else:
        hit = False
    return words[0] if hit else None


def _launched_power_hit(words: list[str]) -> Optional[str]:
    """The power command a `Start-Process`/`start` invocation launches, or None.
    `-ArgumentList '/s','/t','0'` arrives as one word, so it is split here."""
    pieces = [p for w in words[1:] for p in re.split(r"[\s,]+", w) if p and p.lower() not in LAUNCHER_PARAMS]
    for i, piece in enumerate(pieces):
        if command_stem(piece) in POWER_PROGRAMS:
            return _power_hit(pieces[i:])
    return None


def host_power_command(cmd: str) -> Optional[str]:
    """The command in `cmd` that would power off, restart, sleep or hibernate
    this host, or None."""
    for words in command_invocations(cmd):
        if command_stem(words[0]) in LAUNCHERS:
            hit = _launched_power_hit(words)
        else:
            hit = _power_hit(words)
        if hit:
            return hit
    return None


def main() -> None:
    payload = _lib.read_stdin_json()
    if _lib.tool_name(payload) not in {"Bash", "PowerShell"}:
        _lib.allow()

    cmd = _lib.command_string(payload)
    if not cmd:
        _lib.allow()

    # 1) Blanket python kills - dispatch by shell so an `echo` of a kill string
    #    in the other shell doesn't false-positive. A harness with a single,
    #    shell-agnostic terminal tool (e.g. Grok or Codex) gets *both* sets: we cannot observe
    #    which shell it will use, and a missed blanket kill costs far more than
    #    an over-eager block on an echoed kill string.
    tn = _lib.tool_name(payload)
    ambiguous = _lib.shell_is_ambiguous(payload)
    shell_note = " Shell unknown; checked PowerShell and Bash safety rules." if ambiguous else ""
    if ambiguous:
        _lib.logger.info("safe_kill_guard: shell unknown; applying both shell rule sets")
    patterns: list[str] = list(COMMON_BLANKET_KILL)
    if tn == "PowerShell" or ambiguous:
        patterns.extend(POWERSHELL_BLANKET_KILL)
    if tn == "Bash" or ambiguous:
        patterns.extend(BASH_BLANKET_KILL)

    for pattern in patterns:
        if re.search(pattern, cmd, re.IGNORECASE):
            _lib.block(
                "Blocked: blanket python(w?) kill detected (matched: " + pattern + "). "
                "This would also kill sister hubs (local-llm-hub :8000, whisper :8090, "
                "session-host :8446). Use port-scoped kill instead: "
                "`Get-NetTCPConnection -LocalPort <PORT> | Select -ExpandProperty OwningProcess | "
                "ForEach-Object { Stop-Process -Id $_ -Force }`." + shell_note
            )

    # 2) Git bypass flags
    bypass = git_bypass_flag(cmd)
    if bypass:
        _lib.block(
            "Blocked: git safety bypass flag detected (matched: " + bypass + "). "
            "The user has not authorized `--no-verify` / `--no-gpg-sign`. "
            "Fix the underlying hook/signing problem instead of skipping it."
        )

    # 3) Force push to main/master
    if forced_push_hits_protected(cmd, _lib.cwd(payload)):
        _lib.block(
            "Blocked: `git push --force` targeting main/master. "
            "Force-pushing to a protected branch is destructive. "
            "If you really mean to do this, ask the user first."
        )

    # 4) Port-scoped kills against protected ports - PowerShell-only patterns
    targeted = _scan_port_kills(cmd) if (tn == "PowerShell" or ambiguous) else []
    if targeted:
        reg = _lib.load_registry()
        forbidden = set(reg.globals.never_kill_ports)
        hits = sorted({p for p in targeted if p in forbidden})
        if hits:
            _lib.block(
                "Blocked: kill targets a protected port "
                + ", ".join(str(p) for p in hits)
                + " (sister hub — listed in projects.toml [global].never_kill_ports). "
                "Killing it would break unrelated apps." + shell_note
            )

    # 5) Powering off, restarting, sleeping or hibernating this host
    power = host_power_command(cmd)
    if power:
        _lib.block(
            "Blocked: `" + power + "` would power off, restart, sleep or hibernate this host. "
            "Unattended agent lanes run here, so an agent never takes the host down; "
            "ask the user to run it themselves. Naming the command in a search, "
            "message or `Get-Help` is fine; only running it is refused."
        )

    _lib.allow()


if __name__ == "__main__":
    main()
