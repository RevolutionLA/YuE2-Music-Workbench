@echo off
rem [encoding] THIS FILE IS UTF-8 (no BOM) ON PURPOSE, and every .bat it calls has an ASCII
rem [encoding] name. cmd.exe decodes a batch file with the console OEM code page; this machine
rem [encoding] runs the Windows "Beta: UTF-8 worldwide language support" option (OEMCP=65001),
rem [encoding] so GBK bytes were parsed as UTF-8 -> Chinese file names became garbage ->
rem [encoding] `call "...\scripts\<chinese>.bat"` died with "is not recognized" and the window
rem [encoding] flashed shut (measured 2026-10-24: double-clicking did nothing at all).
rem [encoding] UTF-8 content + ASCII call targets works under BOTH 65001 and 936.
rem [encoding] Do NOT re-save as GBK, do NOT rename the targets back to Chinese, and do NOT
rem [encoding] add a mid-file code-page switch.
rem 停止音乐工作台（正式逻辑在 scripts\，此文件仅转调）
call "%~dp0scripts\stop_lab.bat" %*
