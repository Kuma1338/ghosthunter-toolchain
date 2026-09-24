@echo off
REM Run this ON THE GAME DESKTOP (session 1) - NOT over ssh.
REM The queue shared-memory is session-local; ssh (session 0) cannot see it.
cd /d D:\gh_tools\tools
echo ============================================================
echo   probe.py - is the Server* backdoor family alive?
echo   - enter a match first (not main city)
echo   - NO need to get hit. Just run it, then check:
echo       * did your skill cooldowns snap to ready? (ServerResetCD)
echo       * hit one small mob - damage jumps to ~26000? (ServeraddATK)
echo   - do NOT press overlay F9/M4 while this runs
echo ============================================================
python probe.py
echo.
echo === done - log saved to D:\gh_tools\tools\probe_log.txt ===
pause
