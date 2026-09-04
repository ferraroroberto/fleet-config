@echo off
REM Weekly unattended /system-map run — wired as an app-launcher Job
REM (Windows Task Scheduler \AppLauncher\; the schedule is owned by
REM app-launcher's Jobs registry, config/jobs.json). Regenerates the
REM fleet map, commits the diff if it changed, and posts the image to Telegram.
REM Assumes the repo is on `main` for the scheduled refresh to land + push.
cd /d E:\automation\fleet-config
E:\automation\fleet-config\.venv\Scripts\python.exe E:\automation\fleet-config\skills\_lib\claude_progress.py "/system-map" --permission-mode bypassPermissions
