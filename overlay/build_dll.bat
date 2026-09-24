@echo off
setlocal
call "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat" >nul 2>&1
if not exist build mkdir build
cl /nologo /std:c++20 /EHsc /O2 /W3 /utf-8 ^
   /Fo"build\\dll_" /Fe"build\xixing.dll" /LD ^
   src\xixing_dll.cpp ^
   /link /SUBSYSTEM:WINDOWS /DLL /OUT:build\xixing.dll user32.lib
endlocal
