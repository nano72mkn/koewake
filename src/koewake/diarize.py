"""話者分離（Phase 2）。

「誰が喋ったか」を時間の区間として求め、字幕をその人ごとに振り分ける。

pyannote.audio ではなく sherpa-onnx を使っている。pyannote は PyTorch と
HuggingFace のトークン発行・規約同意が要り、「利用者が自分だけで回せること」を
壊してしまうため（詳細は docs/design.md）。
"""

from __future__ import annotations

import wave
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from koewake.modelstore import EMBEDDING, SEGMENTATION, ensure_model
from koewake.subtitle import Segment, Word

ProgressCallback = Callable[[float, str], None]

# 短すぎる発話・短すぎる沈黙は、話者の切り替わりとして扱わない
MIN_DURATION_ON = 0.3
MIN_DURATION_OFF = 0.5
# 話者数を指定しないときの、声の近さのしきい値。小さいほど別人と判定しやすい。
DEFAULT_THRESHOLD = 0.5
# これより短いひとかたまりは、話者の切り替わりとして信用しない。
# 「やった」の「や」だけが前の話者に取られる、といった取りこぼしを防ぐ。
MIN_RUN_DURATION = 0.6


@dataclass(frozen=True)
class SpeakerTurn:
    """ひとりが続けて喋っている区間。"""

    start: float
    end: float
    speaker: int

    def overlap(self, start: float, end: float) -> float:
        return max(0.0, min(self.end, end) - max(self.start, start))


class DiarizationUnavailable(RuntimeError):
    """sherpa-onnx が入っていない、またはモデルを用意できない。"""


def _read_wave(path: Path, expected_rate: int):
    import numpy as np

    with wave.open(str(path)) as wav:
        if wav.getframerate() != expected_rate:
            raise DiarizationUnavailable(
                f"話者分離には {expected_rate}Hz の音声が必要です（{wav.getframerate()}Hz でした）"
            )
        raw = wav.readframes(wav.getnframes())
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def _scaled(
    on_progress: ProgressCallback | None, offset: float, weight: float
) -> ProgressCallback | None:
    if on_progress is None:
        return None
    return lambda ratio, detail: on_progress(offset + ratio * weight, detail)


def diarize(
    audio_path: Path,
    speakers: int | None = None,
    threshold: float = DEFAULT_THRESHOLD,
    on_progress: ProgressCallback | None = None,
) -> list[SpeakerTurn]:
    """音声を話者ごとの区間に分ける。

    `speakers` を指定すればその人数に、しなければ声の近さで自動判定する。
    """
    try:
        import sherpa_onnx
    except ImportError as exc:
        raise DiarizationUnavailable(
            "話者分離には sherpa-onnx が必要です。`uv sync --extra diarize` で入れてください。"
        ) from exc

    # 2つのモデルを続けて取るので、進捗バーが 100% -> 0% と戻らないよう、
    # おおよそのサイズ比（7MB : 28MB）で1本の進捗にならす。
    segmentation_model = ensure_model(SEGMENTATION, _scaled(on_progress, 0.0, 0.2))
    embedding_model = ensure_model(EMBEDDING, _scaled(on_progress, 0.2, 0.8))

    clustering = (
        sherpa_onnx.FastClusteringConfig(num_clusters=speakers)
        if speakers and speakers > 0
        else sherpa_onnx.FastClusteringConfig(threshold=threshold)
    )
    config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
                model=str(segmentation_model)
            ),
        ),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(embedding_model)),
        clustering=clustering,
        min_duration_on=MIN_DURATION_ON,
        min_duration_off=MIN_DURATION_OFF,
    )
    if not config.validate():
        raise DiarizationUnavailable("話者分離の設定を組み立てられませんでした")

    engine = sherpa_onnx.OfflineSpeakerDiarization(config)
    samples = _read_wave(audio_path, engine.sample_rate)

    turns = [
        SpeakerTurn(start=s.start, end=s.end, speaker=s.speaker)
        for s in engine.process(samples).sort_by_start_time()
    ]
    return renumber_by_first_appearance(turns)


def renumber_by_first_appearance(turns: list[SpeakerTurn]) -> list[SpeakerTurn]:
    """話者番号を「喋り始めた順」に振り直す。

    クラスタリングの番号は実行ごとに変わりうるので、そのまま話者1・話者2にすると
    ファイル名の意味が安定しない。最初に喋った人を話者1にする。
    """
    order: dict[int, int] = {}
    for turn in sorted(turns, key=lambda t: t.start):
        if turn.speaker not in order:
            order[turn.speaker] = len(order)
    return [replace(turn, speaker=order[turn.speaker]) for turn in turns]


def speaker_at(turns: list[SpeakerTurn], start: float, end: float) -> int | None:
    """その時間帯を最も多く占めている話者。重なりが無ければ None。"""
    best_speaker, best_overlap = None, 0.0
    for turn in turns:
        overlap = turn.overlap(start, end)
        if overlap > best_overlap:
            best_speaker, best_overlap = turn.speaker, overlap
    return best_speaker


def _nearest_speaker(turns: list[SpeakerTurn], start: float, end: float) -> int | None:
    """どの区間とも重ならないとき、時間的に一番近い話者を採る。"""
    if not turns:
        return None
    middle = (start + end) / 2

    def distance(turn: SpeakerTurn) -> float:
        if turn.start <= middle <= turn.end:
            return 0.0
        return min(abs(turn.start - middle), abs(turn.end - middle))

    return min(turns, key=distance).speaker


def assign_speakers(segments: list[Segment], turns: list[SpeakerTurn]) -> list[Segment]:
    """文字起こしの各区間に話者を割り当てる。

    話者が切り替わる位置をまたぐ区間は、単語のタイムスタンプを使って分割する。
    そうしないと、相手のセリフが自分の字幕ファイルに紛れ込む。
    """
    if not turns:
        return segments

    assigned: list[Segment] = []
    for segment in segments:
        assigned.extend(_split_segment_by_speaker(segment, turns))
    return assigned


def _speaker_of_word(turns: list[SpeakerTurn], word: Word) -> int | None:
    # 話者番号 0 は偽値なので、`or` で繋いではいけない
    speaker = speaker_at(turns, word.start, word.end)
    if speaker is None:
        speaker = _nearest_speaker(turns, word.start, word.end)
    return speaker


def _runs(pairs: list[tuple[Word, int | None]]) -> list[list[tuple[Word, int | None]]]:
    """同じ話者が続くまとまりに分ける。"""
    runs: list[list[tuple[Word, int | None]]] = []
    for word, speaker in pairs:
        if runs and runs[-1][0][1] == speaker:
            runs[-1].append((word, speaker))
        else:
            runs.append([(word, speaker)])
    return runs


def _run_duration(run: list[tuple[Word, int | None]]) -> float:
    return run[-1][0].end - run[0][0].start


def smooth_short_runs(
    pairs: list[tuple[Word, int | None]], min_duration: float = MIN_RUN_DURATION
) -> list[tuple[Word, int | None]]:
    """短すぎる「話者の切り替わり」を、隣のまとまりに吸収させる。

    話者の区間には隙間があり、また境目は数十ミリ秒ずれる。そのため発話の
    最初の一語だけが前の話者のものと判定されることがある
    （「おお、なるほど」の「おお、」、「やった」の「や」など）。
    語が単独で取り残されると、単語の途中で別ファイルに分かれてしまう。

    そこで、規定より短いまとまりは切り替わりとみなさず、隣の長い方に寄せる。
    """
    while True:
        runs = _runs(pairs)
        if len(runs) < 2:
            return pairs

        durations = [_run_duration(run) for run in runs]
        target = None
        for index, duration in enumerate(durations):
            if duration < min_duration and (target is None or duration < durations[target]):
                target = index
        if target is None:
            return pairs

        if target == 0:
            neighbour = 1
        elif target == len(runs) - 1:
            neighbour = target - 1
        else:
            neighbour = (
                target - 1 if durations[target - 1] >= durations[target + 1] else target + 1
            )

        winner = runs[neighbour][0][1]
        absorbed = {id(word) for word, _ in runs[target]}
        pairs = [
            (word, winner if id(word) in absorbed else speaker) for word, speaker in pairs
        ]


def _split_segment_by_speaker(segment: Segment, turns: list[SpeakerTurn]) -> list[Segment]:
    fallback = speaker_at(turns, segment.start, segment.end)
    if fallback is None:
        fallback = _nearest_speaker(turns, segment.start, segment.end)

    words = [word for word in segment.words if word.text.strip()]
    if not words:
        return [replace(segment, speaker=_speaker_label(fallback))]

    pairs = [(word, _speaker_of_word(turns, word)) for word in words]
    pairs = smooth_short_runs(pairs)
    runs = _runs(pairs)

    if len(runs) == 1:
        speaker = runs[0][0][1]
        if speaker is None:
            speaker = fallback
        return [replace(segment, speaker=_speaker_label(speaker))]

    pieces: list[Segment] = []
    for run in runs:
        chunk = [word for word, _ in run]
        speaker = run[0][1]
        pieces.append(
            Segment(
                start=chunk[0].start,
                end=chunk[-1].end,
                text="".join(word.text for word in chunk),
                words=chunk,
                speaker=_speaker_label(speaker if speaker is not None else fallback),
            )
        )
    return pieces


def _speaker_label(speaker: int | None) -> str | None:
    return None if speaker is None else f"話者{speaker + 1}"
