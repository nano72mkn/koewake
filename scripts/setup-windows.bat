@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0.."

echo ==========================================
echo   こえわけ セットアップ（最初の1回だけ）
echo ==========================================
echo.

where uv >nul 2>&1
if errorlevel 1 (
    echo 必要なツール ^(uv^) をインストールしています...
    powershell -ExecutionPolicy Bypass -c "irm https://astral.sh/uv/install.ps1 | iex"
    set "PATH=%USERPROFILE%\.local\bin;%PATH%"
)

where uv >nul 2>&1
if errorlevel 1 (
    echo.
    echo [エラー] uv のインストールに失敗しました。
    echo この画面の内容をコピーして、開発者に相談してください。
    pause
    exit /b 1
)

echo.
echo 必要なものをダウンロードしています。数分かかります...
uv sync
if errorlevel 1 (
    echo.
    echo [エラー] セットアップに失敗しました。この画面の内容をコピーして、開発者に相談してください。
    pause
    exit /b 1
)

echo.
echo ==========================================
echo   セットアップ完了！
echo.
echo   これからは、動画ファイルを
echo   koewake.bat にドラッグ＆ドロップしてください。
echo ==========================================
pause
