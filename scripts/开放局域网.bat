@echo off
chcp 65001 >nul
setlocal
set "HERE=%~dp0"
set "USER_ARGS=%*"
title 音乐工作台 · 开放局域网
rem 双击即可：自动弹一次 UAC（要改端口转发和防火墙），然后在窗口里只打印要点
rem 和「给局域网其它电脑的地址（含 token）」，同时复制到剪贴板。
rem 带时间戳的明细不再刷屏，统统写进 runtime\data\logs\lan-open.log。
rem 收回：同目录的 关闭局域网.bat。
rem 可选参数：-Restart 顺带重启工作台让名单立刻生效（会打断正在跑的任务）；
rem           -Preview 只打印地址，什么都不改（这条不需要管理员，不弹 UAC）。
rem 注意：%HERE% 必须在任何 shift 之前取好——SHIFT 会把 %0 也一起挪走，
rem 之后 %~dp0 就变成空，ps1 路径会被当成相对路径解析到仓库根（实测踩过）。
cd /d "%HERE%.."
net session >nul 2>&1
if not errorlevel 1 goto run
echo %USER_ARGS%|findstr /i /c:"-Preview" >nul && goto run
echo 这一步要改端口转发和防火墙，请在弹窗里点「是」，地址会打在另一个窗口里。
powershell -NoProfile -ExecutionPolicy Bypass -Command "if ($env:USER_ARGS) { $a=$env:USER_ARGS.Split(' ',[System.StringSplitOptions]::RemoveEmptyEntries) } else { $a=@() }; Start-Process -FilePath ('%HERE%%~nx0') -Verb RunAs -ArgumentList $a"
if errorlevel 1 (
    echo 授权被取消，未做任何改动。
    echo.
    pause
) else (
    rem 提权窗口才是干活的那个，这个启动窗口 4 秒后自己关掉，不留一地黑窗
    timeout /t 4 /nobreak >nul
)
endlocal
exit /b 0

:run
rem 名单（YUE2_LAN_HOSTS 等）来自本地未入库文件，脚本用它核对口径是否对得上
if exist "%HERE%..\secrets\local_env.bat" call "%HERE%..\secrets\local_env.bat"
powershell -NoProfile -ExecutionPolicy Bypass -File "%HERE%开放局域网.ps1" -Elevated %USER_ARGS%
echo.
echo   按任意键关闭本窗口
pause >nul
endlocal
exit /b 0
