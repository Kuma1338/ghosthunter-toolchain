@echo off
setlocal
call "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat" >nul 2>&1
cl /nologo /std:c++20 /EHsc /O2 /W3 /utf-8 /DUNICODE /D_UNICODE ^
   /Fo"build\inj_" /Fe"build\inject.exe" ^
   src\inject.cpp ^
   /link /SUBSYSTEM:CONSOLE psapi.lib
endlocal
