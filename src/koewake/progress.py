"""進捗表示。

「固まっているのか、動いているのか」が分からないのが一番つらいので、
処理が進んでいなくても**アニメーションだけは動き続ける**ようにしている。
（文字起こしは無音区間をまたぐと数十秒スコアが動かないことがある）

描画は別スレッドで回す。呼び出し側は `update()` で状態を置いていくだけ。
"""

from __future__ import annotations

import shutil
import sys
import threading
import time
import unicodedata
from dataclasses import dataclass
from types import TracebackType
from typing import IO

# Windows の古いコンソールでも化けない文字にしておく
_WINDOWS = sys.platform.startswith("win")
SPINNER_FRAMES = "|/-\\" if _WINDOWS else "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
BAR_FILLED = "#" if _WINDOWS else "█"
BAR_EMPTY = "-" if _WINDOWS else "░"

BAR_WIDTH = 16
# 補足テキスト（認識中の文・MB数）を出すのに最低限ほしい幅。
# これを割るなら「5…」のような断片になるだけなので、いっそ出さない。
MIN_DETAIL_WIDTH = 10
FRAME_INTERVAL = 0.12
# 端末でないとき（ログにリダイレクトしたときなど）に1行ずつ出す間隔
PLAIN_INTERVAL = 15.0


def display_width(text: str) -> int:
    """全角を2文字ぶんとして数えた表示幅。折り返しを防ぐために要る。"""
    return sum(2 if unicodedata.east_asian_width(char) in "WF" else 1 for char in text)


def truncate(text: str, width: int) -> str:
    """表示幅が `width` に収まるよう、後ろを削って `…` を付ける。"""
    if width <= 0:
        return ""
    if display_width(text) <= width:
        return text

    ellipsis = "…"
    budget = width - display_width(ellipsis)
    if budget <= 0:
        return ""

    kept: list[str] = []
    used = 0
    for char in text:
        char_width = display_width(char)
        if used + char_width > budget:
            break
        kept.append(char)
        used += char_width
    return "".join(kept) + ellipsis


def short_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    minutes, secs = divmod(seconds, 60)
    if minutes == 0:
        return f"{secs}秒"
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}時間{minutes:02d}分"
    return f"{minutes}分{secs:02d}秒"


def format_bytes(count: float) -> str:
    if count >= 1_000_000_000:
        return f"{count / 1_000_000_000:.1f}GB"
    if count >= 1_000_000:
        return f"{count / 1_000_000:.0f}MB"
    return f"{count / 1_000:.0f}KB"


def render_bar(ratio: float, width: int = BAR_WIDTH) -> str:
    filled = round(ratio * width)
    filled = max(0, min(width, filled))
    return BAR_FILLED * filled + BAR_EMPTY * (width - filled)


@dataclass(frozen=True)
class ProgressState:
    label: str
    detail: str
    ratio: float | None
    elapsed: float


def render_line(state: ProgressState, frame: str, width: int) -> str:
    """1行ぶんの進捗表示を組み立てる（副作用なし＝テストしやすい）。"""
    parts = [f"  {frame} {state.label}"]

    if state.ratio is None:
        parts.append(f"  経過 {short_duration(state.elapsed)}")
    else:
        parts.append(f"  {render_bar(state.ratio)} {state.ratio * 100:5.1f}%")
        parts.append(f"  経過 {short_duration(state.elapsed)}")
        # 序盤の推定は当てにならないので、少し進んでから出す
        if state.ratio >= 0.05:
            remaining = state.elapsed / state.ratio - state.elapsed
            parts.append(f" / 残り 約{short_duration(remaining)}")

    line = "".join(parts)

    if state.detail:
        room = width - display_width(line) - 2
        if room >= MIN_DETAIL_WIDTH:
            line = f"{line}  {truncate(state.detail, room)}"
    return truncate(line, width)


class Progress:
    """進捗の状態を持ち、端末なら1行を書き換え続ける。

    端末でないとき（パイプ・リダイレクト）は、時々ふつうの1行を出すだけにする。
    """

    def __init__(self, stream: IO[str] | None = None, *, enabled: bool | None = None) -> None:
        self.stream = stream or sys.stdout
        if enabled is None:
            enabled = bool(getattr(self.stream, "isatty", lambda: False)())
        self.animated = enabled

        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

        self._label = ""
        self._detail = ""
        self._ratio: float | None = None
        self._started = 0.0
        self._painted_width = 0
        self._last_plain = 0.0

    # -- 呼び出し側が使うもの ------------------------------------------------

    def start(self, label: str, *, ratio: float | None = None) -> None:
        with self._lock:
            self._label = label
            self._detail = ""
            self._ratio = ratio
            self._started = time.monotonic()
            self._last_plain = 0.0

        if self._thread is None:
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()
        if not self.animated:
            self._print_plain(force=True)

    def update(
        self,
        *,
        ratio: float | None = None,
        detail: str | None = None,
        label: str | None = None,
    ) -> None:
        with self._lock:
            if ratio is not None:
                self._ratio = min(max(ratio, 0.0), 1.0)
            if detail is not None:
                self._detail = detail.strip()
            if label is not None:
                self._label = label
        if not self.animated:
            self._print_plain()

    def finish(self, message: str | None = None) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=1.0)
        self._clear()
        if message:
            print(message, file=self.stream, flush=True)

    def __enter__(self) -> Progress:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.finish()

    # -- 内部 ---------------------------------------------------------------

    def _loop(self) -> None:
        for step in range(0, 1 << 30):
            if self._stop.wait(FRAME_INTERVAL):
                return
            if self.animated:
                self._paint(SPINNER_FRAMES[step % len(SPINNER_FRAMES)])
            else:
                self._print_plain()

    def _snapshot(self) -> ProgressState:
        with self._lock:
            return ProgressState(
                label=self._label,
                detail=self._detail,
                ratio=self._ratio,
                elapsed=time.monotonic() - self._started if self._started else 0.0,
            )

    def _paint(self, frame: str) -> None:
        state = self._snapshot()
        if not state.label:
            return
        width = shutil.get_terminal_size(fallback=(80, 24)).columns - 1
        line = render_line(state, frame, width)
        painted = display_width(line)
        # 前より短くなったぶんを空白で消す
        padding = " " * max(0, self._painted_width - painted)
        self._painted_width = painted
        try:
            self.stream.write(f"\r{line}{padding}")
            self.stream.flush()
        except (ValueError, OSError):
            self.animated = False

    def _clear(self) -> None:
        if self.animated and self._painted_width:
            self.stream.write("\r" + " " * self._painted_width + "\r")
            self.stream.flush()
        self._painted_width = 0

    def _print_plain(self, *, force: bool = False) -> None:
        now = time.monotonic()
        with self._lock:
            if not force and now - self._last_plain < PLAIN_INTERVAL:
                return
            self._last_plain = now
            label, ratio = self._label, self._ratio
            elapsed = now - self._started if self._started else 0.0
        if not label:
            return
        if ratio is None:
            print(f"  {label}... (経過 {short_duration(elapsed)})", file=self.stream, flush=True)
        else:
            print(
                f"  {label}... {ratio * 100:.0f}% (経過 {short_duration(elapsed)})",
                file=self.stream,
                flush=True,
            )
