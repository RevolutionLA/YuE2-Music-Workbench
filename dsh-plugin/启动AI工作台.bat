@echo off
REM 启动内嵌 DeepSeek Harness AI 工作台（独立 DSH_HOME，端口 3081，不与本地 DSH 的 3080 冲突）
cd /d %~dp0
set "DSH_HOME=%~dp0_dsh_home"
rem 密钥从本地未入库文件读取（见仓库根 secrets/local_env.example.bat）
if exist "%~dp0..\secrets\local_env.bat" call "%~dp0..\secrets\local_env.bat"
node node_modules\@deepseek-ai\dsh\lib\bin.js web --port 3081
