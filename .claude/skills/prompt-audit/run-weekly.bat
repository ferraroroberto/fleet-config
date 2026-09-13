@echo off
REM Weekly unattended /prompt-audit run, fired by an app-launcher Job (the
REM schedule is owned by app-launcher's config/jobs.json; the committed example
REM lives in its config/jobs.sample.json). It runs ahead of cleanup-fleet-all
REM so the prompt-drift bucket is populated when cleanup starts (fleet-config#834).
REM
REM --delivery-check is an outer post-condition, run after the child exits
REM whatever its exit code was: a fresh ledger digest must carry status=complete
REM and either scan=posted or update-issue=#N, or the job goes red.
REM
REM Optional %1 is forwarded as the skill argument (e.g. --dry-run, which posts
REM nothing and so fails the delivery check by design).
cd /d E:\automation\fleet-config
E:\automation\fleet-config\.venv\Scripts\python.exe E:\automation\fleet-config\skills\_lib\claude_progress.py "/prompt-audit %~1" --delivery-check E:\automation\fleet-config\.claude\skills\prompt-audit\delivery_check.py --permission-mode bypassPermissions
