"""Block dangerous kill / push / commit-bypass patterns.

Triggers on `PreToolUse` for `Bash` and `PowerShell`. Blocks:

  * Blanket `python(w?)` kills that would nuke unrelated sister hubs:
      - `Stop-Process -Name python` / `pythonw`
      - `taskkill /IM python.exe` / `pythonw.exe`
      - `Get-Process python* | Stop-Process`
      - `pkill -f python` / `killall python`

  * Port-scoped kills targeting a port in `[global].never_kill_ports`
    (sister hubs like :8000 LLM hub, :8090 whisper, :8446 session-host).

  * `git push --force[-with-lease]` to `main` or `master`.

  * Git safety bypass flags: `--no-verify`, `--no-gpg-sign`,
    `-c commit.gpgsign=false`.

Allow-listed (passes through):
  * Port-scoped kills against ports NOT in `never_kill_ports` —
    `Get-NetTCPConnection -LocalPort 8445 | ... Stop-Process` works fine.
  * `git push --force` to a feature branch (not main/master).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

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
    r"\bpkill\b[^\n]*\bpython\b",
    r"\bkillall\b[^\n]*\bpython\b",
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
# Matches `git push ... --force` or `--force-with-lease` AND a main/master target.
GIT_FORCE_PUSH_RE = re.compile(
    r"\bgit\s+push\b(?=[^\n;|&]*--force(?:-with-lease)?\b)"
    r"(?=[^\n;|&]*\b(?:origin|upstream|github)\b)?"
    r"(?=[^\n;|&]*\b(?:main|master)\b)",
    re.IGNORECASE,
)

# ----- port-scoped kills (used to match `LocalPort N`) -----
LOCALPORT_RE = re.compile(r"-LocalPort\s+(\d+)", re.IGNORECASE)
NETSTAT_PORT_RE = re.compile(r":(\d{2,5})\b")


def _scan_port_kills(cmd: str) -> list[int]:
    """Return the list of ports a port-scoped kill is targeting (heuristic)."""
    ports: list[int] = []

    # Heuristic 1: PowerShell `-LocalPort N` clauses
    if re.search(r"\bStop-Process\b", cmd, re.IGNORECASE) or re.search(r"\bkill\b", cmd, re.IGNORECASE):
        ports.extend(int(p) for p in LOCALPORT_RE.findall(cmd))

    # Heuristic 2: cmd contains `Stop-Process` AND a netstat-y `:PORT` reference
    if re.search(r"\bStop-Process\b", cmd, re.IGNORECASE):
        ports.extend(int(p) for p in NETSTAT_PORT_RE.findall(cmd))

    return ports


def main() -> None:
    payload = _lib.read_stdin_json()
    if _lib.tool_name(payload) not in {"Bash", "PowerShell"}:
        _lib.allow()

    cmd = _lib.command_string(payload)
    if not cmd:
        _lib.allow()

    # 1) Blanket python kills - dispatch by shell so an `echo` of a kill string
    #    in the other shell doesn't false-positive.
    tn = _lib.tool_name(payload)
    patterns: list[str] = list(COMMON_BLANKET_KILL)
    if tn == "PowerShell":
        patterns.extend(POWERSHELL_BLANKET_KILL)
    elif tn == "Bash":
        patterns.extend(BASH_BLANKET_KILL)

    for pattern in patterns:
        if re.search(pattern, cmd, re.IGNORECASE):
            _lib.block(
                "Blocked: blanket python(w?) kill detected (matched: " + pattern + "). "
                "This would also kill sister hubs (local-llm-hub :8000, whisper :8090, "
                "session-host :8446). Use port-scoped kill instead: "
                "`Get-NetTCPConnection -LocalPort <PORT> | Select -ExpandProperty OwningProcess | "
                "ForEach-Object { Stop-Process -Id $_ -Force }`."
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
    if GIT_FORCE_PUSH_RE.search(cmd):
        _lib.block(
            "Blocked: `git push --force` targeting main/master. "
            "Force-pushing to a protected branch is destructive. "
            "If you really mean to do this, ask the user first."
        )

    # 4) Port-scoped kills against protected ports - PowerShell-only patterns
    targeted = _scan_port_kills(cmd) if tn == "PowerShell" else []
    if targeted:
        reg = _lib.load_registry()
        forbidden = set(reg.globals.never_kill_ports)
        hits = sorted({p for p in targeted if p in forbidden})
        if hits:
            _lib.block(
                "Blocked: kill targets a protected port "
                + ", ".join(str(p) for p in hits)
                + " (sister hub — listed in projects.toml [global].never_kill_ports). "
                "Killing it would break unrelated apps."
            )

    _lib.allow()


if __name__ == "__main__":
    main()
