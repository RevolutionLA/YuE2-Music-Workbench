@echo off
rem 一键启动音乐工作台（正式逻辑在 scripts\，此文件仅转调，方便从根目录双击）
echo 运行位置: %~f0
if not exist "%~dp0py312\python.exe" (
  echo [错误] 未找到 %~dp0py312\python.exe
  echo 本脚本要求 Python 3.12 环境目录 py312\ 与本脚本同级放置（README「首次运行准备」列明了哪几样不入库）。
  echo 若你用的是完整分发包，请确认整个目录（含 py312\）一起拷全，只挑 .bat 过来是起不来的。
  pause
  exit /b 1
)
call "%~dp0scripts\启动音乐工作台.bat" %*
