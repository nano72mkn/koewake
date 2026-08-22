import io
import time

from koewake.progress import (
    BAR_WIDTH,
    Progress,
    ProgressState,
    display_width,
    render_bar,
    render_line,
    short_duration,
    truncate,
)


def _state(**kwargs) -> ProgressState:
    base = {"label": "文字起こし中", "detail": "", "ratio": None, "elapsed": 0.0}
    return ProgressState(**{**base, **kwargs})


def test_display_width_counts_full_width_as_two():
    assert display_width("abc") == 3
    assert display_width("こんにちは") == 10
    assert display_width("aあb") == 4


def test_truncate_respects_display_width():
    for width in range(1, 30):
        assert display_width(truncate("こんにちはみなさん元気ですか", width)) <= width


def test_truncate_keeps_short_text_untouched():
    assert truncate("こんにちは", 20) == "こんにちは"


def test_short_duration_formats():
    assert short_duration(0) == "0秒"
    assert short_duration(42) == "42秒"
    assert short_duration(64) == "1分04秒"
    assert short_duration(3700) == "1時間01分"
    assert short_duration(-5) == "0秒"


def test_render_bar_bounds():
    assert len(render_bar(0.0)) == BAR_WIDTH
    assert len(render_bar(1.0)) == BAR_WIDTH
    # 範囲外の値でも壊れない
    assert len(render_bar(1.5)) == BAR_WIDTH
    assert len(render_bar(-0.5)) == BAR_WIDTH
    assert render_bar(0.0) != render_bar(1.0)


def test_render_line_never_exceeds_terminal_width():
    for width in range(10, 120):
        line = render_line(
            _state(
                ratio=0.42,
                elapsed=90.0,
                detail="今日はモンスターハンターの配信をやっていきます",
            ),
            "|",
            width,
        )
        assert display_width(line) <= width, (width, line)


def test_render_line_without_ratio_shows_elapsed_only():
    line = render_line(_state(label="音声を取り出しています", elapsed=5.0), "|", 100)
    assert "音声を取り出しています" in line
    assert "経過 5秒" in line
    assert "%" not in line


def test_render_line_shows_eta_only_after_enough_progress():
    early = render_line(_state(ratio=0.01, elapsed=10.0), "|", 100)
    later = render_line(_state(ratio=0.50, elapsed=10.0), "|", 100)
    assert "残り" not in early
    assert "残り 約10秒" in later


def test_progress_without_tty_prints_plain_lines():
    stream = io.StringIO()
    progress = Progress(stream, enabled=False)
    progress.start("文字起こし中", ratio=0.0)
    progress.finish("おわり")
    output = stream.getvalue()
    assert "文字起こし中" in output
    assert "おわり" in output
    # 端末でないときは制御文字を出さない
    assert "\r" not in output


def test_progress_animates_on_a_tty_like_stream():
    class FakeTTY(io.StringIO):
        def isatty(self) -> bool:
            return True

    stream = FakeTTY()
    progress = Progress(stream)
    assert progress.animated
    progress.start("文字起こし中", ratio=0.0)
    progress.update(ratio=0.5, detail="こんにちは")
    time.sleep(0.4)
    progress.finish()

    output = stream.getvalue()
    assert "\r" in output
    assert "文字起こし中" in output
    # アニメーションのコマが進んでいる（＝止まって見えない）
    assert sum(output.count(frame) for frame in "⠋⠙⠹|/-\\") >= 2
    # 終了後は行が消えている
    assert output.rstrip(" ").endswith("\r")


def test_progress_finish_is_safe_to_call_twice():
    progress = Progress(io.StringIO(), enabled=False)
    progress.start("なにか")
    progress.finish()
    progress.finish()


def test_detail_is_dropped_rather_than_shown_as_a_fragment():
    from koewake.progress import MIN_DETAIL_WIDTH

    state = _state(ratio=0.36, elapsed=3.0, detail="28MB / 78MB")
    for width in range(20, 140):
        line = render_line(state, "|", width)
        assert display_width(line) <= width
        # 出すなら意味のある長さで出す。出せないなら出さない。
        if "28" in line:
            tail = line[line.index("28"):]
            assert display_width(tail) >= min(
                MIN_DETAIL_WIDTH, display_width("28MB / 78MB")
            ) - 1, (width, line)


def test_detail_appears_when_there_is_room():
    line = render_line(_state(ratio=0.36, elapsed=3.0, detail="28MB / 78MB"), "|", 160)
    assert "28MB / 78MB" in line


def test_fallback_to_cpu_keeps_the_model_but_switches_device():
    from koewake.engine import EngineConfig, fallback_to_cpu

    engine = EngineConfig(
        model="large-v3-turbo", device="cuda", compute_type="float16", cpu_threads=8
    )
    fallen = fallback_to_cpu(engine)

    assert fallen.device == "cpu"
    # CPU では float16 は使えないので int8 に落とす
    assert fallen.compute_type == "int8"
    # モデルとスレッド数は引き継ぐ
    assert fallen.model == "large-v3-turbo"
    assert fallen.cpu_threads == 8
    assert "CPU" in fallen.describe()


def test_registering_cuda_dll_dirs_is_harmless_off_windows():
    from koewake.transcribe import _register_cuda_dll_dirs

    # Windows 以外では何もしない（例外も出さない）
    _register_cuda_dll_dirs()


def test_huggingface_warnings_are_silenced_at_import_time():
    """環境変数は huggingface_hub より先に設定されている必要がある。"""
    import os

    import koewake.transcribe  # noqa: F401  読み込むだけで設定される

    assert os.environ.get("HF_HUB_VERBOSITY") == "error"
    assert os.environ.get("HF_HUB_DISABLE_SYMLINKS_WARNING") == "1"
