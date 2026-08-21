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

echo.
echo 話者ごとに別々のSRTに分けますか？
echo   そのまま Enter =^> 分けない（1本のSRT）
echo   人数を入力     =^> その人数で分ける（例: 2）
echo   a              =^> 人数もおまかせで判定
echo.
set "ANSWER="
set /p "ANSWER=話者: "

rem 人数が分かっているなら a より数字のほうが確実（3人以上だと差が出る）
set "SPEAKERS="
if not "%ANSWER%"=="" (
    if /i "%ANSWER%"=="a" (
        set "SPEAKERS=--speakers auto"
    ) else (
        set "SPEAKERS=--speakers %ANSWER%"
    )
)

uv run koewake %SPEAKERS% %VOCAB% %*
set EXITCODE=%errorlevel%

echo.
if not "%EXITCODE%"=="0" (
    echo 何か問題が起きました。この画面の内容をコピーして、開発者に相談してください。
)
pause
exit /b %EXITCODE%
