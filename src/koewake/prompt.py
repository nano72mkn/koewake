"""対話でたずねる部分。

**なぜ Python 側にあるか**

Windows の `.bat` に日本語を書くと、UTF-8 で保存したファイルを cmd.exe が
CP932 として読むため文字化けし、その断片をコマンドとして実行してしまう
（`'○○' は、内部コマンドまたは外部コマンドとして認識されていません`）。

そのため `.bat` は起動するだけにして、画面に出す文言や入力の受け付けは
すべてここに置く。結果として Windows と macOS で同じコードが使える。
"""

from __future__ import annotations

import shlex
import sys
from pathlib import Path


def _ask(question: str) -> str:
    """1行たずねる。入力が閉じている（パイプ実行など）なら空文字を返す。"""
    try:
        return input(question).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return ""


def parse_dropped_paths(text: str) -> list[Path]:
    """ターミナルにドラッグして貼り付けられたパスを分解する。

    macOS は空白を `\\` で、Windows は `"` で囲って貼り付けるので、両方を解く。
    """
    text = text.strip()
    if not text:
        return []

    try:
        parts = shlex.split(text, posix=not sys.platform.startswith("win"))
    except ValueError:
        # 引用符が閉じていないなど。そのまま1つのパスとして扱う。
        parts = [text]

    paths = []
    for part in parts:
        cleaned = part.strip().strip('"').strip("'")
        if cleaned:
            paths.append(Path(cleaned))
    return paths


def ask_inputs() -> list[Path]:
    """処理する動画をたずねる（ドラッグ＆ドロップ用）。"""
    print("動画ファイルを、このウィンドウにドラッグして Enter を押してください。")
    print("（複数まとめてドラッグしてもOK / 何も入れずに Enter で終了）")
    print()
    return parse_dropped_paths(_ask("動画: "))


def ask_speakers() -> str | None:
    """話者ごとに分けるかどうかをたずねる。

    `--speakers` に渡す文字列を返す。分けないなら None。
    """
    print()
    print("話者ごとに別々のSRTに分けますか？")
    print("  そのまま Enter → 分けない（1本のSRT）")
    print("  人数を入力     → その人数で分ける（例: 2）")
    print("  a              → 人数もおまかせで判定")
    print()
    print("※ マイクをトラックごとに分けて録っている動画は、")
    print("   ここで何を選んでもトラックごとに分かれます。")
    print()

    answer = _ask("話者: ")
    if not answer:
        return None
    if answer.lower() in ("a", "auto", "自動"):
        return "auto"
    return answer


def print_welcome() -> None:
    """セットアップが終わったあとの案内。

    セットアップスクリプト（`.bat` / `.command`）から呼ばれる。
    日本語をここに置くことで、`.bat` を ASCII だけに保てる。
    """
    launcher = "koewake.bat" if sys.platform.startswith("win") else "koewake.command"
    print()
    print("=" * 42)
    print("  セットアップが終わりました。")
    print()
    print(f"  これからは、動画ファイルを {launcher} に")
    print("  ドラッグ＆ドロップしてください。")
    print("=" * 42)
