@echo off
chcp 65001 >nul
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0启动模块四现金流工作台.ps1"
if errorlevel 1 pause
