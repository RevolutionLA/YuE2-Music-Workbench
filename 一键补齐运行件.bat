@echo off
rem [encoding] THIS FILE IS GBK (OEM cp936) ON PURPOSE. cmd.exe parses a batch file with the
rem [encoding] console's OEM codepage; a UTF-8 batch under cp936 splits lines mid-character
rem [encoding] and some commands silently stop running. Do NOT re-save as UTF-8 and do NOT
rem [encoding] add a mid-file code-page switch. See .gitattributes.
title 音乐工作台 - 补齐运行件
cd /d %~dp0
rem Python 的输出必须跟着本控制台的 OEM 码页走，遇到编不出的字符只降级不崩。
rem 以前这条靠 chcp 65001 兜着；现在批处理本身是 GBK（见 .gitattributes），不能再中途换码页，
rem 所以读当前码页拼 PYTHONIOENCODING。实测不加这行时 print(emoji) 会 UnicodeEncodeError，
rem 把下载脚本判成失败，启动脚本随即以为模型缺失、又想去重下 2.9GB。
for /f "tokens=2 delims=:" %%p in ('chcp') do set "OEMCP=%%p"
set "OEMCP=%OEMCP: =%"
rem 只认纯数字：畸形值会让 Python 初始化直接 Fatal（评审 T7）
echo %OEMCP%|findstr /r "^[0-9][0-9]*$" >nul || set "OEMCP=936"
set "PYTHONIOENCODING=cp%OEMCP%:backslashreplace"
set "PY="
if exist "py312\python.exe" set "PY=py312\python.exe"
if not defined PY where python >nul 2>&1 && set "PY=python"
if not defined PY where py >nul 2>&1 && set "PY=py -3"
if not defined PY (
  echo [x] 这台机器上找不到任何 Python。
  echo     装一个就能继续：winget install astral-sh.uv   （或去 python.org 装 3.12）
  echo     装好后重新双击本脚本，已经完成的步骤会自动跳过。
  pause
  exit /b 1
)
echo [i] 用 %PY% 启动补齐流程。注意：依赖会装进仓库根的 py312\，不是动这个解释器。
%PY% scripts\setup_runtime.py %*
if errorlevel 1 (
  echo.
  echo [x] 上面最后一条带 x 的就是原因。修好后重跑本脚本即可，流程是幂等的。
  pause
)
exit /b %ERRORLEVEL%
