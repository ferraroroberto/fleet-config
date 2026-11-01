"""Hook-matrix cases for `safe_kill_guard`'s host-power rule: commands that power off,
restart, sleep or hibernate the host are refused; commands that only name one pass.

`hooks/` is junctioned live into every session, so a false refusal here blocks
agents fleet-wide: the look-alikes are pinned as carefully as the refusals.
`guard_checks.host_power_checks` replays both lists with Codex-shaped payloads.
"""
from __future__ import annotations

from typing import List, Tuple

from acceptance.hook_cases import Case

# (tool, command) the guard must refuse.
REFUSED: List[Tuple[str, str]] = [
    ("PowerShell", "shutdown /s /t 0"),
    ("PowerShell", "shutdown /r /t 0"),
    ("Bash", "shutdown //s //t 0"),
    ("Bash", "shutdown -h"),
    ("PowerShell", "shutdown /p"),
    ("PowerShell", "shutdown.exe /g /t 0"),
    ("PowerShell", "shutdown /sg"),
    ("Bash", "C:/Windows/System32/shutdown.exe /s /f"),
    ("PowerShell", "& shutdown.exe /r /t 0"),
    ("Bash", "git status && shutdown /s /t 0"),
    ("Bash", "sudo shutdown -r"),
    ("Bash", "shutdown \\\n  /s"),
    ("PowerShell", "Stop-Computer -Force"),
    ("PowerShell", "Restart-Computer"),
    ("PowerShell", "Restart-Computer -ComputerName localhost -Force"),
    ("PowerShell", "Restart-Computer -ComputerName $env:COMPUTERNAME"),
    ("PowerShell", "stop-computer -cn ."),
    ("PowerShell", "echo hi; Stop-Computer"),
    ("PowerShell", "Invoke-Command -ScriptBlock { Restart-Computer -Force }"),
    ("PowerShell", "rundll32.exe powrprof.dll,SetSuspendState 0,1,0"),
    ("Bash", "rundll32 powrprof.dll,SetSuspendState Sleep"),
    ("PowerShell", "psshutdown -d -t 0"),
    ("PowerShell", "psshutdown64 -r"),
    ("PowerShell", "wmic os where primary=1 call Win32Shutdown 1"),
    ("Bash", "wmic os call reboot"),
    ("PowerShell", "Get-CimInstance Win32_OperatingSystem | Invoke-CimMethod -MethodName Shutdown"),
    ("PowerShell", "Invoke-WmiMethod -Class Win32_OperatingSystem -Name Win32Shutdown -ArgumentList 1"),
    ("PowerShell", "powercfg /setacvalueindex SCHEME_CURRENT SUB_BUTTONS PBUTTONACTION 0"),
    ("PowerShell", "powercfg /setdcvalueindex SCHEME_CURRENT 238c9fa8-0aad-41ed-83f4-97be242c8f20 STANDBYIDLE 60"),
    ("PowerShell", "powercfg /change standby-timeout-ac 0"),
    ("Bash", "powercfg -x -hibernate-timeout-ac 30"),
    ("PowerShell", "powercfg /h off"),
    ("PowerShell", "Start-Process shutdown -ArgumentList '/s /t 0'"),
    ("PowerShell", "Start-Process -FilePath shutdown.exe -ArgumentList '/r','/t','0' -WindowStyle Hidden"),
    ("Bash", 'powershell -Command "Stop-Computer -Force"'),
    ("Bash", "cmd //c shutdown /s /t 0"),
    ("Bash", 'bash -c "shutdown /r /t 0"'),
    ("PowerShell", "iex 'Restart-Computer'"),
]

# (tool, command) the guard must let through: the name appears, nothing powers off.
ALLOWED: List[Tuple[str, str]] = [
    ("Bash", "cat shutdown.log"),
    ("Bash", "ls docs/shutdown-notes.md"),
    ("Bash", "notepad shutdown.txt"),
    ("Bash", "find . -name '*shutdown*'"),
    ("Bash", 'grep -rn "shutdown /s" .'),
    ("Bash", "rg Stop-Computer hooks"),
    ("PowerShell", "Select-String -Pattern 'Restart-Computer' -Path *.ps1"),
    ("Bash", 'git commit -m "block shutdown /s and Restart-Computer"'),
    ("Bash", "git commit -F - <<'EOF'\nfix: guard\n\nStop-Computer and shutdown /s are refused.\nEOF"),
    ("PowerShell", "git commit -m @'\nfix: guard\n\nRestart-Computer is refused, don't run it.\n'@"),
    ("Bash", 'gh issue create --title x --body "shutdown /s /t 0"'),
    ("PowerShell", "Get-Help Stop-Computer"),
    ("PowerShell", "Get-Command Restart-Computer"),
    ("PowerShell", 'powershell -Command "Get-Help Restart-Computer"'),
    ("Bash", 'bash -c "grep shutdown /s file"'),
    ("Bash", "echo shutdown /s"),
    ("PowerShell", "Write-Output 'Stop-Computer'"),
    ("Bash", "ls # shutdown /s"),
    ("PowerShell", "shutdown /a"),
    ("PowerShell", "shutdown /?"),
    ("PowerShell", "powercfg /q SCHEME_CURRENT SUB_BUTTONS"),
    ("PowerShell", "powercfg /requests"),
    ("PowerShell", "wmic os get caption"),
    ("PowerShell", "Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine='notepad'}"),
    ("PowerShell", "rundll32.exe user32.dll,LockWorkStation"),
    ("PowerShell", "Restart-Service spooler"),
    ("PowerShell", "Start-Process notepad -ArgumentList shutdown.txt"),
    ("PowerShell", "Restart-Computer -ComputerName buildbox-07"),
    ("PowerShell", "shutdown /r /m \\\\buildbox-07"),
    ("PowerShell", "psshutdown -a"),
]

HOST_POWER_CASES: List[Case] = [
    *[(f"host_power: {tool} {cmd!r} -> block", "safe_kill_guard",
       {"tool_name": tool, "tool_input": {"command": cmd}}, 2) for tool, cmd in REFUSED],
    *[(f"host_power: {tool} {cmd!r} -> allow", "safe_kill_guard",
       {"tool_name": tool, "tool_input": {"command": cmd}}, 0) for tool, cmd in ALLOWED],
]
