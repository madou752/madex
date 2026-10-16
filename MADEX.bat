@echo off
rem Lancement de secours de MADEX (sans console). Si Madex tourne deja, sa fenetre apparait.
cd /d "%~dp0"
if not exist .venv\Scripts\pythonw.exe ( echo Lance d'abord installer.bat & pause & exit /b 1 )
start "" ".venv\Scripts\pythonw.exe" "%~dp0main.py"
