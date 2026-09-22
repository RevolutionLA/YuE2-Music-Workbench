@echo off
chcp 65001 >nul
title 音乐工作台 - 停止
cd /d %~dp0..
rem 本脚本位于 scripts\，所有相对路径均以仓库根为基准
echo 停止 音乐工作台（正在进行的生成任务将被中断）...
rem 先停看门狗（否则它会自动把网关拉起来）
if exist "runtime\_watchdog.pid" (
  for /f %%p in (runtime\_watchdog.pid) do taskkill /PID %%p /F >nul 2>&1
  del /q "runtime\_watchdog.pid" >nul 2>&1
)
taskkill /IM pythonw.exe /F >nul 2>&1
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":3081" ^| findstr "LISTENING"') do taskkill /PID %%p /F >nul 2>&1
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":7863" ^| findstr "LISTENING"') do taskkill /PID %%p /F >nul 2>&1
taskkill /IM audiocpp_server.exe /F >nul 2>&1
echo 全部已停止。
ping -n 3 127.0.0.1 >nul
exit /b 0
