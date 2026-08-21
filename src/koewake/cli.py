"""コマンドライン入口。

    koewake 動画.mp4
    koewake 動画1.mp4 動画2.mp4 -o out/

Windows/macOS の D&D スクリプトからもここが呼ばれる。
"""

from __future__ import annotations

import argparse
import gc
import json
import shutil
import sys
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from koewake import __version__
from koewake.audio import MEDIA_SUFFIXES, extract_audio, is_vertical, probe_duration
from koewake.engine import DEFAULT_QUALITY, QUALITY_PRESETS, EngineConfig, resolve_engine
from koewake.progress import Progress
from koewake.subtitle import (
    VERTICAL_LAYOUT,
    LayoutOptions,
    Segment,
    build_cues,
    render_srt,
)
from koewake.transcribe import TranscribeOptions, load_model, load_vocabulary, transcribe


class ModelCache:
    """Whisper のモデルを1回だけ読み込んで使い回す。

    ファイルごとに読み直すと、複数ドロップしたときに毎回 1.5GB を
    読み込むことになる。初回だけダウンロードが走るので、その間も
    「止まっていない」ことが分かるよう進捗表示を出す。
    """

    def __init__(self, engine: EngineConfig, progress: Progress) -> None:
        self.engine = engine
        self.progress = progress
        self._model = None

    def get(self):
        if self._model is None:
            self.progress.start("モデルを準備しています（初回はダウンロードあり）")
            try:
                self._model = load_model(
                    self.engine,
                    on_progress=lambda ratio, detail: self.progress.update(
                        ratio=ratio, detail=detail, label="モデルをダウンロード中"
                    ),
                )
            finally:
                self.progress.finish()
        return self._model


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
    parser.add_argument(
        "--no-progress", action="store_true", help="進捗のアニメーションを出さない"
    )
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


@dataclass
class Session:
    """1回の実行を通して共有するもの。"""

    args: argparse.Namespace
    initial_prompt: str
    models: ModelCache
    progress: Progress


def _transcribe_file(source: Path, session: Session, duration: float | None) -> list[Segment]:
    """音声を取り出して文字起こしする。一時ファイルは必ず片付ける。"""
    progress = session.progress
    work_dir = None
    try:
        progress.start("音声を取り出しています")
        try:
            audio_path = extract_audio(source)
        finally:
            progress.finish()
        work_dir = audio_path.parent

        model = session.models.get()

        # 最初のひとまとまりが返るまでは、進み具合が本当に分からない。
        # 0% のバーを出すと止まって見えるので、それまでは経過時間だけ出す。
        progress.start("音声を解析しています")

        def on_progress(ratio: float, text: str) -> None:
            progress.update(
                ratio=ratio if duration else None, detail=text, label="文字起こし中"
            )

        try:
            return transcribe(
                model,
                audio_path,
                TranscribeOptions(
                    language=session.args.language, initial_prompt=session.initial_prompt
                ),
                duration=duration,
                on_progress=on_progress,
            )
        finally:
            progress.finish()
    finally:
        if work_dir and work_dir.name.startswith("koewake-"):
            shutil.rmtree(work_dir, ignore_errors=True)


def process_one(source: Path, session: Session, position: str = "") -> Outcome:
    args = session.args

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

    layout = resolve_layout(args, source)
    duration = probe_duration(source)

    _log(f"\n=== {position}{source.name} ===")
    _log(f"  エンジン : {session.models.engine.describe()}")
    _log(f"  字幕     : 1行{layout.max_chars_per_line}文字 x 最大{layout.max_lines}行")
    if duration:
        _log(f"  尺       : {_format_seconds(duration)}")

    started = time.monotonic()
    segments = _transcribe_file(source, session, duration)
    if not segments:
        _log("  [失敗] 音声から文字を取れませんでした（無音・BGMのみの可能性）")
        return Outcome(Outcome.FAILED)

    session.progress.start("字幕として整形しています")
    try:
        cues = build_cues(segments, layout)
        srt_path.write_text(render_srt(cues), encoding=ENCODINGS[args.encoding], newline="")
    finally:
        session.progress.finish()

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

    engine = resolve_engine(quality=args.quality, model=args.model, device=args.device)
    progress = Progress(enabled=False if args.no_progress else None)
    session = Session(
        args=args,
        initial_prompt=initial_prompt,
        models=ModelCache(engine, progress),
        progress=progress,
    )

    made: list[Path] = []
    failed = 0
    for index, source in enumerate(sources, start=1):
        position = f"[{index}/{len(sources)}] " if len(sources) > 1 else ""
        try:
            outcome = process_one(source, session, position)
        except KeyboardInterrupt:
            progress.finish()
            _log("\n中断しました。")
            return 130
        except Exception as exc:
            progress.finish()
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

    faster-whisper が使う CTranslate2 は、インタプリタ終了時の後片付けで
    まれにクラッシュすることがある（macOS の `recursive_mutex lock failed`）。
    終了処理が始まる前にモデルを解放しきってしまえば、そこを通らずに済む。

    （以前はここで os._exit していたが、multiprocessing の後始末を飛ばすため
    「leaked semaphore」の警告が毎回出てしまい、かえって不安にさせるのでやめた）
    """
    code = main()
    gc.collect()
    sys.stdout.flush()
    sys.stderr.flush()
    return sys.exit(code)


if __name__ == "__main__":
    run()
