@echo off
rem Self-service: push kb-flow updates to GitHub (device code flow)
cd /d %~dp0
python push_github.py
pause
