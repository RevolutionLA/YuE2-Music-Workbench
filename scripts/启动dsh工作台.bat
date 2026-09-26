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

rem 无窗口拉起 node（与网关同款）。蓝军 S5/S6：原先用 WMI Win32_Process.Create，
rem WMI 派生进程继承的是 WMI 服务的环境而不是本 cmd 的环境 —— 实测对照同一段探针子进程脚本：
rem WMI 读不到父进程变量，Start-Process -WindowStyle Hidden 读得到。所以那段时间里
rem 上面 call local_env.bat 塞进来的 DEEPSEEK_API_KEY 根本没传到 node，AI 工作台拿不到密钥，
rem 而密钥出现在命令行字符串又是另一回事（命令行全局可见，绝不能用 %DEEPSEEK_API_KEY% 展开）。
rem 现在：需要在 node 侧生效的变量一律用 set 放进本 cmd 环境，由 powershell→node 逐层继承；
rem 仍然不碰 $env:（本机 PowerShell 的 Start-Process 被自定义封装，改 $env: 会把代理变量
rem 按大小写各塞一份进 -Environment 哈希表，键重复直接报错）。
set "DSH_NO_BROWSER=1"
set "YUE2_ROOT=%cd%"
rem 蓝军 N-9：目录/文件名一律走环境变量，不拼进 PowerShell 的单引号字面量。
rem 拼字面量时路径里一个单引号（C:\Users\O'Brien\...）就让用户目录下的整条命令语法崩，
rem 而 catch{exit 1} 把报错吞光，主脚本继续往下走到"工作台未就绪"，根因永远看不到。
set "YUE2_DSH_DIR=%cd%\dsh-plugin"
set "YUE2_DSH_OUT=%cd%\dsh-plugin\_dsh_web.log"
set "YUE2_DSH_ERRLOG=%cd%\dsh-plugin\_dsh_err.log"
set "YUE2_DSH_BIN=node_modules\@deepseek-ai\dsh\lib\bin.js"
powershell -NoProfile -Command "try{Start-Process -WindowStyle Hidden -FilePath $env:NODE_EXE -WorkingDirectory $env:YUE2_DSH_DIR -RedirectStandardOutput $env:YUE2_DSH_OUT -RedirectStandardError $env:YUE2_DSH_ERRLOG -ArgumentList $env:YUE2_DSH_BIN,'web','--port',$env:DSH_PORT,'--no-open' -ErrorAction Stop | Out-Null}catch{exit 1}"
exit /b 0
