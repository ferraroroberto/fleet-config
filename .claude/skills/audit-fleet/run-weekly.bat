@echo off
REM Weekly fleet codebase-audit wrapper, fired by the app-launcher Jobs tab
REM (job id "codebase-audit-fleet"). The schedule is owned by app-launcher's
REM Jobs registry (config/jobs.json) and is deliberately not restated here.
REM Runs the /audit-fleet skill headless on the local Claude subscription.
REM Sonnet 5 + high effort; bypassPermissions because a scheduled run has
REM no human to answer permission prompts. The orchestrator only does cheap
REM enumeration/dispatch work (see SKILL.md's Execution rules) — easy tier,
REM not hard, per docs/model-tiers.md; the per-repo sweep sub-agents dispatched
REM in step 3 are the ones running hard tier (Opus), never the top-level
REM launcher. claude_progress.py converts Claude's verbose stream-json into
REM flushed, human-readable milestones for app-launcher's live Jobs pane.
REM
REM Optional %1 is forwarded as the skill argument: empty for the whole fleet,
REM or a bare repo name to restrict to a single repo (see SKILL.md's Arguments).
cd /d E:\automation\fleet-config
E:\automation\fleet-config\.venv\Scripts\python.exe E:\automation\fleet-config\skills\_lib\claude_progress.py "/audit-fleet %~1" --model claude-sonnet-5 --effort high --permission-mode bypassPermissions
