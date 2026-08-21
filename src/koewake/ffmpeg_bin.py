"""ffmpeg の実行ファイルを見つける。

利用者に「ffmpeg を先に入れてください」と言わなくて済むよう、
PATH に無ければ imageio-ffmpeg が同梱しているバイナリにフォールバックする。
"""

from __future__ import annotations

import shutil
from functools import lru_cache


class FFmpegNotFound(RuntimeError):
    pass


@lru_cache(maxsize=1)
def ffmpeg_path() -> str:
    found = shutil.which("ffmpeg")
    if found:
        return found

    try:
        import imageio_ffmpeg
    except ImportError as exc:  # pragma: no cover - 依存が入っていれば起きない
        raise FFmpegNotFound(
            "ffmpeg が見つかりません。imageio-ffmpeg も入っていません。"
        ) from exc

    return imageio_ffmpeg.get_ffmpeg_exe()
