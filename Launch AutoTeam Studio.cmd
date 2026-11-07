@echo off
setlocal
cd /d "%~dp0"
if not exist "dist\latest-build.txt" goto source
set /p "AUTOTEAM_LAUNCH_EXE=" < "dist\latest-build.txt"
if not exist "%AUTOTEAM_LAUNCH_EXE%" goto source
start "" "%AUTOTEAM_LAUNCH_EXE%"
exit /b 0
:source
set "PYTHONPATH=%~dp0src"
py -3.12 -m autoteam_studio
if errorlevel 1 pause
