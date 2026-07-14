@echo off
REM Unattended, all-bucket cleanup wrapper — the fully autonomous sibling of
REM /cleanup-fleet's attended, single-bucket, human-gated flow. Not wired to
REM an app-launcher Job by default; register it manually once the first
REM attended dry run (see SKILL.md's Notes) has proven the Workflow +
REM TaskOutput poll loop actually carries a run to completion.
REM Sonnet 5 + high effort, matching /audit-fleet's shape (a heavy fan-out
REM orchestrator, not a light single-pass skill); bypassPermissions because a
REM scheduled run has no human to answer permission prompts. --verbose streams
REM turn-by-turn activity to stdout so a visible console shows live progress.
REM
REM Optional %1 is forwarded as the skill argument: empty for all seven queued buckets,
REM or one or more bucket names to restrict the run (see SKILL.md's Arguments).
cd /d E:\automation\fleet-config
claude -p "/cleanup-fleet-all %~1" --model claude-sonnet-5 --effort high --permission-mode bypassPermissions --verbose
