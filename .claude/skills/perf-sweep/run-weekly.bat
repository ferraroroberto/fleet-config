@echo off
REM Weekly fleet phone-speed sweep, fired by the app-launcher Jobs tab
REM (job id "perf-sweep-fleet"). The schedule is owned by app-launcher's
REM Jobs registry (config/jobs.json) and is deliberately not restated here.
REM Runs the /perf-sweep skill headless on the local Claude subscription.
REM
REM The measuring is one deterministic Python command (perf_review sweep), so the
REM orchestrator only starts it, waits for it and relays the digest: Sonnet is
REM enough. bypassPermissions because a scheduled run has no human to answer
REM permission prompts. claude_progress.py converts Claude's verbose stream-json
REM into flushed, human-readable milestones for app-launcher's live Jobs pane.
REM
REM Optional %1 is forwarded as the skill argument: empty for every web app, or a
REM bare repo name to measure just that one (see SKILL.md's Arguments).
cd /d E:\automation\fleet-config
E:\automation\fleet-config\.venv\Scripts\python.exe E:\automation\fleet-config\skills\_lib\claude_progress.py "/perf-sweep %~1" --model claude-sonnet-5 --permission-mode bypassPermissions
