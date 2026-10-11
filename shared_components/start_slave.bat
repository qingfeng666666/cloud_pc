@echo off
cd /d %~dp0

REM ============================================
REM  云电脑 — 从机启动脚本（修改下面三行即可）
REM ============================================

set HOST_IP=192.168.1.100
set SHARE_ROOT=D:\shared
set SLAVE_NAME=MyServer

REM ============================================

python -m slave.main --root %SHARE_ROOT% --host %HOST_IP% --name %SLAVE_NAME%
pause
