@echo off
rem 一键启动音乐工作台（正式逻辑在 scripts\，此文件仅转调，方便从根目录双击）
echo 运行位置: %~f0
if not exist "%~dp0py312\python.exe" (
  echo [错误] 未找到 %~dp0py312\python.exe
  echo 你双击的不是本项目的启动脚本，请到 E:\AI\10AIMusic\Yue\yuE-2\yuE-2\ 下运行。
  pause
  exit /b 1
)
call "%~dp0scripts\启动音乐工作台.bat" %*
