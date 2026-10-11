@echo off
setlocal
rem A fresh frozen process must not reuse an old slave's temporary bundle.
set PYINSTALLER_RESET_ENVIRONMENT=1
cd /d G:\eu5_project\CloudManagement\slave_source\dist
CloudSlaveManagement.exe --root G:\eu5_project --name MyServer1
if errorlevel 1 pause
