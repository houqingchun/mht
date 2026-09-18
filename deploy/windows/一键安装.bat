@echo off
rem XinQing one-click installer. Double-click this file.
rem
rem This file is intentionally pure ASCII: cmd.exe decodes a .bat with the
rem console code page, so Chinese in here would depend on the machine's locale,
rem and a UTF-8 BOM would break the very first line. All Chinese lives in
rem deploy\install.ps1, which PowerShell 5.1 reads as UTF-8 because it has a BOM.
rem
rem Do NOT add "chcp 65001": PowerShell 5.1 writes Chinese through WriteConsoleW
rem (the Unicode console API), so it is correct regardless of code page -- and
rem chcp 65001 is exactly the combination that garbles PS 5.1 output when it is
rem redirected into the log file.
rem
rem The "press any key" line is cmd.exe's own `pause`, NOT an `echo`: pause
rem prints the OS-localized prompt from a code page that actually matches the
rem console, so a Chinese Windows shows it in Chinese for free. A hand-written
rem UTF-8 echo here would be mojibake under cp936 -- and the trailing byte of a
rem Chinese character can even swallow the next ASCII character, so such a line
rem is not merely ugly, it can be a syntax error.
rem
rem Hence the rule for this whole file, and it is checkable with one byte scan:
rem EVERY byte must be < 128. That includes comments: a Chinese word inside a
rem rem-line is harmless to cmd, but it makes the one-scan check above useless.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy\install.ps1"
echo.
pause >nul
