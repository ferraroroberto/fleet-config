"""Hook-matrix cases for `safe_kill_guard`: blanket kills, protected ports, force-push and --no-verify."""
from __future__ import annotations

from typing import List

from acceptance.hook_cases import Case
from acceptance.shared import REPO

# safe_kill_guard
SAFE_KILL_CASES: List[Case] = [
    ("safe_kill: Stop-Process -Name python -> block",
     "safe_kill_guard",
     {"tool_name": "PowerShell", "tool_input": {"command": "Stop-Process -Name python -Force"}},
     2),
    ("safe_kill: Stop-Process -Name pythonw -> block",
     "safe_kill_guard",
     {"tool_name": "PowerShell", "tool_input": {"command": "Stop-Process -Name pythonw -Force"}},
     2),
    ("safe_kill: port-scoped kill on 8446 (protected) -> block",
     "safe_kill_guard",
     {"tool_name": "PowerShell", "tool_input": {"command": "Get-NetTCPConnection -LocalPort 8446 | Select -ExpandProperty OwningProcess | ForEach-Object { Stop-Process -Id $_ -Force }"}},
     2),
    ("safe_kill: port-scoped kill on 8445 (project port) -> allow",
     "safe_kill_guard",
     {"tool_name": "PowerShell", "tool_input": {"command": "Get-NetTCPConnection -LocalPort 8445 | Select -ExpandProperty OwningProcess | ForEach-Object { Stop-Process -Id $_ -Force }"}},
     0),
    ("safe_kill: git push --force origin main -> block",
     "safe_kill_guard",
     {"tool_name": "Bash", "tool_input": {"command": "git push --force origin main"}},
     2),
    ("safe_kill: git push -f origin main -> block (fleet-config#503)",
     "safe_kill_guard",
     {"tool_name": "Bash", "tool_input": {"command": "git push -f origin main"}},
     2),
    ("safe_kill: git push --force feature/x -> allow",
     "safe_kill_guard",
     {"tool_name": "Bash", "tool_input": {"command": "git push --force origin feature/foo"}},
     0),
    ("safe_kill: git push --foo origin main -> allow (no false positive on -f)",
     "safe_kill_guard",
     {"tool_name": "Bash", "tool_input": {"command": "git push --foo origin main"}},
     0),
    ("safe_kill: git push --force to a branch NAMED *-main-* -> allow (fleet-config#562)",
     "safe_kill_guard",
     {"tool_name": "Bash", "cwd": str(REPO),
      "tool_input": {"command": "git push --force origin chore/rename-main-config-loader"}},
     0),
    ("safe_kill: git -C <dir> push --force origin main -> block (fleet-config#1275)",
     "safe_kill_guard",
     {"tool_name": "Bash", "cwd": str(REPO),
      "tool_input": {"command": "git -C " + str(REPO) + " push --force origin main"}},
     2),
    ("safe_kill: git -c k=v push -f origin master -> block (fleet-config#1275)",
     "safe_kill_guard",
     {"tool_name": "Bash", "cwd": str(REPO),
      "tool_input": {"command": "git -c push.default=current push -f origin master"}},
     2),
    ("safe_kill: git -C <dir> push --force to a branch NAMED *-main-* -> allow (#562 holds, #1275)",
     "safe_kill_guard",
     {"tool_name": "Bash", "cwd": str(REPO),
      "tool_input": {"command": "git -C " + str(REPO) + " push --force origin feature/main-thing"}},
     0),
    ("safe_kill: git push --force origin HEAD:main -> block (refspec dst, fleet-config#562)",
     "safe_kill_guard",
     {"tool_name": "Bash", "cwd": str(REPO),
      "tool_input": {"command": "git push --force-with-lease origin HEAD:main"}},
     2),
    ("safe_kill: git commit --no-verify -> block",
     "safe_kill_guard",
     {"tool_name": "Bash", "tool_input": {"command": "git commit --no-verify -m hi"}},
     2),
    ("safe_kill: pkill -f python3 -> block (fleet-config#503)",
     "safe_kill_guard",
     {"tool_name": "Bash", "tool_input": {"command": "pkill -f python3"}},
     2),
    ("safe_kill: pkill -f pythonw -> block (fleet-config#503)",
     "safe_kill_guard",
     {"tool_name": "Bash", "tool_input": {"command": "pkill -f pythonw"}},
     2),
    ("safe_kill: killall python3 -> block (fleet-config#503)",
     "safe_kill_guard",
     {"tool_name": "Bash", "tool_input": {"command": "killall python3"}},
     2),
    # heuristic 2 used to collect any `:PORT` token anywhere in the whole
    # command once `Stop-Process` appeared anywhere in it, so a PID-scoped
    # kill that merely *mentions* a protected port in a later, unrelated
    # clause was refused. Fixed to scope the `:PORT` match to the same
    # statement as the kill (fleet-config#709).
    ("safe_kill: PID-scoped kill that merely mentions a protected port later -> allow (fleet-config#709)",
     "safe_kill_guard",
     {"tool_name": "PowerShell",
      "tool_input": {"command": "Stop-Process -Id 1234 -Force; Invoke-WebRequest http://127.0.0.1:8446/api/version"}},
     0),
    # Same port mentioned in the SAME statement as the kill still blocks.
    ("safe_kill: kill and protected-port reference in the same statement -> block (fleet-config#709)",
     "safe_kill_guard",
     {"tool_name": "PowerShell",
      "tool_input": {"command": "Stop-Process -Id 1234 -Force # targeting 127.0.0.1:8446"}},
     2),
]

# Kills selected by process name or command line also stop other lanes' processes
# (fleet-config#2118); the same kill by explicit PID or the project's own port passes.
_NAMED_KILL_BLOCKED = [
    ("PowerShell", "Stop-Process -Name node -Force"),
    ("PowerShell", "Get-Process node | Stop-Process -Force"),
    ("PowerShell", "Get-Process -Name node | ForEach-Object { Stop-Process -Id $_.Id }"),
    ("PowerShell", "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*serve_pair*' } "
                   "| ForEach-Object { Stop-Process -Id $_.ProcessId }"),
    ("PowerShell", "Get-CimInstance Win32_Process -Filter \"Name='x.exe'\" | Invoke-CimMethod -MethodName Terminate"),
    ("PowerShell", "Get-WmiObject Win32_Process | Where { $_.CommandLine -match 'x' } | % { $_.Terminate() }"),
    ("PowerShell", "taskkill /IM node.exe /F"),
    ("Bash", "taskkill //IM node.exe //F"),
    ("Bash", "pkill -f serve_pair"),
    ("Bash", "killall node"),
    ("Bash", "kill $(pgrep -f serve_pair)"),
    ("Bash", "ps aux | grep serve | awk '{print $2}' | xargs kill"),
    ("Bash", "powershell -Command \"Stop-Process -Name node\""),
    # The blanket python kills that would take out the sister hubs (fleet-config#2137).
    ("PowerShell", "Get-Process python* | Stop-Process -Force"),
    ("PowerShell", "taskkill /IM pythonw.exe /F"),
    ("Bash", "pkill python3"),
]
_NAMED_KILL_ALLOWED = [
    ("PowerShell", "Stop-Process -Id 1234 -Force"),
    ("PowerShell", "Get-Process -Id 1234 | Stop-Process"),
    ("PowerShell", "Get-CimInstance Win32_Process -Filter \"ParentProcessId=12\" | % { Stop-Process -Id $_.ProcessId }"),
    ("PowerShell", "taskkill /PID 1234 /F"),
    ("Bash", "kill 1234"),
    ("Bash", "kill -9 1234"),
    ("PowerShell", "echo 'Stop-Process -Name node; taskkill /IM node.exe'"),
    ("Bash", "echo pkill -f serve_pair"),
    ("Bash", "rg 'killall' docs/"),
    # A python kill that is only named, in a commit message or a python string, is not a kill.
    ("Bash", "git commit -m \"fix: refuse pkill python and taskkill /IM python.exe\""),
    ("Bash", "python -c \"print('Stop-Process -Name python')\""),
    ("PowerShell", "Write-Output 'Get-Process python | Stop-Process'"),
]
SAFE_KILL_CASES += [
    (f"safe_kill: kill by name or command line -> block ({command[:48]}) (fleet-config#2118)",
     "safe_kill_guard", {"tool_name": tool, "tool_input": {"command": command}}, 2)
    for tool, command in _NAMED_KILL_BLOCKED
] + [
    (f"safe_kill: kill by PID, or text that only names a kill -> allow ({command[:48]}) (fleet-config#2118)",
     "safe_kill_guard", {"tool_name": tool, "tool_input": {"command": command}}, 0)
    for tool, command in _NAMED_KILL_ALLOWED
]
