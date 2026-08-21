#!/bin/bash
# こえわけ セットアップ（最初の1回だけ）
set -u
cd "$(dirname "$0")/.." || exit 1

export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

echo "=========================================="
echo "  こえわけ セットアップ（最初の1回だけ）"
echo "=========================================="
echo

if ! command -v uv >/dev/null 2>&1; then
    echo "必要なツール (uv) をインストールしています..."
    curl -LsSf https://astral.sh/uv/install.sh | sh || {
        echo
        echo "[エラー] uv のインストールに失敗しました。"
        read -r -p "Enter キーで閉じます "
        exit 1
    }
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

echo
echo "=========================================="
echo "  セットアップ完了！"
echo
echo "  これからは koewake.command を開いて、"
echo "  動画ファイルをウィンドウにドラッグしてください。"
echo "=========================================="
read -r -p "Enter キーで閉じます "
