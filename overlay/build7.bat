@echo off
cd /d "%~dp0."
call "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat" >nul 2>&1
cl /nologo /std:c++20 /EHsc /O2 /W3 /utf-8 /LD /Fo"build\dll8_" /Fe"build\xixing8.dll" src\xixing_dll.cpp /link user32.lib
if exist build\xixing8.dll (echo DLL_BUILD_OK) else (echo DLL_BUILD_FAILED)
cl /nologo /std:c++20 /EHsc /O2 /W3 /utf-8 /DUNICODE /D_UNICODE ^
   /Fo"build\\" /Fe"build\overlay.exe" ^
   src\main.cpp src\ue4.cpp src\xixing.cpp ^
   /link /SUBSYSTEM:WINDOWS user32.lib gdi32.lib gdiplus.lib psapi.lib
if exist build\overlay.exe (echo OVERLAY_BUILD_OK) else (echo OVERLAY_BUILD_FAILED)
