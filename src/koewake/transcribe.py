"""音声 -> テキスト（faster-whisper）。

エンジンはここ 1 ファイルに閉じ込めてある。将来クラウドAPIに差し替える場合も
`transcribe()` が `list[Segment]` を返す形さえ保てば、他のモジュールは触らずに済む。
"""

from __future__ import annotations

import os
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from koewake.engine import EngineConfig
from koewake.progress import format_bytes
from koewake.subtitle import Segment, Word

ProgressCallback = Callable[[float, str], None]

# 無音・BGMだけの区間で Whisper が高確率で吐く定型句。字幕に入ると事故になるので落とす。
HALLUCINATION_PATTERNS = [
    re.compile(pattern)
    for pattern in (
        r"^ご視聴(いただき)?(ありがとう|ありがとうございました)",
        r"^最後まで(ご視聴|見て)",
        r"^チャンネル登録",
        r"^(高評価|グッドボタン).{0,10}(お願い|よろしく)",
        r"^字幕",
        r"^おわり$",
        r"^(Thanks for watching|Subscribe|Thank you for watching)",
        r"^\W+$",
    )
]

# 同じ文字/短い語の連呼（「あああああ…」「はいはいはい…」）
_REPEAT_CHAR = re.compile(r"(.)\1{9,}")
_REPEAT_WORD = re.compile(r"(.{2,6}?)\1{5,}")


@dataclass(frozen=True)
class TranscribeOptions:
    language: str = "ja"
    beam_size: int = 5
    vad: bool = True
    initial_prompt: str | None = None
    no_speech_threshold: float = 0.85
    max_repeats: int = 3


def _looks_like_hallucination(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return True
    if any(pattern.search(stripped) for pattern in HALLUCINATION_PATTERNS):
        return True
    return bool(_REPEAT_CHAR.search(stripped) or _REPEAT_WORD.search(stripped))


def _drop_consecutive_repeats(segments: list[Segment], limit: int) -> list[Segment]:
    """同じセリフが延々続くループ出力を、規定回数までに抑える。"""
    kept: list[Segment] = []
    streak_text: str | None = None
    streak = 0
    for segment in segments:
        text = segment.text.strip()
        if text == streak_text:
            streak += 1
            if streak > limit:
                continue
        else:
            streak_text, streak = text, 1
        kept.append(segment)
    return kept


# 置いてあれば自動で読む単語リストの名前と、探す場所
DEFAULT_VOCAB_NAMES = ("単語リスト.txt", "vocab.txt")
DEFAULT_VOCAB_FOLDERS = ("scripts", ".")


def find_default_vocabulary(root: Path | None = None) -> Path | None:
    """単語リストが置いてあれば、そのパスを返す。

    ランチャーから `--vocab` を渡さずに済ませるための仕組み。
    こうしておくと `.bat` に日本語のファイル名を書かなくてよくなる
    （cmd.exe が CP932 で読むため、`.bat` は ASCII だけにしたい）。
    """
    root = root or Path.cwd()
    for folder in DEFAULT_VOCAB_FOLDERS:
        for name in DEFAULT_VOCAB_NAMES:
            candidate = root / folder / name
            if candidate.is_file():
                return candidate
    return None


def load_vocabulary(path: Path) -> str:
    """固有名詞リスト（1行1語 / # でコメント）を Whisper の initial_prompt に変換する。"""
    terms: list[str] = []
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if line:
            terms.append(line)
    if not terms:
        return ""
    return "、".join(terms) + "。"


def _byte_progress_tqdm(on_progress: ProgressCallback):
    """huggingface_hub に渡す tqdm 互換クラスを作る。

    描画はさせず、バイト数だけを `on_progress` に流す。

    注意点が2つある。
    - `total` は生成時には 0 で、ダウンロードが始まってから設定される。
      なので合計は update のたびに読み直す。
    - バイトのバーは複数作られる（ダウンロード用と、xet の再構築用）。
      二重に数えないよう、最初のひとつだけを見る。
    - `disable=True` の tqdm は `self.n` を更新しないので、進んだ量は自分で数える。
    """
    from tqdm.auto import tqdm as base_tqdm

    lock = threading.Lock()
    tracked: list[object] = []

    class ByteProgressTqdm(base_tqdm):
        def __init__(self, *args, **kwargs):
            kwargs["disable"] = True
            super().__init__(*args, **kwargs)
            self._tracked = False
            self._done = 0
            if kwargs.get("unit") == "B":
                with lock:
                    if not tracked:
                        tracked.append(self)
                        self._tracked = True

        def update(self, n=1):
            result = super().update(n)
            if self._tracked:
                self._done += n
                if self.total:
                    on_progress(
                        min(self._done / self.total, 1.0),
                        f"{format_bytes(self._done)} / {format_bytes(self.total)}",
                    )
            return result

    return ByteProgressTqdm


def download_model(name: str, on_progress: ProgressCallback | None = None) -> str:
    """モデルを取得して、ローカルのパスを返す。取得済みならすぐ返る。

    faster-whisper 自身はダウンロード進捗を潰している（`tqdm_class=disabled_tqdm`）ので、
    ここは自前で `snapshot_download` を呼び、何MB進んだかを拾えるようにしている。
    1.5GB のダウンロードが無反応に見えるのが一番つらいため。
    """
    # 「HF_TOKEN を設定すると速いよ」という案内を黙らせる。公開モデルを取るだけなので
    # 不要な上、進捗表示に割り込んで行が乱れる。
    # （logging の setLevel は huggingface_hub 側があとから上書きするので効かない）
    os.environ.setdefault("HF_HUB_VERBOSITY", "error")

    import huggingface_hub
    from faster_whisper.utils import _MODELS

    kwargs = {
        "allow_patterns": [
            "config.json",
            "preprocessor_config.json",
            "model.bin",
            "tokenizer.json",
            "vocabulary.*",
        ],
    }
    if on_progress is not None:
        kwargs["tqdm_class"] = _byte_progress_tqdm(on_progress)

    return huggingface_hub.snapshot_download(_MODELS.get(name, name), **kwargs)


def load_model(engine: EngineConfig, on_progress: ProgressCallback | None = None):
    """Whisper のモデルを読み込む。

    初回はモデル本体（large 系で 1.5GB 前後）をダウンロードするので時間がかかる。
    複数ファイルを処理するときに読み直さずに済むよう、`transcribe()` から分けてある。
    """
    from faster_whisper import WhisperModel

    source = engine.model
    try:
        source = download_model(engine.model, on_progress)
    except Exception:
        # ダウンロード経路で何かあっても、WhisperModel 側の通常経路に任せれば動く。
        # （ローカルパス指定・HF の仕様変更・オフラインでキャッシュ済み など）
        source = engine.model

    return WhisperModel(
        source,
        device=engine.device,
        compute_type=engine.compute_type,
        cpu_threads=engine.cpu_threads,
    )


def transcribe(
    model,
    audio_path: Path,
    options: TranscribeOptions | None = None,
    duration: float | None = None,
    on_progress: ProgressCallback | None = None,
) -> list[Segment]:
    options = options or TranscribeOptions()

    raw_segments, _info = model.transcribe(
        str(audio_path),
        language=options.language,
        beam_size=options.beam_size,
        vad_filter=options.vad,
        vad_parameters={"min_silence_duration_ms": 500},
        word_timestamps=True,
        initial_prompt=options.initial_prompt or None,
        # 直前の出力を条件にすると、一度ハルシネーションが始まったとき延々と引きずる。
        condition_on_previous_text=False,
    )

    segments: list[Segment] = []
    for raw in raw_segments:
        if on_progress and duration:
            on_progress(min(raw.end / duration, 1.0), raw.text.strip())

        text = raw.text.strip()
        if not text:
            continue
        if getattr(raw, "no_speech_prob", 0.0) > options.no_speech_threshold:
            continue
        if _looks_like_hallucination(text):
            continue

        words = [
            Word(start=word.start, end=word.end, text=word.word)
            for word in (raw.words or [])
            if word.start is not None and word.end is not None
        ]
        segments.append(Segment(start=raw.start, end=raw.end, text=text, words=words))

    return _drop_consecutive_repeats(segments, options.max_repeats)
