@echo off
REM Weekly unattended /learning-log run — wired as an app-launcher Job
REM (Windows Task Scheduler \AppLauncher\, Fridays 01:30). Reads the fleet's
REM merged PRs + closed issues since the last run, narrates the learning log +
REM horizon via the local hub, upserts the learning-log ledger issue + posts
REM the weekly narrative as a comment, and fires a Slack completion ping.
REM No source code is read; the auto window anchors to the ledger's last-run-at.
cd /d E:\automation\claude-config
claude -p "/learning-log" --permission-mode bypassPermissions
