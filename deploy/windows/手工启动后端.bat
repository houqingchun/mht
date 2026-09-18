@echo off
rem XinQing -- start the BACKEND by hand. ASCII only on purpose: cmd.exe reads .bat
rem with the console code page, so a Chinese character in here can swallow the next
rem ASCII character and turn into a syntax error that names nothing. All the Chinese
rem lives in manual-start.ps1, which PowerShell reads as UTF-8 (with a BOM).
rem
rem Use this when the one-click install did not finish. It builds the runtime
rem environment if it is missing, checks backend\.env, brings the schema up to date,
rem writes the baseline data when the database is empty, and then starts the server
rem IN THIS WINDOW -- closing the window is how you stop it.
rem
rem The schema/database steps come from install.ps1 steps 2-4; that file is the
rem authority. The one deliberate difference: the wipe (reset_to_baseline) asks
rem first here, because this script does not own the database.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0manual-start.ps1" -Action backend
echo.
pause >nul
