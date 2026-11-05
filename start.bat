@echo off
rem Paatti one-click launcher for Windows: double-click this file.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1"
pause
