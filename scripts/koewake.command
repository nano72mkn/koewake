#!/bin/bash
# 動画をドラッグして SRT を作る（macOS 用）
set -u
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR/.." || exit 1

export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

if ! command -v uv >/dev/null 2>&1; then
    echo "[エラー] 先に setup-macos.command を実行してください。"
    read -r -p "Enter キーで閉じます "
    exit 1
fi

# 同じフォルダに単語リストがあれば自動で使う（固有名詞の精度が上がる）
VOCAB_ARGS=()
if [ -f "$SCRIPT_DIR/単語リスト.txt" ]; then
    VOCAB_ARGS=(--vocab "$SCRIPT_DIR/単語リスト.txt")
fi

echo "=========================================="
echo "  こえわけ"
echo "=========================================="
echo
echo "動画ファイルを、このウィンドウにドラッグして Enter を押してください。"
echo "（複数まとめてドラッグしてもOK / 何も入れずに Enter で終了）"
echo
printf "動画: "
IFS= read -r dropped

if [ -z "$dropped" ]; then
    echo "終了します。"
    exit 0
fi

# Terminal はドラッグしたパスの空白を \ でエスケープして貼り付ける。
# eval でその引用を解いて、複数ファイルを配列に戻す。
eval "paths=($dropped)"

if [ ${#paths[@]} -eq 0 ]; then
    echo "ファイルが読み取れませんでした。終了します。"
    read -r -p "Enter キーで閉じます "
    exit 1
fi

# macOS の bash は 3.2 なので、空配列を "${a[@]}" で展開すると set -u で落ちる。
uv run koewake ${VOCAB_ARGS[@]+"${VOCAB_ARGS[@]}"} "${paths[@]}"
status=$?

echo
if [ $status -ne 0 ]; then
    echo "何か問題が起きました。この画面の内容をコピーして、開発者に相談してください。"
fi
read -r -p "Enter キーで閉じます "
exit $status
