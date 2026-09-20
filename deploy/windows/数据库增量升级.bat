@echo off
rem ============================================================================
rem  Database incremental upgrade (V1.0.0 -> V1.1.2).
rem
rem  THIS FILE MUST STAY PURE ASCII. The file NAME is Chinese, the CONTENT is
rem  not -- cmd.exe reads a .bat in the console code page (cp936 on a Chinese
rem  Windows), and a UTF-8 BOM would break the very first line (@echo off).
rem  Every Chinese message lives in manual-migrate.ps1, which PowerShell reads
rem  as UTF-8 with a BOM.
rem
rem  It runs the same two steps as step 4 of the one-click installer:
rem      python -m app.db.ensure_schema
rem      python -m alembic upgrade head
rem  See the header of manual-migrate.ps1 for why that order matters.
rem
rem  Never pass %* here: this button has exactly one job (upgrade the database),
rem  so it hardcodes no -Action. -InstallDir exists for a human running
rem  manual-migrate.ps1 by hand, not for this button.
rem ============================================================================

cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0manual-migrate.ps1"

echo.
pause >nul
