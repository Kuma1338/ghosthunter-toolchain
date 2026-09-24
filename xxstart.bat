@echo off
REM XiXing one-shot starter: ESP overlay + DLL deploy + logger arm
REM Run AFTER entering a match. Then: press F on ONE box, then run tools\xxstar.py whenever you want to suck the area.
cd /d D:\gh_tools
start "" overlay\build\overlay.exe
python tools\xxdeploy.py
echo.
echo === READY ===
echo 1. Open ONE box manually with F  (calibration source)
echo 2. Run:  python tools\xxstar.py   (whenever you want to suck this area)
echo.
pause
