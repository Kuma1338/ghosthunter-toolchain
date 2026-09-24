@echo off
cd /d D:\gh_tools\overlay
taskkill /F /IM overlay.exe >nul 2>&1
if exist build\overlay.exe copy /y build\overlay.exe build\overlay.bak.exe >nul
call "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat" >nul 2>&1
cl /nologo /std:c++20 /EHsc /O2 /W3 /utf-8 /DUNICODE /D_UNICODE /Fo"build\\" /Fe"build\overlay.exe" src\main.cpp src\ue4.cpp src\xixing.cpp /link /SUBSYSTEM:WINDOWS user32.lib gdi32.lib gdiplus.lib psapi.lib
echo BUILD_EXITCODE=%errorlevel%
