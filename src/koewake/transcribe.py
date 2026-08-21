"""音声 -> テキスト（faster-whisper）。

エンジンはここ 1 ファイルに閉じ込めてある。将来クラウドAPIに差し替える場合も
`transcribe()` が `list[Segment]` を返す形さえ保てば、他のモジュールは触らずに済む。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from koewake.engine import EngineConfig
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


def transcribe(
    audio_path: Path,
    engine: EngineConfig,
    options: TranscribeOptions | None = None,
    duration: float | None = None,
    on_progress: ProgressCallback | None = None,
) -> list[Segment]:
    from faster_whisper import WhisperModel

    options = options or TranscribeOptions()

    model = WhisperModel(
        engine.model,
        device=engine.device,
        compute_type=engine.compute_type,
        cpu_threads=engine.cpu_threads,
    )

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
