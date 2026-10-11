@echo off
cd /d %~dp0

echo ========================================
echo   Building Cloud PC Host (windowed exe)
echo ========================================

python -m pip install -r ..\requirements-host-gui.txt -r ..\requirements-build.txt
if errorlevel 1 exit /b 1
python -m PyInstaller --noconfirm cloud_host.spec
if errorlevel 1 exit /b 1

echo.
echo Done! Output: dist\CloudHost.exe
pause
