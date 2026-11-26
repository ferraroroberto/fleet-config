@echo off
REM Weekly unattended /retro-fleet run. Not wired to an app-launcher Job until
REM the owner says so; the Jobs registry owns the schedule once it is.
REM Mines every agent session log changed since the cached watermark, renders
REM the scored proposal page and posts one Board ask per new proposal.
cd /d E:\automation\fleet-config
E:\automation\fleet-config\.venv\Scripts\python.exe E:\automation\fleet-config\skills\_lib\claude_progress.py "/retro-fleet" --model claude-sonnet-5 --permission-mode bypassPermissions
