"""動画から音声を取り出す。"""

from __future__ import annotations

import re
import subprocess
import tempfile
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


def extract_audio(source: Path, dest_dir: Path | None = None) -> Path:
    """`source` の音声を 16kHz mono WAV として書き出し、そのパスを返す。"""
    dest_dir = dest_dir or Path(tempfile.mkdtemp(prefix="koewake-"))
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{source.stem}.16k.wav"

    cmd = [
        ffmpeg_path(),
        "-hide_banner",
        "-loglevel", "error",
        "-nostdin",
        "-y",
        "-i", str(source),
        "-vn",
        "-ac", "1",
        "-ar", str(SAMPLE_RATE),
        "-acodec", "pcm_s16le",
        str(dest),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise AudioExtractionError(
            f"音声の取り出しに失敗しました: {source.name}\n{result.stderr.strip()}"
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
