@echo off
setlocal
call "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat" >nul 2>&1
cl /nologo /std:c++20 /EHsc /O2 /W3 /utf-8 ^
   /Fo"build\dll3_" /Fe"build\xixing3.dll" /LD ^
   src\xixing_dll.cpp ^
   /link /SUBSYSTEM:WINDOWS /DLL /OUT:build\xixing3.dll user32.lib
endlocal
