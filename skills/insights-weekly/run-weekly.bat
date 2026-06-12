@echo off
REM Weekly unattended /insights-weekly run — wired as an app-launcher Job
REM (Windows Task Scheduler \AppLauncher\, Fridays 01:00). Refreshes /insights,
REM diffs the newest report HTML against the previous one via the local LLM hub,
REM writes a dated note under %USERPROFILE%\.claude\usage-data\weekly\, and posts
REM a digest to Slack. First run captures a baseline instead of a diff.
cd /d E:\automation\claude-config
claude -p "/insights-weekly" --permission-mode bypassPermissions
