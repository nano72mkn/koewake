"""動画から音声を取り出す。"""

from __future__ import annotations

import re
import subprocess
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path

from koewake.ffmpeg_bin import ffmpeg_path

# Whisper が内部で使うサンプリングレート。ここで合わせておくと余計な再変換が起きない。
SAMPLE_RATE = 16_000

VIDEO_SUFFIXES = {
    ".mp4", ".mov", ".mkv", ".avi", ".wmv", ".flv", ".webm", ".m4v", ".mts", ".ts",
}
AUDIO_SUFFIXES = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma"}
MEDIA_SUFFIXES = VIDEO_SUFFIXES | AUDIO_SUFFIXES


class AudioExtractionError(RuntimeError):
    pass


@dataclass(frozen=True)
class AudioTrack:
    """音声トラック1本。`index` は音声ストリームの中での番号（0始まり）。"""

    index: int
    title: str | None = None

    def label(self) -> str:
        """出力ファイル名に使う名前。収録側で名前が付いていればそれを使う。"""
        if self.title:
            return self.title
        return f"トラック{self.index + 1}"


_STREAM_RE = re.compile(r"^\s*Stream #\d+:\d+.*?:\s*(\w+):")
# mp4 は name、mkv は title に入る
_TRACK_TITLE_RE = re.compile(r"^\s*(?:name|title)\s*:\s*(.+?)\s*$")


def probe_audio_tracks(source: Path) -> list[AudioTrack]:
    """音声トラックの一覧。取得できなければ「1本ある」とみなす。

    マイクを人ごとに分けて録っている場合、1つの動画に音声が複数入っている。
    それぞれ別に字幕を作れるよう、ここで本数と名前を調べる。
    """
    cmd = [ffmpeg_path(), "-hide_banner", "-nostdin", "-i", str(source)]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=60, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return [AudioTrack(index=0)]

    tracks: list[AudioTrack] = []
    in_audio = False
    for line in result.stderr.splitlines():
        stream = _STREAM_RE.match(line)
        if stream:
            in_audio = stream.group(1) == "Audio"
            if in_audio:
                tracks.append(AudioTrack(index=len(tracks)))
            continue

        if in_audio and tracks:
            title = _TRACK_TITLE_RE.match(line)
            if title and tracks[-1].title is None:
                tracks[-1] = replace(tracks[-1], title=title.group(1))

    return tracks or [AudioTrack(index=0)]


def extract_audio(
    source: Path, dest_dir: Path | None = None, track: int = 0
) -> Path:
    """`source` の音声を 16kHz mono WAV として書き出し、そのパスを返す。

    `track` は音声ストリームの中での番号（0始まり）。
    """
    dest_dir = dest_dir or Path(tempfile.mkdtemp(prefix="koewake-"))
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{source.stem}.{track}.16k.wav"

    cmd = [
        ffmpeg_path(),
        "-hide_banner",
        "-loglevel", "error",
        "-nostdin",
        "-y",
        "-i", str(source),
        "-map", f"0:a:{track}",
        "-vn",
        "-ac", "1",
        "-ar", str(SAMPLE_RATE),
        "-acodec", "pcm_s16le",
        str(dest),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise AudioExtractionError(
            f"音声の取り出しに失敗しました: {source.name}（トラック{track + 1}）"
            f"\n{result.stderr.strip()}"
        )
    if not dest.exists() or dest.stat().st_size == 0:
        raise AudioExtractionError(
            f"音声トラックが見つかりませんでした: {source.name}"
        )
    return dest


def probe_duration(source: Path) -> float | None:
    """尺（秒）。取得できなければ None。進捗表示にしか使わないので失敗しても止めない。"""
    cmd = [
        ffmpeg_path(),
        "-hide_banner",
        "-nostdin",
        "-i", str(source),
        "-f", "null", "-",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.SubprocessError):
        return None

    for line in result.stderr.splitlines():
        marker = "Duration:"
        if marker not in line:
            continue
        value = line.split(marker, 1)[1].split(",", 1)[0].strip()
        try:
            hours, minutes, seconds = value.split(":")
            return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
        except ValueError:
            return None
    return None


_SIZE_RE = re.compile(r"\b(\d{2,5})x(\d{2,5})\b")


def probe_video_size(source: Path) -> tuple[int, int] | None:
    """映像の幅・高さ。音声ファイルなど映像が無ければ None。"""
    cmd = [ffmpeg_path(), "-hide_banner", "-nostdin", "-i", str(source)]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.SubprocessError):
        return None

    for line in result.stderr.splitlines():
        if "Video:" not in line:
            continue
        match = _SIZE_RE.search(line)
        if match:
            return int(match.group(1)), int(match.group(2))
    return None


def is_vertical(source: Path) -> bool | None:
    """縦動画（ショート）なら True、横なら False、判定できなければ None。"""
    size = probe_video_size(source)
    if size is None:
        return None
    width, height = size
    return height > width
