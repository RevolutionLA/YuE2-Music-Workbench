@echo off
chcp 65001 >nul
title 音乐工作台
cd /d %~dp0..
rem 本脚本位于 scripts\，所有相对路径均以仓库根为基准

echo ================================================
echo   音乐工作台 一键启动
echo ================================================

set GATEWAY_UP=0
set DSH_UP=0
netstat -ano | findstr ":7863" | findstr "LISTENING" >nul && set GATEWAY_UP=1
netstat -ano | findstr ":3081" | findstr "LISTENING" >nul && set DSH_UP=1

set GRADIO_TEMP_DIR=%cd%\tmp\
set PYTHON_PATH=%cd%\py312\
set PYTHONHOME=
set PYTHONPATH=
set PYTHON_EXECUTABLE=%PYTHON_PATH%\python.exe
set FFMPEG_PATH=%cd%\py312\ffmpeg\bin
set SOX_PATH=%cd%\py312\sox-14-4-2
set TORCH_HOME=%cd%\cache
set HF_ENDPOINT=https://hf-mirror.com
set HF_HOME=%cd%\hf_download
set NO_PROXY=127.0.0.1,localhost,::1
set no_proxy=127.0.0.1,localhost,::1
rem 静默启动器调用时禁止网关自行开浏览器（由 vbs 统一开 3081）
if "%~1"=="no-open" (echo > "%TEMP%\_lab_no_open.marker" && set "_LAB_NO_OPEN_MARKER=1") else (del /q "%TEMP%\_lab_no_open.marker" 2>nul)
set CU_PATH=%PYTHON_PATH%\Lib\site-packages\torch\lib
set CUDA_HOME=%PYTHON_PATH%\Library
set PATH=%PYTHON_PATH%;%PYTHON_PATH%\Scripts;%FFMPEG_PATH%;%SOX_PATH%;%PATH%

rem 密钥从本地未入库文件读取（参考 secrets/local_env.example.bat 创建自己的 local_env.bat）
if exist "%~dp0..\secrets\local_env.bat" call "%~dp0..\secrets\local_env.bat"
rem 首次运行：模型缺失时自动从大陆镜像下载（约2.7GB，支持断点续传）
"%~dp0..\py312\python.exe" "%~dp0..\scripts\download_models.py" --check >nul 2>&1
if errorlevel 1 (
    echo.
    echo   [首次运行] 检测到模型缺失，开始自动下载 YuE2 模型（大陆镜像加速）...
    echo   下载约 2.7GB，支持断点续传，中断后重新运行本脚本即可继续
    "%~dp0..\py312\python.exe" "%~dp0..\scripts\download_models.py"
    if errorlevel 1 (
        echo   模型下载失败，请检查网络后重新运行本脚本
        pause
        exit /b 1
    )
)

if "%GATEWAY_UP%"=="1" goto watchdog
echo [1/3] 启动网关 :7863（WMI 无窗口启动）...
powershell -NoProfile -Command "$sw=([wmiclass]'Win32_ProcessStartup').CreateInstance(); $sw.ShowWindow=0; $p=([wmiclass]'Win32_Process').Create('%cd%\py312\python.exe -s %cd%\app.py','%cd%',$sw); if($p.ReturnValue -ne 0){exit 1}"

:watchdog
rem 看门狗：网关假死（health 无响应）自动重启；pythonw 无窗口，日志写 watchdog.log
tasklist /FI "IMAGENAME eq pythonw.exe" /V 2>nul | findstr /C:"watchdog" >nul
if not errorlevel 1 goto dsh
if exist "_watchdog.pid" (
  powershell -NoProfile -Command "if(Get-Process -Id (Get-Content '_watchdog.pid' -ErrorAction SilentlyContinue) -ErrorAction SilentlyContinue){exit 1}else{exit 0}" >nul 2>&1
  if not errorlevel 1 del /q "_watchdog.pid" 2>nul
)
if exist "_watchdog.pid" (
  echo [2/3] 看门狗已在运行，跳过
  goto dsh2
)
echo [2/3] 启动网关看门狗（假死自愈，无窗口）...
powershell -NoProfile -Command "$p = Start-Process -WindowStyle Hidden '%PYTHON_PATH%pythonw.exe' -WorkingDirectory '%cd%' -ArgumentList 'watchdog.py' -PassThru; $p.Id | Out-File -Encoding ascii '_watchdog.pid'"
goto dsh2
:dsh
echo [2/3] 看门狗已在运行，跳过
:dsh2
if "%DSH_UP%"=="1" goto wait
echo [3/3] 启动 dsh AI 工作台 :3081 ...
if not exist "dsh-plugin\_dsh_home" mkdir "dsh-plugin\_dsh_home"
powershell -NoProfile -Command "$env:DSH_HOME='%cd%\dsh-plugin\_dsh_home'; $env:DSH_NO_BROWSER='1'; $env:DEEPSEEK_API_KEY='%DEEPSEEK_API_KEY%'; Start-Process -WindowStyle Hidden node -WorkingDirectory '%cd%\dsh-plugin' -ArgumentList 'node_modules\@deepseek-ai\dsh\lib\bin.js','web','--port','3081','--no-open' -RedirectStandardOutput '%cd%\dsh-plugin\_dsh_web.log' -RedirectStandardError '%cd%\dsh-plugin\_dsh_err.log'"

:wait
set /a TRIES=0
:waitloop
ping -n 3 127.0.0.1 >nul
"%SystemRoot%\System32\curl.exe" -s --noproxy "*" -m 3 http://127.0.0.1:7863/api/health >nul 2>&1
if not errorlevel 1 goto ready
set /a TRIES+=1
if %TRIES% geq 20 goto ready
echo     等待网关就绪... %TRIES%/20
goto waitloop

:ready
echo.
echo ================================================
echo   全部就绪！
echo   工作台:  http://127.0.0.1:7863
echo   AI 助手: 页面内 "AI 工作台" 页签
echo ================================================
rem 等待 dsh web 就绪后，打开带 token 的 3081 地址（无 token 则回退 7863）
set /a DSH_TRIES=0
:dshwait
ping -n 3 127.0.0.1 >nul
set "DSH_URL="
for /f "tokens=*" %%u in ('powershell -NoProfile -Command "if(Test-Path 'dsh-plugin\_dsh_web.log'){Select-String -Path 'dsh-plugin\_dsh_web.log' -Pattern 'http://127\.0\.0\.1:3081/\?token=\S+' | ForEach-Object { $_.Matches[0].Value } | Select-Object -Last 1}" 2^>nul') do set "DSH_URL=%%u"
if defined DSH_URL goto open
set /a DSH_TRIES+=1
if %DSH_TRIES% lss 15 goto dshwait
:open
rem 被 vbs 静默启动器调用时带 no-open 参数：由 vbs 负责打开浏览器（隐藏窗口里 start 不可靠）
if "%~1"=="no-open" exit /b 0
rem 只打开 3081 工作台；token 未就绪时静默放弃，绝不弹 7863 迷惑用户
if defined DSH_URL (
    echo   打开 AI 工作台: %DSH_URL%
    start "" "%DSH_URL%"
) else (
    echo   dsh token 未就绪，请手动打开 3081 工作台页面
)
exit /b 0
