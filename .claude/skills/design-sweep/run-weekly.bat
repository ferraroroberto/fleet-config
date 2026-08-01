@echo off
REM Weekly fleet design-drift sweep, fired by the app-launcher Jobs tab
REM (job id "design-sweep-fleet"). The schedule is owned by app-launcher's
REM Jobs registry (config/jobs.json) and is deliberately not restated here.
REM Runs the /design-sweep skill headless on the local Claude subscription.
REM
REM Opus + high effort for the ORCHESTRATOR: unlike /audit-fleet (whose top-level
REM loop is cheap enumeration → easy/Sonnet), the design-sweep orchestrator runs
REM at hard tier because the enumeration/gating/digest reasoning is where a
REM mistake is most expensive (fleet-config#180, docs/model-tiers.md). The
REM per-repo /design-sync sub-agents it dispatches in step 3 run on Sonnet —
REM cheaper worker tier, and exempt from the <=3-concurrent-Opus burst cap, so
REM only ever the one Opus orchestrator is in flight. bypassPermissions because a
REM scheduled run has no human to answer permission prompts. claude_progress.py
REM converts Claude's verbose stream-json into flushed, human-readable milestones
REM for app-launcher's live Jobs pane.
REM
REM Optional %1 is forwarded as the skill argument: empty for the whole fleet,
REM or a bare repo name to restrict to a single repo (see SKILL.md's Arguments).
cd /d E:\automation\fleet-config
E:\automation\fleet-config\.venv\Scripts\python.exe E:\automation\fleet-config\skills\_lib\claude_progress.py "/design-sweep %~1" --model opus --effort high --permission-mode bypassPermissions
