"""話者分離モデルの取得とキャッシュ。

Whisper は huggingface_hub がキャッシュを面倒みてくれるが、話者分離のモデルは
GitHub のリリースに置かれた素のファイルなので、置き場所と進捗表示を自前で持つ。
"""

from __future__ import annotations

import os
import shutil
import sys
import tarfile
import tempfile
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

ProgressCallback = Callable[[float, str], None]

_RELEASE = "https://github.com/k2-fsa/sherpa-onnx/releases/download"
CHUNK = 1 << 16


@dataclass(frozen=True)
class ModelSpec:
    """1つのモデルファイル。書庫なら中の1ファイルを取り出す。"""

    filename: str
    url: str
    member: str | None = None

    @property
    def label(self) -> str:
        return self.filename


# 話者の切れ目を見つけるモデル（pyannote segmentation 3.0 を ONNX にしたもの）
SEGMENTATION = ModelSpec(
    filename="pyannote-segmentation-3-0.onnx",
    url=f"{_RELEASE}/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2",
    member="sherpa-onnx-pyannote-segmentation-3-0/model.onnx",
)

# 声の特徴を数値にするモデル。日本語専用のものは無いが、
# 話者埋め込みは言語の影響を受けにくいので中国語・英語で学習したものを使う。
EMBEDDING = ModelSpec(
    filename="campplus-sv-zh-en-common-advanced.onnx",
    # リリースのタグ名が "recongition"（原文ママ）なので、直しては駄目
    url=f"{_RELEASE}/speaker-recongition-models/"
    "3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx",
)


def cache_dir() -> Path:
    """モデルの置き場所。環境変数 KOEWAKE_CACHE_DIR があればそれを使う。"""
    override = os.environ.get("KOEWAKE_CACHE_DIR")
    if override:
        return Path(override)

    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches"
    else:
        base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base) / "koewake"


def _download(url: str, dest: Path, on_progress: ProgressCallback | None) -> None:
    """途中で失敗しても壊れたファイルを残さないよう、一時ファイル経由で置く。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url) as response:
        total = int(response.headers.get("Content-Length") or 0)
        done = 0
        with tempfile.NamedTemporaryFile(dir=dest.parent, delete=False) as tmp:
            temp_path = Path(tmp.name)
            while True:
                chunk = response.read(CHUNK)
                if not chunk:
                    break
                tmp.write(chunk)
                done += len(chunk)
                if on_progress and total:
                    from koewake.progress import format_bytes

                    on_progress(
                        min(done / total, 1.0),
                        f"{format_bytes(done)} / {format_bytes(total)}",
                    )
    temp_path.replace(dest)


def _extract_member(archive: Path, member: str, dest: Path) -> None:
    with tarfile.open(archive, "r:*") as tar:
        extracted = tar.extractfile(member)
        if extracted is None:
            raise FileNotFoundError(f"{archive.name} の中に {member} がありません")
        with extracted, dest.open("wb") as out:
            shutil.copyfileobj(extracted, out)


def ensure_model(spec: ModelSpec, on_progress: ProgressCallback | None = None) -> Path:
    """モデルを用意して、そのパスを返す。すでにあればすぐ返る。"""
    target = cache_dir() / spec.filename
    if target.exists():
        return target

    target.parent.mkdir(parents=True, exist_ok=True)
    if spec.member is None:
        _download(spec.url, target, on_progress)
        return target

    with tempfile.TemporaryDirectory(dir=target.parent) as work:
        archive = Path(work) / "archive"
        _download(spec.url, archive, on_progress)
        staged = Path(work) / "model.onnx"
        _extract_member(archive, spec.member, staged)
        staged.replace(target)
    return target
