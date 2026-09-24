@echo off
setlocal
call "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat" >nul 2>&1
if "%1"=="clean" ( rmdir /s /q build 2>nul & exit /b 0 )
if not exist build mkdir build
cl /nologo /std:c++20 /EHsc /O2 /W3 /utf-8 /DUNICODE /D_UNICODE ^
   /Fo"build\\" /Fe"build\overlay.exe" ^
   src\main.cpp src\ue4.cpp src\xixing.cpp ^
   /link /SUBSYSTEM:WINDOWS user32.lib gdi32.lib gdiplus.lib psapi.lib
endlocal
