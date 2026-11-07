@echo off
REM Daily unattended /inspiration-scan run, wired as an app-launcher Job
REM (the schedule is owned by app-launcher's Jobs registry, config/jobs.json).
REM The file keeps the repo's run-weekly.bat name for every scheduled-skill
REM launcher. Reads the owner's sources, scores new items through the hub,
REM and delivers the HTML digest.
cd /d E:\automation\fleet-config
E:\automation\fleet-config\.venv\Scripts\python.exe E:\automation\fleet-config\skills\_lib\claude_progress.py "/inspiration-scan" --permission-mode bypassPermissions
