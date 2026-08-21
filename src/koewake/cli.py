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
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

from koewake import __version__
from koewake.audio import MEDIA_SUFFIXES, extract_audio, is_vertical, probe_duration
from koewake.diarize import DiarizationUnavailable, assign_speakers, diarize
from koewake.engine import DEFAULT_QUALITY, QUALITY_PRESETS, EngineConfig, resolve_engine
from koewake.progress import Progress
from koewake.subtitle import (
    VERTICAL_LAYOUT,
    Cue,
    LayoutOptions,
    Segment,
    build_cues_by_speaker,
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

    def __init__(self, status: str, paths: list[Path] | None = None) -> None:
        self.status = status
        self.paths = paths or []


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
    parser.add_argument(
        "--speakers",
        metavar="auto|人数",
        help="話者ごとに別々のSRTを出す。人数が分かっていれば数字で指定する（例: --speakers 2）",
    )
    parser.add_argument(
        "--speaker-names",
        metavar="名前,名前",
        help="話者につける名前をカンマ区切りで（喋り始めた順）。例: --speaker-names ホスト,ゲスト",
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
    # None = 話者分離をしない / 0 = 人数は自動判定 / 1以上 = その人数
    speakers: int | None = None
    speaker_names: list[str] = field(default_factory=list)


def _extract(source: Path, session: Session) -> Path:
    session.progress.start("音声を取り出しています")
    try:
        return extract_audio(source)
    finally:
        session.progress.finish()


def _transcribe_audio(
    audio_path: Path, session: Session, duration: float | None
) -> list[Segment]:
    progress = session.progress
    model = session.models.get()

    # 最初のひとまとまりが返るまでは、進み具合が本当に分からない。
    # 0% のバーを出すと止まって見えるので、それまでは経過時間だけ出す。
    progress.start("音声を解析しています")

    def on_progress(ratio: float, text: str) -> None:
        progress.update(ratio=ratio if duration else None, detail=text, label="文字起こし中")

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


def _apply_diarization(
    audio_path: Path, segments: list[Segment], session: Session
) -> list[Segment]:
    """話者を判別して、各区間に割り当てる。

    失敗しても字幕そのものは作れるので、警告だけ出して1本のSRTに落とす。
    """
    progress = session.progress
    progress.start("声から話者を判別しています")
    try:
        turns = diarize(
            audio_path,
            speakers=session.speakers or None,
            on_progress=lambda ratio, detail: progress.update(
                ratio=ratio, detail=detail, label="話者分離モデルを準備しています"
            ),
        )
    except DiarizationUnavailable as exc:
        progress.finish()
        _log(f"  [警告] 話者分離ができませんでした: {exc}")
        _log("         話者で分けずに、1本のSRTを作ります。")
        return segments
    finally:
        progress.finish()

    if not turns:
        _log("  [警告] 話者を判別できませんでした。1本のSRTを作ります。")
        return segments

    found = len({turn.speaker for turn in turns})
    _log(f"  話者     : {found}人を検出")
    return assign_speakers(segments, turns)


def _existing_outputs(output_dir: Path, stem: str, diarizing: bool) -> list[Path]:
    if not diarizing:
        single = output_dir / f"{stem}.srt"
        return [single] if single.exists() else []

    prefix = f"{stem}_"
    return sorted(
        path
        for path in output_dir.iterdir()
        if path.is_file() and path.suffix == ".srt" and path.name.startswith(prefix)
    )


def _write_outputs(
    source: Path,
    output_dir: Path,
    segments: list[Segment],
    layout: LayoutOptions,
    session: Session,
) -> list[tuple[Path, int]]:
    session.progress.start("字幕として整形しています")
    try:
        grouped = build_cues_by_speaker(segments, layout)
    finally:
        session.progress.finish()

    written: list[tuple[Path, int]] = []
    for speaker in sorted(grouped, key=_speaker_sort_key):
        cues: list[Cue] = grouped[speaker]
        path = output_dir / speaker_filename(
            source.stem, speaker, session.speaker_names
        )
        path.write_text(
            render_srt(cues), encoding=ENCODINGS[session.args.encoding], newline=""
        )
        written.append((path, len(cues)))
    return written


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

    diarizing = session.speakers is not None
    existing = _existing_outputs(output_dir, source.stem, diarizing)
    if existing and not args.overwrite:
        names = "、".join(path.name for path in existing[:3])
        _log(f"[スキップ] すでにSRTがあります（--overwrite で上書き）: {names}")
        return Outcome(Outcome.SKIPPED)

    layout = resolve_layout(args, source)
    duration = probe_duration(source)

    _log(f"\n=== {position}{source.name} ===")
    _log(f"  エンジン : {session.models.engine.describe()}")
    _log(f"  字幕     : 1行{layout.max_chars_per_line}文字 x 最大{layout.max_lines}行")
    if duration:
        _log(f"  尺       : {_format_seconds(duration)}")

    started = time.monotonic()
    work_dir = None
    try:
        audio_path = _extract(source, session)
        work_dir = audio_path.parent

        segments = _transcribe_audio(audio_path, session, duration)
        if not segments:
            _log("  [失敗] 音声から文字を取れませんでした（無音・BGMのみの可能性）")
            return Outcome(Outcome.FAILED)

        if diarizing:
            segments = _apply_diarization(audio_path, segments, session)
    finally:
        if work_dir and work_dir.name.startswith("koewake-"):
            shutil.rmtree(work_dir, ignore_errors=True)

    written = _write_outputs(source, output_dir, segments, layout, session)

    if args.save_transcript:
        transcript_path = output_dir / f"{source.stem}.transcript.json"
        transcript_path.write_text(
            json.dumps([asdict(segment) for segment in segments], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        _log(f"  文字起こしデータ: {transcript_path.name}")

    elapsed = time.monotonic() - started
    _log(f"  [完了] 所要 {_format_seconds(elapsed)}")
    for path, count in written:
        _log(f"    {path}（字幕 {count} 枚）")
    return Outcome(Outcome.MADE, [path for path, _ in written])


# ファイル名に使えない文字（Windows / macOS の両方を満たす範囲）
_UNSAFE_IN_FILENAME = str.maketrans({char: "_" for char in '\\/:*?"<>|'})


def parse_speakers(value: str | None) -> int | None:
    """`--speakers` の値を解釈する。

    None       -> 話者分離をしない
    "auto"     -> 人数は自動判定（0 を返す）
    "2" など   -> その人数
    """
    if value is None:
        return None
    text = value.strip().lower()
    if text in ("auto", "自動"):
        return 0
    try:
        count = int(text)
    except ValueError as exc:
        raise ValueError(
            f"--speakers には auto か人数を指定してください（受け取った値: {value}）"
        ) from exc
    if count < 1:
        raise ValueError("--speakers の人数は1以上にしてください")
    return count


def parse_speaker_names(value: str | None) -> list[str]:
    if not value:
        return []
    return [name.strip() for name in value.split(",") if name.strip()]


def speaker_filename(stem: str, speaker: str | None, names: list[str]) -> str:
    """話者ごとのSRTのファイル名。"""
    if speaker is None:
        return f"{stem}.srt"

    label = speaker
    # 「話者2」の 2 番目 -> names[1]
    if speaker.startswith("話者"):
        try:
            index = int(speaker.removeprefix("話者")) - 1
        except ValueError:
            index = -1
        if 0 <= index < len(names):
            label = names[index]

    return f"{stem}_{label.translate(_UNSAFE_IN_FILENAME)}.srt"


def _speaker_sort_key(speaker: str | None) -> tuple[int, int | str]:
    if speaker is None:
        return (0, 0)
    if speaker.startswith("話者"):
        suffix = speaker.removeprefix("話者")
        if suffix.isdigit():
            return (1, int(suffix))
    return (2, speaker)


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

    try:
        speakers = parse_speakers(args.speakers)
    except ValueError as exc:
        _log(f"[エラー] {exc}")
        return 1
    speaker_names = parse_speaker_names(args.speaker_names)
    if speaker_names and speakers is None:
        _log("[エラー] --speaker-names は --speakers と一緒に指定してください。")
        return 1

    engine = resolve_engine(quality=args.quality, model=args.model, device=args.device)
    progress = Progress(enabled=False if args.no_progress else None)
    session = Session(
        args=args,
        initial_prompt=initial_prompt,
        models=ModelCache(engine, progress),
        progress=progress,
        speakers=speakers,
        speaker_names=speaker_names,
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

        if outcome.status == Outcome.MADE and outcome.paths:
            made.extend(outcome.paths)
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
