@echo off
cd /d "%~dp0\..\.."
py -3 -m pip install pillow pyinstaller
if errorlevel 1 exit /b 1
py -3 -m PyInstaller --noconfirm --clean --windowed --onefile --paths . --name CloudManagement --version-file cloud_pc\shared_components\windows_version_info.txt --add-data "cloud_pc\LICENSE;." --hidden-import PIL.ImageTk cloud_pc\management_ui\windows_entry.py
