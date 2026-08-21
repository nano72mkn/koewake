"""実行環境（GPU/CPU）とモデルの選び方。

利用者のPCスペックが分からない前提なので、既定値は「どのPCでも動くこと」を優先し、
速いハードを持っていれば自動でそちらに乗る、という方針にしてある。
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# 品質プリセット -> Whisper モデル名
QUALITY_PRESETS: dict[str, str] = {
    # 下書き用。ゲーム実況の固有名詞はかなり落ちる。
    "fast": "small",
    # 既定。速度と精度のバランスが日本語でも良い。
    "balanced": "large-v3-turbo",
    # 最優先が精度のとき。turbo よりだいぶ遅い。
    "accurate": "large-v3",
}

DEFAULT_QUALITY = "balanced"


@dataclass(frozen=True)
class EngineConfig:
    model: str
    device: str
    compute_type: str
    cpu_threads: int

    def describe(self) -> str:
        where = "GPU (CUDA)" if self.device == "cuda" else f"CPU ({self.cpu_threads}スレッド)"
        return f"{self.model} / {where} / {self.compute_type}"


def _cuda_device_count() -> int:
    try:
        import ctranslate2

        return int(ctranslate2.get_cuda_device_count())
    except Exception:
        return 0


def _cpu_threads() -> int:
    count = os.cpu_count() or 4
    # 全コアを奪うとエンコードや配信と同時に回せないので少し残す。
    return max(2, min(count - 1, 16))


def resolve_engine(
    quality: str = DEFAULT_QUALITY,
    model: str | None = None,
    device: str = "auto",
) -> EngineConfig:
    if quality not in QUALITY_PRESETS:
        raise ValueError(
            f"不明な品質プリセット: {quality}（選べるのは {', '.join(QUALITY_PRESETS)}）"
        )
    resolved_model = model or QUALITY_PRESETS[quality]

    if device == "auto":
        device = "cuda" if _cuda_device_count() > 0 else "cpu"

    if device == "cuda":
        compute_type = "float16"
    else:
        # CTranslate2 は Metal に対応していないので Apple Silicon でも CPU 実行。
        # int8 なら Apple Silicon / 一般的な Windows CPU のどちらでも実用速度になる。
        compute_type = "int8"

    return EngineConfig(
        model=resolved_model,
        device=device,
        compute_type=compute_type,
        cpu_threads=_cpu_threads(),
    )
