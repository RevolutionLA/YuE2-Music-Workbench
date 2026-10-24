@echo off
rem [encoding] THIS FILE IS UTF-8 (no BOM) ON PURPOSE, and every .bat it calls has an ASCII
rem [encoding] name. cmd.exe decodes a batch file with the console OEM code page; this machine
rem [encoding] runs the Windows "Beta: UTF-8 worldwide language support" option (OEMCP=65001),
rem [encoding] so GBK bytes were parsed as UTF-8 -> Chinese file names became garbage ->
rem [encoding] `call "...\scripts\<chinese>.bat"` died with "is not recognized" and the window
rem [encoding] flashed shut (measured 2026-10-24: double-clicking did nothing at all).
rem [encoding] UTF-8 content + ASCII call targets works under BOTH 65001 and 936.
rem [encoding] Do NOT re-save as GBK, do NOT rename the targets back to Chinese, and do NOT
rem [encoding] add a mid-file code-page switch.
rem 一键启动音乐工作台（正式逻辑在 scripts\，此文件仅转调，方便从根目录双击）
echo 运行位置: %~f0
if not exist "%~dp0py312\python.exe" (
  echo [错误] 未找到 %~dp0py312\python.exe
  echo 本脚本要求 Python 3.12 环境目录 py312\ 与本脚本同级放置（README「首次运行准备」列明了哪几样不入库）。
  echo 若你用的是完整分发包，请确认整个目录（含 py312\）一起拷全，只挑 .bat 过来是起不来的。
  pause
  exit /b 1
)
call "%~dp0scripts\start_lab.bat" %*
