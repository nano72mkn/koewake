"""コマンドライン入口。

    koewake 動画.mp4
    koewake 動画1.mp4 動画2.mp4 -o out/

Windows/macOS の D&D スクリプトからもここが呼ばれる。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from dataclasses import asdict, replace
from pathlib import Path

from koewake import __version__
from koewake.audio import MEDIA_SUFFIXES, extract_audio, is_vertical, probe_duration
from koewake.engine import DEFAULT_QUALITY, QUALITY_PRESETS, resolve_engine
from koewake.subtitle import VERTICAL_LAYOUT, LayoutOptions, build_cues, render_srt
from koewake.transcribe import TranscribeOptions, load_vocabulary, transcribe


class Outcome:
    """1ファイル分の処理結果。

    「対応外の形式なので飛ばした」と「失敗した」を区別する。
    D&D で使うので、後者だけをエラーとして扱いたい。
    """

    MADE = "made"
    SKIPPED = "skipped"
    FAILED = "failed"

    def __init__(self, status: str, path: Path | None = None) -> None:
        self.status = status
        self.path = path


ENCODINGS = {
    # Filmora(Windows) で最も文字化けしにくい。既定。
    "utf-8-sig": "utf-8-sig",
    "utf-8": "utf-8",
    # 古い環境向けの逃げ道
    "shift-jis": "cp932",
}


def _log(message: str) -> None:
    print(message, flush=True)


def _format_seconds(seconds: float) -> str:
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes}分{secs:02d}秒"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="koewake",
        description="動画から日本語の字幕ファイル(SRT)を作ります。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="例: koewake ライブ配信.mp4 --vocab よく使う単語.txt",
    )
    parser.add_argument("inputs", nargs="+", type=Path, help="動画または音声ファイル")
    parser.add_argument("-o", "--output-dir", type=Path, help="SRTの出力先（既定: 入力と同じ場所）")
    parser.add_argument(
        "-q", "--quality",
        choices=sorted(QUALITY_PRESETS),
        default=DEFAULT_QUALITY,
        help=f"精度と速度のバランス（既定: {DEFAULT_QUALITY}）",
    )
    parser.add_argument(
        "--model", help="Whisperのモデル名を直接指定する（--quality より優先）"
    )
    parser.add_argument(
        "--device",
        choices=["auto", "cpu", "cuda"],
        default="auto",
        help="実行デバイス（既定: auto）",
    )
    parser.add_argument("--language", default="ja", help="音声の言語（既定: ja）")
    parser.add_argument(
        "--vocab", type=Path, help="固有名詞リスト（1行1語）。ゲーム名・配信者名などの精度が上がる"
    )
    parser.add_argument(
        "--layout",
        choices=["auto", "horizontal", "vertical"],
        default="auto",
        help="字幕の折り返し幅。auto は動画の縦横から判定（既定: auto）",
    )
    parser.add_argument("--chars-per-line", type=int, help="1行の最大文字数（--layout より優先）")
    parser.add_argument("--max-lines", type=int, help="1枚あたりの最大行数")
    parser.add_argument(
        "--keep-punctuation", action="store_true", help="句読点（、。）を字幕に残す"
    )
    parser.add_argument(
        "--encoding", choices=sorted(ENCODINGS), default="utf-8-sig", help="SRTの文字コード"
    )
    parser.add_argument(
        "--save-transcript", action="store_true", help="文字起こしの生データを .json でも残す"
    )
    parser.add_argument("--overwrite", action="store_true", help="既存のSRTを上書きする")
    parser.add_argument("--version", action="version", version=f"koewake {__version__}")
    return parser


def resolve_layout(args: argparse.Namespace, source: Path) -> LayoutOptions:
    if args.layout == "vertical":
        layout = VERTICAL_LAYOUT
    elif args.layout == "horizontal":
        layout = LayoutOptions()
    else:
        vertical = is_vertical(source)
        layout = VERTICAL_LAYOUT if vertical else LayoutOptions()

    if args.chars_per_line:
        layout = replace(layout, max_chars_per_line=args.chars_per_line)
    if args.max_lines:
        layout = replace(layout, max_lines=args.max_lines)
    if args.keep_punctuation:
        layout = replace(layout, strip_punctuation=False)
    return layout


def collect_inputs(paths: list[Path]) -> list[Path]:
    """ファイル/フォルダ混在で渡されても、扱えるメディアだけを平らに集める。"""
    collected: list[Path] = []
    for path in paths:
        if path.is_dir():
            collected.extend(
                sorted(
                    child
                    for child in path.iterdir()
                    if child.is_file() and child.suffix.lower() in MEDIA_SUFFIXES
                )
            )
        else:
            collected.append(path)
    return collected


def process_one(source: Path, args: argparse.Namespace, initial_prompt: str) -> Outcome:
    if not source.exists():
        _log(f"[エラー] ファイルが見つかりません: {source}")
        return Outcome(Outcome.FAILED)
    if source.suffix.lower() not in MEDIA_SUFFIXES:
        _log(f"[スキップ] 対応していない形式です: {source.name}")
        return Outcome(Outcome.SKIPPED)

    output_dir = args.output_dir or source.parent
    output_dir.mkdir(parents=True, exist_ok=True)
    srt_path = output_dir / f"{source.stem}.srt"
    if srt_path.exists() and not args.overwrite:
        _log(f"[スキップ] すでにSRTがあります（--overwrite で上書き）: {srt_path.name}")
        return Outcome(Outcome.SKIPPED)

    engine = resolve_engine(quality=args.quality, model=args.model, device=args.device)
    layout = resolve_layout(args, source)

    _log(f"\n=== {source.name} ===")
    _log(f"  エンジン : {engine.describe()}")
    _log(f"  字幕     : 1行{layout.max_chars_per_line}文字 x 最大{layout.max_lines}行")

    duration = probe_duration(source)
    if duration:
        _log(f"  尺       : {_format_seconds(duration)}")

    started = time.monotonic()
    work_dir = None
    try:
        _log("  [1/3] 音声を取り出しています...")
        audio_path = extract_audio(source)
        work_dir = audio_path.parent

        _log("  [2/3] 文字起こし中...（初回はモデルのダウンロードで数分かかります）")

        last_shown = -1.0

        def on_progress(ratio: float, text: str) -> None:
            nonlocal last_shown
            percent = ratio * 100
            if percent - last_shown < 2.0:
                return
            last_shown = percent
            print(f"\r      {percent:5.1f}%  {text[:20]}", end="", flush=True)

        segments = transcribe(
            audio_path,
            engine,
            TranscribeOptions(language=args.language, initial_prompt=initial_prompt),
            duration=duration,
            on_progress=on_progress,
        )
        print("\r" + " " * 78, end="\r", flush=True)

        if not segments:
            _log("  [失敗] 音声から文字を取れませんでした（無音・BGMのみの可能性）")
            return Outcome(Outcome.FAILED)

        _log("  [3/3] 字幕として整形しています...")
        cues = build_cues(segments, layout)

        srt_path.write_text(
            render_srt(cues), encoding=ENCODINGS[args.encoding], newline=""
        )

        if args.save_transcript:
            transcript_path = output_dir / f"{source.stem}.transcript.json"
            transcript_path.write_text(
                json.dumps([asdict(segment) for segment in segments], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            _log(f"  文字起こしデータ: {transcript_path.name}")

        elapsed = time.monotonic() - started
        _log(f"  [完了] {srt_path}（字幕 {len(cues)} 枚 / 所要 {_format_seconds(elapsed)}）")
        return Outcome(Outcome.MADE, srt_path)
    finally:
        if work_dir and work_dir.name.startswith("koewake-"):
            shutil.rmtree(work_dir, ignore_errors=True)


def _report(made: list[Path], failed: int) -> None:
    _log("")
    if made:
        _log(f"SRTを {len(made)} 本つくりました。Filmora にドラッグして読み込んでください。")
        for path in made:
            _log(f"  - {path}")
    if failed:
        _log(f"{failed} 本は失敗しました。上のエラーを確認してください。")
    elif not made:
        _log("つくるものがありませんでした。")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    initial_prompt = ""
    if args.vocab:
        if not args.vocab.exists():
            _log(f"[エラー] 単語リストが見つかりません: {args.vocab}")
            return 1
        initial_prompt = load_vocabulary(args.vocab)
        if initial_prompt:
            _log(f"単語リストを読み込みました（{args.vocab.name}）")

    sources = collect_inputs(args.inputs)
    if not sources:
        _log("[エラー] 処理できるファイルがありませんでした。")
        return 1

    made: list[Path] = []
    failed = 0
    for source in sources:
        try:
            outcome = process_one(source, args, initial_prompt)
        except KeyboardInterrupt:
            _log("\n中断しました。")
            return 130
        except Exception as exc:
            failed += 1
            _log(f"  [エラー] {source.name}: {exc}")
            continue

        if outcome.status == Outcome.MADE and outcome.path:
            made.append(outcome.path)
        elif outcome.status == Outcome.FAILED:
            failed += 1

    _report(made, failed)
    return 1 if failed else 0


def run() -> None:
    """コンソールスクリプトの入口。

    faster-whisper が使う CTranslate2 は、終了時の後片付けで
    まれにクラッシュする（macOS の `recursive_mutex lock failed`）。
    SRT を書き終えたあとなので実害は無いが、そのままだと D&D スクリプトが
    成功した実行を「失敗した」と表示してしまう。
    出力を流し切ってから、後片付けを待たずにプロセスを終える。
    （一時ファイルは process_one の finally で消してある）
    """
    code = main()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)


if __name__ == "__main__":
    run()
