#!/bin/bash
# koewake launcher (macOS)
#
# 画面に出す文言と入力の受け付けは Python 側（koewake/prompt.py）にある。
# Windows の .bat が日本語を持てない事情に合わせ、両OSで同じ動きにするため。
set -u
cd "$(cd "$(dirname "$0")" && pwd)/.." || exit 1

export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

if ! command -v uv >/dev/null 2>&1; then
    echo "[エラー] 先に setup-macos.command を実行してください。"
    read -r -p "Enter キーで閉じます "
    exit 1
fi

uv run koewake --ask-speakers "$@"
status=$?

echo
read -r -p "Enter キーで閉じます "
exit $status
