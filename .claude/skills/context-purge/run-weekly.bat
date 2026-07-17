@echo off
cd /d E:\automation\fleet-config
E:\automation\fleet-config\.venv\Scripts\python.exe E:\automation\fleet-config\skills\_lib\claude_progress.py "/context-purge fleet" --model opus --permission-mode bypassPermissions
