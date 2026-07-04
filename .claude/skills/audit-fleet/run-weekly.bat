@echo off
REM Weekly fleet codebase-audit wrapper, fired by the app-launcher Jobs tab
REM (job id "codebase-audit-fleet", weekly THU 22:00, visible console).
REM Runs the /audit-fleet skill headless on the local Claude subscription.
REM Sonnet 5 + high effort; bypassPermissions because a scheduled run has
REM no human to answer permission prompts. The orchestrator only does cheap
REM enumeration/dispatch work (see SKILL.md's Execution rules) — hard tier,
REM not extreme, per docs/model-tiers.md; Opus is reserved for the rare
REM extreme-tier escalation a sub-agent might make, never the top-level
REM launcher. --verbose streams the turn-by-turn activity to stdout so the
REM visible console shows live progress instead of sitting on one line until
REM claude -p flushes its buffered result at the end.
REM
REM Optional %1 is forwarded as the skill argument: empty for the whole fleet,
REM or a bare repo name to restrict to a single repo (see SKILL.md's Arguments).
cd /d E:\automation\fleet-config
claude -p "/audit-fleet %~1" --model claude-sonnet-5 --effort high --permission-mode bypassPermissions --verbose
