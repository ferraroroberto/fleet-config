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

Allow-listed (passes through):
  * Port-scoped kills against ports NOT in `never_kill_ports` —
    `Get-NetTCPConnection -LocalPort 8445 | ... Stop-Process` works fine.
  * `git push --force` to a feature branch (not main/master) — including one
    whose *name contains* `main`/`master`, e.g. `chore/rename-main-config-loader`.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

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
GIT_BYPASS_PATTERNS = (
    r"\bgit\b[^\n]*\s--no-verify\b",
    r"\bgit\b[^\n]*\s--no-gpg-sign\b",
    r"\bgit\b[^\n]*\s-c\s+commit\.gpgsign=false\b",
)

# ----- git force-push to main/master -----
# The predicate is the *ref actually being pushed*, never a `main`/`master` word
# anywhere on the line. The old lookahead — `(?=[^\n;|&]*\b(?:main|master)\b)` —
# was a word-boundary search over the whole command, so it refused
# `git push --force origin chore/rename-main-config-loader`: a legitimate
# feature-branch force-push this module's own docstring promises to allow
# (fleet-config#562). A fleet-wide guard that blocks valid work is the expensive
# kind of wrong — #464/#472 reverted a hook within the hour for exactly that.
GIT_PUSH_RE = re.compile(r"\bgit\s+push\b", re.IGNORECASE)
# `--force`, `--force-with-lease[=ref]`, and short-flag clusters carrying `f`
# (`-f`, `-fu`, `-uf`). Anchored to a token start so `--foo` can't match.
FORCE_FLAG_RE = re.compile(
    r"(?:^|\s)(?:--force(?:-with-lease)?(?:=\S*)?|-[a-z]*f[a-z]*)(?=\s|$)",
    re.IGNORECASE,
)
PROTECTED_BRANCHES = {"main", "master"}


def destination_branch(refspec: str) -> str:
    """The branch a refspec writes to: `HEAD:main` → `main`, `+refs/heads/main`
    → `main`, `feature/x` → `feature/x`. The destination is the part after the
    last `:` (a refspec is `<src>:<dst>`), minus the force-`+` and the
    `refs/heads/` prefix."""
    dest = refspec.rsplit(":", 1)[-1].lstrip("+")
    prefix = "refs/heads/"
    return dest[len(prefix):] if dest.startswith(prefix) else dest


def forced_push_refspecs(cmd: str) -> Optional[list[str]]:
    """Refspecs of a forced `git push`, or ``None`` when `cmd` isn't one.

    An **empty list** means the push named no refspec (`git push --force`,
    `git push -f origin`), so the destination is whatever branch is checked
    out — the caller resolves that separately rather than guessing.
    """
    for segment in re.split(r"[\n;|&]+", cmd):
        if not GIT_PUSH_RE.search(segment) or not FORCE_FLAG_RE.search(segment):
            continue
        tokens = segment.split()
        for i, token in enumerate(tokens):
            if token.lower() == "push":
                # First positional after `push` is the remote; the rest are refspecs.
                positional = [t for t in tokens[i + 1:] if not t.startswith("-")]
                return positional[1:]
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
    refspecs = forced_push_refspecs(cmd)
    if refspecs is None:
        return False
    targets = [destination_branch(r) for r in refspecs] or [_current_branch(cwd_path)]
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
    # get refused as though it targeted that port (fleet-config#709).
    for segment in STATEMENT_SPLIT_RE.split(cmd):
        if re.search(r"\bStop-Process\b", segment, re.IGNORECASE):
            ports.extend(int(p) for p in NETSTAT_PORT_RE.findall(segment))

    return ports


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
    #    an over-eager block on an echoed kill string (fleet-config#491).
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
    for pattern in GIT_BYPASS_PATTERNS:
        if re.search(pattern, cmd, re.IGNORECASE):
            _lib.block(
                "Blocked: git safety bypass flag detected (matched: " + pattern + "). "
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

    _lib.allow()


if __name__ == "__main__":
    main()
