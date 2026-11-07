@echo off
setlocal
cd /d "%~dp0"
if not exist ".build-venv\Scripts\python.exe" py -3.12 -m venv .build-venv
if errorlevel 1 goto failed
".build-venv\Scripts\python.exe" -m pip install -r requirements-build.txt
if errorlevel 1 goto failed
".build-venv\Scripts\python.exe" -m pip install --no-deps --no-build-isolation -e .
if errorlevel 1 goto failed
".build-venv\Scripts\python.exe" scripts\build_windows.py
if errorlevel 1 goto failed
echo Build and offline verification completed. Use Launch AutoTeam Studio.cmd.
pause
exit /b 0
:failed
echo Build failed. Read the error above. Existing releases and project histories were preserved.
pause
exit /b 1
