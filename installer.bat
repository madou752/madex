@echo off
chcp 65001 >nul
title Installation / mise a jour de MADEX
cd /d "%~dp0"
echo.
echo   ███╗   ███╗ █████╗ ██████╗ ███████╗██╗  ██╗
echo   ████╗ ████║██╔══██╗██╔══██╗██╔════╝╚██╗██╔╝
echo   ██╔████╔██║███████║██║  ██║█████╗   ╚███╔╝
echo   ██║╚██╔╝██║██╔══██║██║  ██║██╔══╝   ██╔██╗
echo   ██║ ╚═╝ ██║██║  ██║██████╔╝███████╗██╔╝ ██╗
echo   ╚═╝     ╚═╝╚═╝  ╚═╝╚═════╝ ╚══════╝╚═╝  ╚═╝
echo   Madou's Automated Development EXecutive
echo.

rem --- Mise a jour : on ferme Madex s'il tourne (sinon Windows bloque certains fichiers) ---
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python*' -and ($_.CommandLine -like '*main.py*' -or $_.CommandLine -like '*madex.orb*') -and $_.CommandLine -like '*%~dp0*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" >nul 2>nul
if exist .venv\Scripts\python.exe ( echo   Mise a jour de MADEX : ta cle, tes regles et sa memoire sont conservees. & echo. )

rem --- Claude Code : dans le PATH, ou aux emplacements habituels de l'installeur officiel ---
set "CLAUDE_OK="
where claude >nul 2>nul && set "CLAUDE_OK=1"
if not defined CLAUDE_OK for %%P in ("%USERPROFILE%\.local\bin\claude.exe" "%APPDATA%\npm\claude.cmd" "%USERPROFILE%\.claude\local\claude.exe" "%LOCALAPPDATA%\Programs\claude\claude.exe") do (
  if exist %%P ( set "CLAUDE_OK=1" & echo   Claude Code trouve : %%~P )
)
if not defined CLAUDE_OK (
  echo [!] Claude Code est introuvable. L'installation continue, mais le cerveau de Madex ne marchera pas
  echo     tant que Claude Code n'est pas installe : https://code.claude.com  puis lance  claude  pour te connecter.
  echo.
)
where git >nul 2>nul || ( echo [!] Git for Windows est introuvable. Installe-le d'abord. & pause & exit /b 1 )
where py >nul 2>nul || ( echo [!] Python introuvable. Installe Python 3.12 depuis python.org ^(coche "Add to PATH"^). & pause & exit /b 1 )

echo [1/5] Environnement Python...
if not exist .venv\Scripts\python.exe (
  py -3.12 -m venv .venv 2>nul || py -3.11 -m venv .venv || ( echo [!] Python 3.11 ou 3.12 requis. & pause & exit /b 1 )
)

echo [2/5] Dependances ^(quelques minutes^)...
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip >nul
pip install -r requirements.txt || ( echo [!] Echec de l'installation. & pause & exit /b 1 )

echo [3/5] Modeles de reconnaissance vocale ^(~600 Mo, une seule fois^)...
python -c "from faster_whisper import WhisperModel; WhisperModel('base', compute_type='int8'); WhisperModel('small', compute_type='int8'); print('    OK')"

echo [4/5] Voix ElevenLabs
python -c "from madex.secrets_store import get; raise SystemExit(0 if get('ELEVENLABS_API_KEY') else 1)" && ( echo     Cle deja enregistree. & goto raccourcis )
echo     Colle ta cle API ElevenLabs ^(elevenlabs.io ^> profil ^> API Keys^),
echo     ou appuie sur Entree pour garder la voix gratuite de secours.
python -c "from getpass import getpass; from madex.secrets_store import set_value; k = getpass('    Cle (masquee) : ').strip(); k and set_value('ELEVENLABS_API_KEY', k); print('    Cle enregistree dans .env' if k else '    Voix de secours conservee')"

:raccourcis

echo [5/5] Raccourcis...
powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\MADEX.lnk'); $s.TargetPath='%~dp0.venv\Scripts\pythonw.exe'; $s.Arguments='\"%~dp0main.py\"'; $s.WorkingDirectory='%~dp0'; $s.IconLocation='%~dp0assets\madex.ico'; $s.Description='MADEX (Ctrl+Alt+M)'; $s.Hotkey='CTRL+ALT+M'; $s.Save(); Write-Host ('    Raccourci MADEX cree sur le Bureau : ' + [Environment]::GetFolderPath('Desktop'))"
echo     Raccourci clavier : Ctrl+Alt+M lance Madex ou fait apparaitre sa fenetre.
echo     Secours : double-clic sur MADEX.bat dans ce dossier.
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\MADEX.lnk"
if exist "%STARTUP%" goto autostart
choice /c ON /m "    Lancer MADEX automatiquement a l'ouverture de Windows"
if errorlevel 2 goto fin
:autostart
powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Startup')+'\MADEX.lnk'); $s.TargetPath='%~dp0.venv\Scripts\pythonw.exe'; $s.Arguments='\"%~dp0main.py\" --fond'; $s.WorkingDirectory='%~dp0'; $s.IconLocation='%~dp0assets\madex.ico'; $s.Description='MADEX'; $s.Save()"
echo     OK : MADEX demarrera en fond avec Windows. Dis « Madex » pour faire apparaitre sa fenetre.

:fin
echo.
echo === Termine ===
echo   Test du micro et de la voix : demarrer.bat --test-voix
echo   Lancement                   : raccourci MADEX sur le Bureau, Ctrl+Alt+M, ou MADEX.bat
echo   Vos reglages et vos cles sont dans %LOCALAPPDATA%\MADEX : une mise a jour ne les efface jamais.
pause
