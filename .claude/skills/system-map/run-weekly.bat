@echo off
REM Weekly unattended /system-map run — wired as an app-launcher Job
REM (Windows Task Scheduler \AppLauncher\, Fridays 01:00). Regenerates the
REM fleet map, commits the diff if it changed, and posts the image to Slack.
REM Assumes the repo is on `main` for the scheduled refresh to land + push.
cd /d E:\automation\fleet-config
claude -p "/system-map" --permission-mode bypassPermissions
