@echo off
rem ---------------------------------------------------------------
rem  koewake GPU setup (Windows, NVIDIA only) - optional.
rem
rem  faster-whisper does not ship cuBLAS / cuDNN, so CUDA fails with
rem  "Library cublas64_12.dll is not found". This installs them.
rem  Downloads about 1 GB.
rem
rem  IMPORTANT: Keep this file ASCII-only. See koewake.bat for why.
rem ---------------------------------------------------------------
chcp 65001 >nul
set "PYTHONUTF8=1"
setlocal
cd /d "%~dp0.."

echo ==========================================
echo   koewake GPU setup (NVIDIA)
echo ==========================================
echo.

where uv >nul 2>&1
if errorlevel 1 set "PATH=%USERPROFILE%\.local\bin;%PATH%"

where uv >nul 2>&1
if errorlevel 1 (
    echo [ERROR] uv not found. Run setup-windows.bat first.
    pause
    exit /b 1
)

nvidia-smi >nul 2>&1
if errorlevel 1 (
    echo [WARN] No NVIDIA GPU detected. This setup is probably not needed.
    echo koewake works on CPU without it.
    echo.
)

echo Downloading CUDA libraries (about 1 GB). This takes a while ...
uv sync --extra cuda
if errorlevel 1 (
    echo.
    echo [ERROR] Setup failed. Show this screen to the developer.
    pause
    exit /b 1
)

uv run koewake --welcome

pause
