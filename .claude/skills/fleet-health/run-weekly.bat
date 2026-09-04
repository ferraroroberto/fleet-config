@echo off
REM Weekly unattended /fleet-health run — wired as an app-launcher Job
REM (Windows Task Scheduler \AppLauncher\; the schedule is owned by
REM app-launcher's Jobs registry, config/jobs.json). Captures every reachable
REM machine through its own hub, analyses each against last week, and appends to
REM the incremental ledger under %USERPROFILE%\.claude\fleet-health\, then posts
REM a digest to Telegram. Captures block for ~1h — the job must not be killed early.
cd /d E:\automation\fleet-config
E:\automation\fleet-config\.venv\Scripts\python.exe E:\automation\fleet-config\skills\_lib\claude_progress.py "/fleet-health" --permission-mode bypassPermissions
