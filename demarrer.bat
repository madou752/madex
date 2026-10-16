@echo off
chcp 65001 >nul
title MADEX
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe ( echo Lance d'abord installer.bat & pause & exit /b 1 )
.venv\Scripts\python.exe main.py %*
pause
