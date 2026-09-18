@echo off
rem XinQing ops button. ASCII only on purpose -- see the note in install.bat.
rem
rem These buttons are copied to the INSTALL ROOT by install.ps1 and the
rem deploy\ops folder is removed afterwards, so %~dp0 is always the install
rem root. ops.ps1 derives the install directory from its own location, so it
rem does not matter where you double-clicked from.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy\ops.ps1" -Action status
echo.
rem "press any key" is cmd.exe's own localized `pause`, not an echo:
rem pause prints the OS string from a code page that matches the console,
rem while a hand-written UTF-8 echo is mojibake under cp936.
pause >nul
