@echo off
REM Weekly unattended /learning-log run — wired as an app-launcher Job
REM (Windows Task Scheduler \AppLauncher\, Fridays 01:30). Gathers the fleet's
REM merged PRs + closed issues since the last run, computes exact productivity
REM stats, fans out one Sonnet sub-agent per work-type bucket to extract
REM insights, aggregates the learning log + horizon, upserts the learning-log
REM ledger issue + posts the weekly digest as a comment, and fires a Slack ping.
REM No source code is read; the auto window anchors to the ledger's last-run-at.
cd /d E:\automation\claude-config
claude -p "/learning-log" --model claude-sonnet-4-6 --permission-mode bypassPermissions
