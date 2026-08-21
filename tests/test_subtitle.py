import itertools

from koewake.subtitle import (
    VERTICAL_LAYOUT,
    Cue,
    LayoutOptions,
    Segment,
    Word,
    build_cues,
    format_timestamp,
    normalize_text,
    render_srt,
    segment_to_cues,
    wrap_lines,
)

LAYOUT = LayoutOptions(max_chars_per_line=20, max_lines=2)


def test_format_timestamp():
    assert format_timestamp(0) == "00:00:00,000"
    assert format_timestamp(3661.5) == "01:01:01,500"
    assert format_timestamp(-1) == "00:00:00,000"


def test_normalize_text_strips_punctuation():
    assert (
        normalize_text("こんにちは、みなさん。", strip_punctuation=True) == "こんにちは みなさん"
    )
    assert (
        normalize_text("こんにちは、みなさん。", strip_punctuation=False)
        == "こんにちは、みなさん。"
    )


def test_wrap_lines_respects_line_length():
    text = "今日はゲーム実況の配信をやっていきたいと思いますのでよろしく"
    lines = wrap_lines(text, LAYOUT)
    assert len(lines) <= LAYOUT.max_lines
    assert all(len(line) <= LAYOUT.max_chars_per_line for line in lines)
    assert "".join(lines).replace(" ", "") == text.replace(" ", "")


def test_wrap_lines_never_starts_with_forbidden_char():
    text = "やったーついに勝てた、めちゃくちゃ嬉しいんだけどこれ"
    lines = wrap_lines(text, LayoutOptions(max_chars_per_line=12, max_lines=2))
    assert all(line[0] not in "、。ー っ" for line in lines[1:])


def test_short_segment_becomes_single_cue():
    segment = Segment(start=1.0, end=2.5, text="おはようございます")
    cues = segment_to_cues(segment, LAYOUT)
    assert len(cues) == 1
    assert cues[0].lines == ["おはようございます"]


def test_long_segment_is_split_using_word_timestamps():
    text = "あ" * 90
    words = [Word(start=i * 0.1, end=(i + 1) * 0.1, text="あ") for i in range(90)]
    segment = Segment(start=0.0, end=9.0, text=text, words=words)
    cues = segment_to_cues(segment, LAYOUT)
    assert len(cues) >= 3
    assert all(len(cue.text.replace("\n", "")) <= LAYOUT.max_chars for cue in cues)
    assert cues[0].start == 0.0


def test_long_segment_without_words_splits_by_chars():
    segment = Segment(start=0.0, end=10.0, text="こんにちは" * 20)
    cues = segment_to_cues(segment, LAYOUT)
    assert len(cues) >= 3
    assert cues[0].start == 0.0
    assert abs(cues[-1].end - 10.0) < 0.01


def test_timing_is_normalized_without_overlap():
    segments = [
        Segment(start=0.0, end=0.2, text="はい"),
        Segment(start=0.3, end=0.5, text="どうも"),
        Segment(start=5.0, end=30.0, text="ながい"),
    ]
    cues = build_cues(segments, LAYOUT)
    for previous, current in itertools.pairwise(cues):
        assert current.start >= previous.end
    assert all(cue.end - cue.start <= LAYOUT.max_duration + 1e-6 for cue in cues)


def test_render_srt_shape():
    cues = [Cue(start=0.0, end=1.5, lines=["おはよう", "ございます"])]
    output = render_srt(cues)
    assert output.startswith("1\r\n00:00:00,000 --> 00:00:01,500\r\n")
    assert "おはよう\r\nございます" in output
    assert output.endswith("\r\n")


def test_empty_segment_produces_no_cue():
    assert segment_to_cues(Segment(start=0.0, end=1.0, text="   "), LAYOUT) == []


def _words(text: str, step: float = 0.3) -> list[Word]:
    """句読点を含む擬似的な単語列（Whisper の word timestamps を模す）。"""
    pieces, buffer = [], ""
    for char in text:
        buffer += char
        if char in "、。" or len(buffer) >= 4:
            pieces.append(buffer)
            buffer = ""
    if buffer:
        pieces.append(buffer)
    return [
        Word(start=i * step, end=(i + 1) * step, text=piece)
        for i, piece in enumerate(pieces)
    ]


def test_split_prefers_punctuation_over_hard_limit():
    text = (
        "こんにちは、みなさん。今日はモンスターハンターの配信をやっていきたいと思います。"
        "今回は新しいモンスターに挑戦します。"
    )
    words = _words(text)
    segment = Segment(start=0.0, end=words[-1].end, text=text, words=words)
    cues = segment_to_cues(segment, LAYOUT)

    assert len(cues) >= 2
    # 「と思います」だけが次のキューに取り残されない
    assert not any(cue.text.replace("\n", "").startswith("と思います") for cue in cues)
    for cue in cues:
        assert len(cue.text.replace("\n", "")) <= LAYOUT.max_chars


def test_split_covers_all_words_exactly_once():
    text = "あいうえお、かきくけこ。さしすせそたちつてとなにぬねの。はひふへほまみむめも"
    words = _words(text)
    segment = Segment(start=0.0, end=words[-1].end, text=text, words=words)
    cues = segment_to_cues(segment, LayoutOptions(max_chars_per_line=10, max_lines=2))

    joined = "".join(cue.text for cue in cues).replace("\n", "").replace(" ", "")
    assert joined == text.replace("、", "").replace("。", "")


def test_line_break_does_not_split_okurigana():
    text = "今回は大型アップデートで追加された新しいモンスターに挑戦していきますので"
    lines = wrap_lines(text, LAYOUT)
    assert len(lines) == 2
    # 「新|しい」のように漢字と送り仮名の間で割らない
    assert not lines[1].startswith("しい")
    assert all(len(line) <= LAYOUT.max_chars_per_line for line in lines)


def test_line_break_does_not_split_katakana_word():
    text = "きのうのコラボ配信でモンスターハンターをやりました"
    lines = wrap_lines(text, LayoutOptions(max_chars_per_line=16, max_lines=2))
    assert "".join(lines) == text
    # 助詞「で」の後ろで折り返し、カタカナ語は割らない
    assert not (_is_kata(lines[0][-1]) and _is_kata(lines[1][0]))


def test_line_limit_wins_when_no_good_break_exists():
    # カタカナ語が長すぎて、割らずに収める方法が無いケース。
    # 見た目より「1行の文字数を守る」を優先する。
    text = "きのうのコラボ配信でモンスターハンターをやりました"
    options = LayoutOptions(max_chars_per_line=14, max_lines=2)
    lines = wrap_lines(text, options)
    assert "".join(lines) == text
    assert all(len(line) <= options.max_chars_per_line for line in lines)


def _is_kata(char: str) -> bool:
    return 0x30A0 <= ord(char) <= 0x30FF


def test_every_line_fits_the_limit_for_many_lengths():
    base = "今回は大型アップデートで追加された新しいモンスターに挑戦していきますので"
    for limit in (10, 13, 16, 20, 24):
        options = LayoutOptions(max_chars_per_line=limit, max_lines=2)
        for length in range(limit + 1, limit * 2 + 1):
            text = base[:length]
            lines = wrap_lines(text, options)
            assert all(len(line) <= limit for line in lines), (limit, length, lines)
            assert "".join(lines) == text.replace(" ", "")


def test_vertical_layout_respects_narrow_lines():
    text = "モンスターハンターの配信をやっていきたいと思います"
    lines = wrap_lines(text, VERTICAL_LAYOUT)
    assert all(len(line) <= VERTICAL_LAYOUT.max_chars_per_line for line in lines)
    assert len(lines) <= VERTICAL_LAYOUT.max_lines


def test_long_sentence_is_split_evenly_not_greedily():
    # 上限まで詰めると「と思います」だけが次の字幕に取り残される長さ。
    text = "今日はモンスターハンターの配信をやっていきたいと思います。"
    words = _words(text)
    segment = Segment(start=0.0, end=words[-1].end, text=text, words=words)
    cues = segment_to_cues(segment, VERTICAL_LAYOUT)

    assert len(cues) == 2
    lengths = [len(cue.text.replace("\n", "")) for cue in cues]
    # 片方だけが極端に短くならない
    assert min(lengths) >= max(lengths) * 0.6, cues
    assert not cues[-1].text.startswith("と思います")


def test_short_sentences_share_one_cue():
    text = "こんにちは。元気です。"
    words = _words(text)
    segment = Segment(start=0.0, end=words[-1].end, text=text, words=words)
    cues = segment_to_cues(segment, LAYOUT)
    assert len(cues) == 1


def test_split_never_exceeds_the_budget():
    text = (
        "今回は大型アップデートで追加された新しいモンスターに挑戦していきますので、"
        "最後まで見ていってください。それでは早速いってみましょう。"
    )
    words = _words(text)
    segment = Segment(start=0.0, end=words[-1].end, text=text, words=words)
    for options in (LAYOUT, VERTICAL_LAYOUT, LayoutOptions(max_chars_per_line=10, max_lines=2)):
        cues = segment_to_cues(segment, options)
        for cue in cues:
            assert len(cue.text.replace("\n", "")) <= options.max_chars
            assert len(cue.lines) <= options.max_lines
            assert all(len(line) <= options.max_chars_per_line for line in cue.lines)


def test_budget_invariant_holds_across_many_inputs():
    """語の長さ・文の長さ・折り返し幅を総当たりに近い形で振って、上限が破られないか見る。

    「必要枚数に等分できない語の並び」で上限を超えるバグが出たので、その再発防止。
    """
    import random

    rng = random.Random(20260821)
    alphabet = "今回大型追加新モンスター挑戦最後見配信声分けあいうえおかきくけこ"

    for trial in range(300):
        # 1〜9文字のばらついた語を並べ、ときどき句読点で区切る
        words: list[Word] = []
        clock = 0.0
        while len(words) < rng.randint(3, 40):
            size = rng.randint(1, 9)
            piece = "".join(rng.choice(alphabet) for _ in range(size))
            if rng.random() < 0.25:
                piece += rng.choice("、。")
            words.append(Word(start=clock, end=clock + 0.3, text=piece))
            clock += 0.3

        options = LayoutOptions(
            max_chars_per_line=rng.randint(6, 24),
            max_lines=rng.choice([1, 2, 3]),
        )
        segment = Segment(
            start=0.0,
            end=clock,
            text="".join(word.text for word in words),
            words=words,
        )

        for cue in segment_to_cues(segment, options):
            assert len(cue.lines) <= options.max_lines, (trial, cue)
            for line in cue.lines:
                assert len(line) <= options.max_chars_per_line, (trial, cue)
            assert cue.end >= cue.start, (trial, cue)


def test_cue_does_not_start_with_a_particle():
    text = "今回は大型アップデートで追加された新しいモンスターに挑戦していきますので。"
    words = _words(text)
    segment = Segment(start=0.0, end=words[-1].end, text=text, words=words)
    for options in (LAYOUT, VERTICAL_LAYOUT):
        for cue in segment_to_cues(segment, options)[1:]:
            assert cue.text[0] not in "をにはがでとへの", (options, cue)


def test_verb_starting_with_ya_is_not_treated_as_a_particle():
    # 「や」は助詞にもなるが、「やっていく」の語頭でもある。切れ目として不当に嫌わない。
    text = "今日はモンスターハンターの配信をやっていきたいと思います。"
    words = _words(text)
    segment = Segment(start=0.0, end=words[-1].end, text=text, words=words)
    cues = segment_to_cues(segment, VERTICAL_LAYOUT)
    lengths = [len(cue.text.replace("\n", "")) for cue in cues]
    assert min(lengths) >= max(lengths) * 0.6, cues
