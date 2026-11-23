@echo off
rem kb-flow TG inbox bot launcher (minimized window, log: bot.log)
cd /d %~dp0
start "kb-tg-bot" /min cmd /c "python tg_bot_local.py >> bot.log 2>&1"
