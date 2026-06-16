@echo off
REM Weekly unattended /context-audit run — wired as an app-launcher Job
REM (Windows Task Scheduler \AppLauncher\, Fridays 00:30). Audits the fleet's
REM always-on context surface (CLAUDE.md token budgets, skill-description word
REM counts, single-home-by-altitude violations), upserts the context-audit
REM ledger issue + weekly comment, and posts a Slack digest. bypassPermissions
REM because a scheduled run has no human to answer permission prompts.
cd /d E:\automation\fleet-config
claude -p "/context-audit" --permission-mode bypassPermissions
