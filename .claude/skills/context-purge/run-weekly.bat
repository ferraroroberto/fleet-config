@echo off
cd /d E:\automation\fleet-config
claude -p "/context-purge fleet" --model opus --permission-mode bypassPermissions
