from koewake.subtitle import Segment
from koewake.transcribe import _drop_consecutive_repeats, _looks_like_hallucination


def test_known_hallucinations_are_detected():
    assert _looks_like_hallucination("ご視聴ありがとうございました")
    assert _looks_like_hallucination("チャンネル登録よろしくお願いします")
    assert _looks_like_hallucination("ああああああああああああ")
    assert _looks_like_hallucination("はいはいはいはいはいはい")
    assert _looks_like_hallucination("   ")


def test_normal_speech_is_kept():
    assert not _looks_like_hallucination("今日はこのゲームをやっていきます")
    assert not _looks_like_hallucination("うわー今の見た？")


def test_repeated_segments_are_capped():
    segments = [Segment(start=float(i), end=i + 1.0, text="はい") for i in range(10)]
    kept = _drop_consecutive_repeats(segments, limit=3)
    assert len(kept) == 3


def test_alternating_segments_are_untouched():
    segments = [
        Segment(start=0.0, end=1.0, text="はい"),
        Segment(start=1.0, end=2.0, text="いいえ"),
        Segment(start=2.0, end=3.0, text="はい"),
    ]
    assert len(_drop_consecutive_repeats(segments, limit=3)) == 3
