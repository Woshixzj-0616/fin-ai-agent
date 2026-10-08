@echo off
chcp 65001 >nul
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_module.ps1" -Module assets -Port 8103
if errorlevel 1 pause
