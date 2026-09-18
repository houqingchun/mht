@echo off
rem XinQing -- start the FRONTEND by hand, on port 5173. ASCII only on purpose:
rem cmd.exe reads .bat with the console code page, so a Chinese character in here can
rem swallow the next ASCII character and become a syntax error that names nothing.
rem (The filename is Chinese; the bytes inside are not.)
rem
rem This window serves frontend\dist AND forwards /api/** to the backend. It has to
rem do both: the frontend calls the API with a relative path ('/api/v1'), so the page
rem and the API must share one origin. A plain 'python -m http.server' would open the
rem login page and then fail every request with "Unexpected token '<'".
rem
rem Start the backend first (the other .bat next to this one). This window reuses
rem the same Python environment, so it needs that one to have run at least once.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0manual-start.ps1" -Action frontend
echo.
pause >nul
