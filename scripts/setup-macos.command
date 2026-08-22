#!/bin/bash
# koewake セットアップ（最初の1回だけ）
set -u
cd "$(cd "$(dirname "$0")" && pwd)/.." || exit 1

export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

echo "=========================================="
echo "  こえわけ セットアップ"
echo "=========================================="
echo

if ! command -v uv >/dev/null 2>&1; then
    echo "必要なツール (uv) をインストールしています..."
    if ! curl -LsSf https://astral.sh/uv/install.sh | sh; then
        echo
        echo "[エラー] uv のインストールに失敗しました。"
        read -r -p "Enter キーで閉じます "
        exit 1
    fi
    export PATH="$HOME/.local/bin:$PATH"
fi

echo
echo "必要なものをダウンロードしています。数分かかります..."
if ! uv sync; then
    echo
    echo "[エラー] セットアップに失敗しました。この画面の内容をコピーして、開発者に相談してください。"
    read -r -p "Enter キーで閉じます "
    exit 1
fi

# 案内は Python 側（両OSで同じ文言を使う）
uv run koewake --welcome

read -r -p "Enter キーで閉じます "
