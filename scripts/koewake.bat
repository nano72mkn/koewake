@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0.."

if "%~1"=="" (
    echo.
    echo 動画ファイルを、この koewake.bat のアイコンに
    echo ドラッグ＆ドロップしてください。
    echo （まとめて複数ドロップしてもOKです）
    echo.
    pause
    exit /b 1
)

rem 同じフォルダに単語リストがあれば自動で使う（固有名詞の精度が上がる）
set "VOCAB="
if exist "%~dp0単語リスト.txt" set VOCAB=--vocab "%~dp0単語リスト.txt"

where uv >nul 2>&1
if errorlevel 1 set "PATH=%USERPROFILE%\.local\bin;%PATH%"

uv run koewake %VOCAB% %*
set EXITCODE=%errorlevel%

echo.
if not "%EXITCODE%"=="0" (
    echo 何か問題が起きました。この画面の内容をコピーして、開発者に相談してください。
)
pause
exit /b %EXITCODE%
