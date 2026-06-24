@echo off
REM Weekly unattended /config-map run — wired as an app-launcher Job
REM (Windows Task Scheduler \AppLauncher\, Fridays 02:30 — staggered off
REM /system-map's 01:00 slot). Regenerates the cross-agent config & convention
REM map, commits the diff if it changed, and posts the image to Slack.
REM Assumes the repo is on `main` for the scheduled refresh to land + push.
cd /d E:\automation\fleet-config
claude -p "/config-map" --permission-mode bypassPermissions
