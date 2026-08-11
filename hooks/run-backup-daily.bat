@echo off
REM Daily unattended fleet-private backup (fleet-config#590) — wired as an
REM app-launcher Job (Windows Task Scheduler \AppLauncher\; the schedule is
REM owned by app-launcher's Jobs registry, config/jobs.json).
REM
REM Plain Python, NOT a `claude -p` skill, so it deliberately does not route
REM through skills/_lib/claude_progress.py: there is no stream to narrate and
REM no false-success shape to guard against — the script's own exit code is
REM the truth, and the Job's `alert_on_failure` keys off it.
REM
REM Synchronous by design. Nothing here may background a step: an unattended
REM run has nobody to resume it.
cd /d E:\automation\fleet-config
E:\automation\fleet-config\.venv\Scripts\python.exe E:\automation\fleet-config\hooks\backup_private.py
exit /b %ERRORLEVEL%
