@echo off
REM Weekly unattended /config-map run — wired as an app-launcher Job
REM (Windows Task Scheduler \AppLauncher\). The schedule is owned by
REM app-launcher's Jobs registry (config/jobs.json) and is deliberately not
REM restated here; it is staggered clear of /system-map's own run.
REM Regenerates the cross-agent config & convention map, commits the diff if
REM it changed, and posts the image to Slack.
REM Assumes the repo is on `main` for the scheduled refresh to land + push.
cd /d E:\automation\fleet-config
E:\automation\fleet-config\.venv\Scripts\python.exe E:\automation\fleet-config\skills\_lib\claude_progress.py "/config-map" --permission-mode bypassPermissions
