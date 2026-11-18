@echo off
REM Weekly unattended burst: the burn, load and wall-clock acceptance units, a hermetic re-run of the
REM whole gate from a fresh detached clone, and the per-unit runtime medians from the unit ledger.
REM Wired as an app-launcher Job (the schedule is owned by app-launcher's Jobs registry, not this file).
REM No model is involved, so this calls burst.py directly instead of going through the model adapter; the exit code
REM is burst.py's: 0 green, 1 a red unit, 3 a leg that could not be established. The hermetic gate takes
REM as long as a full acceptance run, so the job must not be killed early.
cd /d E:\automation\fleet-config
E:\automation\fleet-config\.venv\Scripts\python.exe E:\automation\fleet-config\.claude\skills\weekly-burst\burst.py
