@echo off
rem ---------------------------------------------------------------
rem  koewake launcher (Windows)
rem
rem  IMPORTANT: Keep this file ASCII-only.
rem  cmd.exe reads .bat as CP932 on Japanese Windows, so UTF-8
rem  Japanese text gets mangled and the garbage is executed as
rem  commands. All Japanese messages live in Python (koewake/prompt.py).
rem ---------------------------------------------------------------
chcp 65001 >nul
set "PYTHONUTF8=1"
setlocal
cd /d "%~dp0.."

where uv >nul 2>&1
if errorlevel 1 set "PATH=%USERPROFILE%\.local\bin;%PATH%"

where uv >nul 2>&1
if errorlevel 1 (
    echo [ERROR] uv not found. Run setup-windows.bat first.
    pause
    exit /b 1
)

rem The word list (if any) is picked up by Python, so that this
rem file needs no Japanese file names.
uv run koewake --ask-speakers %*
set EXITCODE=%errorlevel%

pause
exit /b %EXITCODE%
