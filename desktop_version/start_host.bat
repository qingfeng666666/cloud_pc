@echo off
cd /d %~dp0
set PYTHONPATH=%~dp0..\shared_components
start "" pythonw -m host.main
