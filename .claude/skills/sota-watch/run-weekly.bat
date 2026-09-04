@echo off
REM Weekly unattended /sota-watch run — wired as an app-launcher Job
REM (Windows Task Scheduler \AppLauncher\; the schedule is owned by
REM app-launcher's Jobs registry, config/jobs.json). Walks the SOTA
REM watchlist, deep-researches due areas, relays the local-llm-hub frontier
REM ledger, and comments the digest on the sota-watch ledger issue + Telegram.
cd /d E:\automation\fleet-config
E:\automation\fleet-config\.venv\Scripts\python.exe E:\automation\fleet-config\skills\_lib\claude_progress.py "/sota-watch" --permission-mode bypassPermissions
