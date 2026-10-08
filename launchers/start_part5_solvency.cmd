@echo off
chcp 65001 >nul
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_module.ps1" -Module solvency -Port 8105
if errorlevel 1 pause
