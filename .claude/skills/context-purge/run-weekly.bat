@echo off
cd /d E:\automation\fleet-config
REM --delivery-check is an outer post-condition, run after the child exits and
REM whatever its exit code was: it asks whether this run actually published a
REM digest and pinged it, rather than trusting a clean exit (fleet-config#627).
E:\automation\fleet-config\.venv\Scripts\python.exe E:\automation\fleet-config\skills\_lib\claude_progress.py "/context-purge fleet" --model opus --delivery-check E:\automation\fleet-config\.claude\skills\context-purge\delivery_check.py --permission-mode bypassPermissions
