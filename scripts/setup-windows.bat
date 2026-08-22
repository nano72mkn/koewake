@echo off
rem ---------------------------------------------------------------
rem  koewake setup (Windows) - run this once.
rem
rem  IMPORTANT: Keep this file ASCII-only. See koewake.bat for why.
rem  Japanese guidance is printed by Python at the end.
rem ---------------------------------------------------------------
chcp 65001 >nul
set "PYTHONUTF8=1"
setlocal
cd /d "%~dp0.."

echo ==========================================
echo   koewake setup
echo ==========================================
echo.

where uv >nul 2>&1
if errorlevel 1 (
    echo Installing uv ...
    powershell -ExecutionPolicy Bypass -c "irm https://astral.sh/uv/install.ps1 | iex"
    set "PATH=%USERPROFILE%\.local\bin;%PATH%"
)

where uv >nul 2>&1
if errorlevel 1 (
    echo.
    echo [ERROR] Could not install uv.
    echo Show this screen to the developer.
    pause
    exit /b 1
)

echo.
echo Downloading dependencies. This takes a few minutes ...
uv sync
if errorlevel 1 (
    echo.
    echo [ERROR] Setup failed. Show this screen to the developer.
    pause
    exit /b 1
)

rem Japanese guidance comes from Python, so this file stays ASCII.
uv run koewake --welcome

pause
