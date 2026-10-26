@echo off
rem 复制本文件为 local_env.bat 并填入你自己的密钥（local_env.bat 已被 .gitignore 排除）
rem set DEEPSEEK_API_KEY=sk-xxxxxxxx

rem ==== 局域网直连网关的可选令牌 ====
rem 回环（本机 UI / dsh 反代）永远免令牌；设了它以后，局域网设备直连 7863
rem 必须带 ?token=... 或 X-Yue2-Token。不设则保持旧行为（直连网关无鉴权）。
rem set YUE2_ACCESS_TOKEN=请换成一串足够长的随机字符
