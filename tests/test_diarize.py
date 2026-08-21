from koewake.diarize import (
    MIN_RUN_DURATION,
    SpeakerTurn,
    assign_speakers,
    renumber_by_first_appearance,
    smooth_short_runs,
    speaker_at,
)
from koewake.subtitle import Segment, Word


def _words(spec: list[tuple[str, float, float]]) -> list[Word]:
    return [Word(start=start, end=end, text=text) for text, start, end in spec]


def test_speaker_at_picks_the_largest_overlap():
    turns = [SpeakerTurn(0.0, 5.0, 0), SpeakerTurn(5.0, 10.0, 1)]
    assert speaker_at(turns, 0.0, 4.0) == 0
    assert speaker_at(turns, 6.0, 9.0) == 1
    # わずかに跨る場合は、多く重なっている方
    assert speaker_at(turns, 4.9, 8.0) == 1
    assert speaker_at(turns, 20.0, 21.0) is None


def test_speaker_zero_is_not_treated_as_missing():
    """話者番号 0 は偽値なので、`or` で潰さないこと。"""
    turns = [SpeakerTurn(0.0, 5.0, 0)]
    assert speaker_at(turns, 1.0, 2.0) == 0

    segment = Segment(start=1.0, end=2.0, text="はい", words=_words([("はい", 1.0, 2.0)]))
    assert assign_speakers([segment], turns)[0].speaker == "話者1"


def test_renumber_by_first_appearance():
    turns = [
        SpeakerTurn(10.0, 12.0, 7),
        SpeakerTurn(0.0, 5.0, 3),
        SpeakerTurn(6.0, 8.0, 7),
    ]
    renumbered = {(t.start, t.speaker) for t in renumber_by_first_appearance(turns)}
    # 最初に喋った話者3 が 0 番になる
    assert (0.0, 0) in renumbered
    assert (6.0, 1) in renumbered
    assert (10.0, 1) in renumbered


def test_smoothing_absorbs_a_stray_leading_word():
    """「やった」の「や」だけが前の話者に取られるのを防ぐ。"""
    words = _words([("や", 42.64, 42.92), ("った、", 42.92, 43.16), ("倒せました", 43.58, 44.30)])
    pairs = [(words[0], 0), (words[1], 1), (words[2], 1)]
    smoothed = smooth_short_runs(pairs)
    assert [speaker for _, speaker in smoothed] == [1, 1, 1]


def test_smoothing_keeps_genuinely_long_turns():
    words = _words([("こんにちは", 0.0, 3.0), ("どうも", 3.0, 6.0)])
    pairs = [(words[0], 0), (words[1], 1)]
    assert smooth_short_runs(pairs) == pairs


def test_smoothing_absorbs_into_the_longer_neighbour():
    words = _words([
        ("ながいはなし", 0.0, 4.0),
        ("あ", 4.0, 4.2),
        ("みじかい", 4.2, 4.6),
    ])
    pairs = [(words[0], 0), (words[1], 1), (words[2], 0)]
    smoothed = smooth_short_runs(pairs)
    assert [speaker for _, speaker in smoothed] == [0, 0, 0]


def test_smoothing_never_loses_words():
    words = _words([(f"語{i}", i * 0.2, i * 0.2 + 0.2) for i in range(20)])
    pairs = [(word, index % 3) for index, word in enumerate(words)]
    smoothed = smooth_short_runs(pairs)
    assert [word for word, _ in smoothed] == words
    # すべて短いので、最終的にひとりにまとまる
    assert len({speaker for _, speaker in smoothed}) == 1


def test_assign_speakers_splits_a_segment_at_a_real_change():
    turns = [SpeakerTurn(0.0, 5.0, 0), SpeakerTurn(5.0, 12.0, 1)]
    words = _words([
        ("おはようございます", 0.0, 4.5),
        ("こんばんは", 5.5, 11.0),
    ])
    segment = Segment(start=0.0, end=11.0, text="おはようございますこんばんは", words=words)
    pieces = assign_speakers([segment], turns)

    assert [piece.speaker for piece in pieces] == ["話者1", "話者2"]
    assert pieces[0].text == "おはようございます"
    assert pieces[1].text == "こんばんは"


def test_assign_speakers_without_turns_is_a_no_op():
    segment = Segment(start=0.0, end=1.0, text="はい")
    assert assign_speakers([segment], []) == [segment]


def test_segment_without_word_timestamps_gets_one_speaker():
    turns = [SpeakerTurn(0.0, 5.0, 1)]
    segment = Segment(start=1.0, end=2.0, text="ことば")
    assert assign_speakers([segment], turns)[0].speaker == "話者2"


def test_min_run_duration_is_short_enough_for_real_replies():
    # 「はい」「うん」程度の相づちは 0.5 秒前後。丸ごと吸収されすぎない範囲に置く。
    assert 0.4 <= MIN_RUN_DURATION <= 1.0
