@echo off
REM Unattended, all-bucket cleanup wrapper — the fully autonomous sibling of
REM /cleanup-fleet's attended, single-bucket, human-gated flow. Not wired to
REM an app-launcher Job by default; register it manually once the first
REM attended dry run (see SKILL.md's Notes) has proven the Workflow +
REM TaskOutput poll loop actually carries a run to completion.
REM Sonnet 5 + high effort, matching /audit-fleet's shape (a heavy fan-out
REM orchestrator, not a light single-pass skill); bypassPermissions because a
REM scheduled run has no human to answer permission prompts. claude_progress.py
REM emits flushed, filtered milestones for app-launcher's live Jobs pane.
REM
REM Optional %1 is forwarded as the skill argument: empty for all seven queued buckets,
REM or one or more bucket names to restrict the run (see SKILL.md's Arguments).
cd /d E:\automation\fleet-config
E:\automation\fleet-config\.venv\Scripts\python.exe E:\automation\fleet-config\skills\_lib\claude_progress.py "/cleanup-fleet-all %~1" --model claude-sonnet-5 --effort high --permission-mode bypassPermissions
