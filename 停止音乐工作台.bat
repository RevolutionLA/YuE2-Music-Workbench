@echo off
rem [encoding] THIS FILE IS GBK (OEM cp936) ON PURPOSE. cmd.exe parses a batch file with the
rem [encoding] console's OEM codepage; a UTF-8 batch under cp936 splits lines mid-character
rem [encoding] and some commands silently stop running (not just a display issue). Do NOT
rem [encoding] re-save as UTF-8 and do NOT add a mid-file code-page switch.
rem 停止音乐工作台（正式逻辑在 scripts\，此文件仅转调）
call "%~dp0scripts\停止音乐工作台.bat" %*
