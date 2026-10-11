@echo off
cd /d %~dp0

echo ========================================
echo   Building Cloud PC Slave (onefile exe)
echo ========================================

python -m pip install -r ..\requirements-slave.txt -r ..\requirements-build.txt
if errorlevel 1 exit /b 1
python -m PyInstaller --noconfirm cloud_slave.spec
if errorlevel 1 exit /b 1

echo.
echo Done! Output: dist\CloudSlave.exe
pause
