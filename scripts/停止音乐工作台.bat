@echo off
chcp 65001 >nul
title 音乐工作台 - 停止
cd /d %~dp0..
rem 本脚本位于 scripts\，所有相对路径均以仓库根为基准

rem ---- 端口：唯一真源 ports.json（与启动脚本同源）----
set "GATEWAY_PORT=7863"
set "DSH_PORT=3081"
for /f "usebackq tokens=*" %%p in (`powershell -NoProfile -Command "try{$j=Get-Content '%cd%\ports.json' -Raw | ConvertFrom-Json; '{0} {1}' -f $(if($j.gateway){$j.gateway}else{'7863'}),$(if($j.dsh){$j.dsh}else{'3081'})}catch{'7863 3081'}" 2^>nul`) do (
  for /f "tokens=1,2" %%a in ("%%p") do (set "GATEWAY_PORT=%%a" & set "DSH_PORT=%%b")
)

echo 停止 音乐工作台（正在进行的生成任务将被中断）...

rem 先停看门狗，否则它会立刻把网关重新拉起来
if exist "runtime\_watchdog.pid" (
  for /f %%p in (runtime\_watchdog.pid) do taskkill /PID %%p /F >nul 2>&1
  del /q "runtime\_watchdog.pid" >nul 2>&1
)
taskkill /IM pythonw.exe /F >nul 2>&1

rem 干掉 MCP 工具进程（dsh 调用 AI 工具时拉起，脚本在 src\mcp_server.py）。
rem 按命令行匹配而非进程名，避免误杀 py312 下其它 python 进程。
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*mcp_server.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1

rem 端口匹配带尾随空格，避免 :7863 误命中 :17863
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /C:":%DSH_PORT% " ^| findstr "LISTENING"') do taskkill /PID %%p /F >nul 2>&1
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /C:":%GATEWAY_PORT% " ^| findstr "LISTENING"') do taskkill /PID %%p /F >nul 2>&1
taskkill /IM audiocpp_server.exe /F >nul 2>&1

echo 全部已停止（网关 :%GATEWAY_PORT% / 工作台 :%DSH_PORT% / 推理引擎 :8080）。
ping -n 3 127.0.0.1 >nul
exit /b 0
