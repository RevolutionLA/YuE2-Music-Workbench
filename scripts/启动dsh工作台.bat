@echo off
chcp 65001 >nul
rem 仅启动 dsh 工作台 UI（端口默认 3081，读 ports.json）。
rem 主启动脚本（启动音乐工作台.bat）与看门狗（watchdog.py）共用本文件，
rem 避免同一份启动参数在两个地方各写一遍、改一处漏一处。
cd /d %~dp0..

rem ---- 端口：唯一真源 ports.json ----
set "DSH_PORT=3081"
for /f "usebackq tokens=*" %%p in (`powershell -NoProfile -Command "try{$j=Get-Content '%cd%\ports.json' -Raw | ConvertFrom-Json; if($j.dsh){$j.dsh}else{'3081'}}catch{'3081'}" 2^>nul`) do set "DSH_PORT=%%p"

rem ---- node 定位：优先 PATH，退回常见安装位 ----
set "NODE_EXE=node"
where node >nul 2>&1
if errorlevel 1 if exist "C:\Program Files\nodejs\node.exe" set "NODE_EXE=C:\Program Files\nodejs\node.exe"

set "DSH_HOME=%cd%\dsh-plugin\_dsh_home"
if not exist "%DSH_HOME%" mkdir "%DSH_HOME%"
rem 仓库根：dsh 会把插件拷到 _dsh_home 下再加载，插件自己推不出真实路径，
rem 这里显式注入，UI 静态托管才能找到 static/index.html
rem 密钥从本地未入库文件读取（参考 secrets\local_env.example.bat 创建自己的 local_env.bat）
if exist "%cd%\secrets\local_env.bat" call "%cd%\secrets\local_env.bat"

rem 用 WMI Win32_Process.Create 无窗口拉起（与网关同款），并显式注入所需环境变量。
rem 注意：本机 PowerShell 的 Start-Process 被自定义封装，一旦在 -Command 里修改 $env:
rem 就会把代理变量按大小写各塞一份进 -Environment 哈希表导致键重复而报错；
rem 故此处不碰 $env:，改用 cmd /c "set ... &&" 在子进程里设环境，规避该坑。
powershell -NoProfile -Command "$sw=([wmiclass]'Win32_ProcessStartup').CreateInstance(); $sw.ShowWindow=0; $cmd='cmd /c set DSH_HOME=%cd%\dsh-plugin\_dsh_home&& set DSH_NO_BROWSER=1&& set YUE2_ROOT=%cd%&& set DEEPSEEK_API_KEY=%DEEPSEEK_API_KEY%&& cd %cd%\dsh-plugin&& %NODE_EXE% node_modules\@deepseek-ai\dsh\lib\bin.js web --port %DSH_PORT% --no-open > %cd%\dsh-plugin\_dsh_web.log 2> %cd%\dsh-plugin\_dsh_err.log'; $p=([wmiclass]'Win32_Process').Create($cmd,'%cd%',$sw); if($p.ReturnValue -ne 0){exit 1}"
exit /b 0
