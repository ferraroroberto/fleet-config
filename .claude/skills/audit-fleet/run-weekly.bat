@echo off
REM Weekly fleet codebase-audit wrapper, fired by the app-launcher Jobs tab
REM (job id "codebase-audit-fleet", weekly THU 22:00, visible console).
REM Runs the /audit-fleet skill headless on the local Claude subscription.
REM Opus 4.7 + high effort; bypassPermissions because a scheduled run has
REM no human to answer permission prompts. --verbose streams the turn-by-turn
REM activity to stdout so the visible console shows live progress instead of
REM sitting on one line until claude -p flushes its buffered result at the end.
REM
REM Optional %1 is forwarded as the skill argument: empty for the weekly job (a
REM fresh run that resets the retry chain), "resume" when the self-relaunch task
REM fires it after a session-rate-limit cut-off (the skill continues the chain
REM and the ledger gate skips already-audited repos). See skills/_lib/audit_retry.py.
cd /d E:\automation\fleet-config
claude -p "/audit-fleet %~1" --model claude-opus-4-7 --effort high --permission-mode bypassPermissions --verbose
