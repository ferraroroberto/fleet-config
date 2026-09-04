@echo off
REM Weekly unattended /insights-weekly run — wired as an app-launcher Job
REM (Windows Task Scheduler \AppLauncher\; the schedule is owned by
REM app-launcher's Jobs registry, config/jobs.json). Refreshes /insights,
REM diffs the newest report HTML against the previous one via the local LLM hub,
REM writes a dated note under %USERPROFILE%\.claude\usage-data\weekly\, and posts
REM a digest to Telegram. First run captures a baseline instead of a diff.
cd /d E:\automation\fleet-config
E:\automation\fleet-config\.venv\Scripts\python.exe E:\automation\fleet-config\skills\_lib\claude_progress.py "/insights-weekly" --permission-mode bypassPermissions
