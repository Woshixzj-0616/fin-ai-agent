@echo off
chcp 65001 >nul
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0启动模块五工作台.ps1"
if errorlevel 1 pause
