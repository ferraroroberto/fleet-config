@echo off
REM Weekly skill evals (fleet-config#1131), fired by an app-launcher Job scheduled before the
REM /prompt-audit job (the committed example lives in app-launcher's config/jobs.sample.json).
REM plugin eval is the runner, so no claude -p session wraps it: eval_rotation.py runs in the
REM foreground to completion and writes ~/.claude/prompt-audit/evals/, which /prompt-audit reads.
REM It exits 1 when no scored run produced a result, which turns this job red; /prompt-audit then
REM reports the evals as not established, and its own delivery check is unaffected.
cd /d E:\automation\fleet-config
E:\automation\fleet-config\.venv\Scripts\python.exe E:\automation\fleet-config\.claude\skills\prompt-audit\eval_rotation.py --rotate
