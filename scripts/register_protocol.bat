@echo off
rem ============================================================
rem  注册 yue2workbench:// 自定义协议（一次即可，之后可删本脚本）
rem  作用：前端页面检测到后端离线时，点「离线」按钮即可通过
rem        yue2workbench:// 协议唤起「启动音乐工作台.bat」
rem  写入 HKCU（当前用户），不需要管理员权限。
rem ============================================================
setlocal
set "BAT_DIR=%~dp0"
set "START_BAT=%BAT_DIR%启动音乐工作台.bat"

if not exist "%START_BAT%" (
  echo [错误] 未找到启动脚本：%START_BAT%
  echo 请将本脚本放在 scripts\ 目录下再运行。
  pause
  exit /b 1
)

rem Windows 要求协议命令带引号包裹完整路径，%%1 为占位符
set "CMD=\"%%1\""
reg add "HKCU\Software\Classes\yue2workbench" /ve /d "URL:YuE2 Music Workbench" /f
reg add "HKCU\Software\Classes\yue2workbench" /v "URL Protocol" /d "" /f
reg add "HKCU\Software\Classes\yue2workbench\shell\open\command" /ve /d "cmd /c \"\"%START_BAT%\" %%1\"" /f

if %errorlevel%==0 (
  echo [成功] 协议 yue2workbench:// 已注册，指向：%START_BAT%
) else (
  echo [失败] 注册未成功，errorlevel=%errorlevel%
)
pause
