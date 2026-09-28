@echo off
chcp 65001 >nul
setlocal
set "HERE=%~dp0"
set "USER_ARGS=%*"
title 音乐工作台 - 关闭局域网
rem 双击即可：删掉 3081 的端口转发和它那条防火墙规则，并把 secrets\local_env.bat 里的
rem LAN 名单三行注释掉（改前先备份 local_env.bat.bak），同网段设备就再也连不上。
rem 本机使用不受影响（照旧 http://127.0.0.1:3081）。重新开放：双击 开放局域网.bat。
rem 可选参数：-KeepEnv 只拆转发和防火墙、不动名单；-Restart 顺带重启工作台让名单生效。
rem 注意：%HERE% 必须在任何 shift 之前取好（SHIFT 会连 %0 一起挪走，%~dp0 会变空）。
cd /d "%HERE%.."
net session >nul 2>&1
if not errorlevel 1 goto run
echo 这一步要改端口转发和防火墙，正在请求管理员权限，请在弹窗里点「是」。
powershell -NoProfile -ExecutionPolicy Bypass -Command "if ($env:USER_ARGS) { $a=$env:USER_ARGS.Split(' ',[System.StringSplitOptions]::RemoveEmptyEntries) } else { $a=@() }; Start-Process -FilePath ('%HERE%%~nx0') -Verb RunAs -ArgumentList $a"
if errorlevel 1 (
    echo 授权被取消，未做任何改动。
) else (
    echo 已在管理员窗口里执行，明细也写进了 runtime\data\logs\lan-open.log
)
echo.
pause
endlocal
exit /b 0

:run
rem 名单文件里的三行由脚本自己注释，这里只把它读进来做口径核对
if exist "%HERE%..\secrets\local_env.bat" call "%HERE%..\secrets\local_env.bat"
powershell -NoProfile -ExecutionPolicy Bypass -File "%HERE%开放局域网.ps1" -Elevated -Revoke %USER_ARGS%
echo.
echo 明细日志：runtime\data\logs\lan-open.log
pause
endlocal
exit /b 0
