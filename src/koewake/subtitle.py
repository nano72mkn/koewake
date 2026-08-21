"""文字起こし結果を「読める字幕」に整形して SRT にする。

Whisper が返すセグメントはそのままだと 1 枚に 60 文字入っていたりして
字幕としては読めない。ここで
  1) 長すぎるセグメントを分割し
  2) 1枚あたりの行数・文字数に収まるよう折り返し
  3) 表示時間を人間が読める長さに整える
という 3 段階をかける。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field, replace

# 行頭に来てはいけない文字（禁則処理）
NO_LINE_START = set(
    "、。，．・：；！？」』）］｝〉》”’ー~〜"
    "ぁぃぅぇぉっゃゅょゎァィゥェォッャュョヮヵヶ!?,."
)
# 行末に来てはいけない文字
NO_LINE_END = set("「『（［｛〈《“‘([{")
# 分割の手がかりにする句読点
BREAK_AFTER = set("、。！？!?」』…")
# 文の終わり
SENTENCE_END = set("。！？!?")
# 行頭・字幕の先頭に置きたくない助詞。
# 「や」「も」「か」「ね」「よ」は語頭にも普通に立つ（やっていく・もう・かなり）ので入れない。
LEADING_PARTICLES = set("をにはがでとへの")
# キューの切れ目を選ぶときの、境界スコアの重み（距離は文字数なので、それに揃える）
CUT_BOUNDARY_WEIGHT = 0.25
# 文末になりやすい語尾（句読点が無い喋り言葉のための保険）
SOFT_BREAK_TAILS = (
    "です", "ます", "でした", "ました", "だね",
    "ですね", "ますね", "けど", "から", "ので",
)

_SPACES = re.compile(r"[ 　\t]+")


@dataclass(frozen=True)
class Word:
    start: float
    end: float
    text: str


@dataclass
class Segment:
    start: float
    end: float
    text: str
    words: list[Word] = field(default_factory=list)
    # Phase 2（話者分離）で埋める席。Phase 1 では常に None。
    speaker: str | None = None


@dataclass
class Cue:
    start: float
    end: float
    lines: list[str]
    speaker: str | None = None

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


@dataclass(frozen=True)
class LayoutOptions:
    """字幕の見た目と尺のルール。"""

    max_chars_per_line: int = 20
    max_lines: int = 2
    min_duration: float = 1.0
    max_duration: float = 6.0
    min_gap: float = 0.08
    strip_punctuation: bool = True

    @property
    def max_chars(self) -> int:
        return self.max_chars_per_line * self.max_lines


# 縦動画（ショート）は横幅が狭いので 1 行を短くする
VERTICAL_LAYOUT = LayoutOptions(max_chars_per_line=13, max_lines=2)


def normalize_text(text: str, *, strip_punctuation: bool) -> str:
    text = _SPACES.sub(" ", text).strip()
    if strip_punctuation:
        # 句読点は改行・カット位置の手がかりに使った後なので、表示からは落とす。
        # （日本語字幕では句読点を置かず余白で区切るのが一般的）
        text = text.replace("、", " ").replace("。", " ")
        text = _SPACES.sub(" ", text).strip()
    return text


def _char_class(char: str) -> str:
    code = ord(char)
    if 0x3041 <= code <= 0x309F:
        return "hira"
    if 0x30A0 <= code <= 0x30FF:
        return "kata"
    if 0x4E00 <= code <= 0x9FFF or code == 0x3005:
        return "kanji"
    if char.isascii() and char.isalnum():
        return "latin"
    return "other"


def _script_bonus(prev_char: str, next_char: str) -> float:
    """日本語には単語の区切りが無いので、文字種の変わり目を語の境目とみなす。"""
    before, after = _char_class(prev_char), _char_class(next_char)
    if before == "hira" and after in ("kanji", "kata", "latin"):
        # 助詞のあと。日本語で最も自然な折り返し位置。
        return 25.0
    if before == "kanji" and after == "hira":
        # 送り仮名を割ってしまう（「新|しい」）。
        return -30.0
    if before == after and before in ("kata", "latin"):
        # カタカナ語の途中（「モンス|ター」）。
        return -40.0
    if before == after and before == "kanji":
        return -15.0
    return 0.0


def _boundary_bonus(prev_char: str, next_char: str) -> float:
    """この 2 文字の間で切ってよいか。プラスなら切りたい位置。"""
    bonus = _script_bonus(prev_char, next_char)
    if next_char in LEADING_PARTICLES:
        # 助詞から始まる字幕（「に挑戦していきますので」）は読みにくい。
        # 助詞は直前の語にくっつけたままにする。
        bonus -= 30.0
    return bonus


def _break_score(text: str, pos: int, ideal: int) -> float:
    """`pos` の直前で改行/分割したときの良さ。大きいほど良い。"""
    if pos <= 0 or pos >= len(text):
        return -math.inf

    prev_char = text[pos - 1]
    next_char = text[pos]

    if next_char in NO_LINE_START or prev_char in NO_LINE_END:
        return -math.inf

    score = -abs(pos - ideal) * 3.0
    if prev_char in BREAK_AFTER:
        score += 120.0
    elif prev_char == " ":
        score += 60.0
    elif text[:pos].endswith(SOFT_BREAK_TAILS):
        score += 40.0
    if next_char in "「『（":
        score += 30.0
    return score + _boundary_bonus(prev_char, next_char)


def _best_break(text: str, ideal: int, window: int, lo: int = 1, hi: int | None = None) -> int:
    """`lo`〜`hi` の範囲で、`ideal` に近くて自然な分割位置を選ぶ。"""
    hi = min(len(text) - 1 if hi is None else hi, len(text) - 1)
    lo = max(1, lo)
    if lo > hi:
        return max(1, min(ideal, len(text) - 1))

    search_lo = max(lo, ideal - window)
    search_hi = min(hi, ideal + window)
    if search_lo > search_hi:
        search_lo, search_hi = lo, hi

    best_pos, best_score = -1, -math.inf
    for pos in range(search_lo, search_hi + 1):
        score = _break_score(text, pos, ideal)
        if score > best_score:
            best_pos, best_score = pos, score

    # 禁則で全滅した場合は理想位置で強制的に切る（切れないより切れた方がマシ）
    return best_pos if best_pos > 0 else min(max(ideal, lo), hi)


def wrap_lines(text: str, options: LayoutOptions) -> list[str]:
    """1 枚のキュー内で行に折り返す。

    「今の行に何文字載せるか」を決めるとき、残りの行数に収まる範囲だけを
    探索する。これをしないと最後の行だけ規定文字数を超える。
    """
    text = text.strip()
    limit = options.max_chars_per_line
    if len(text) <= limit:
        return [text]

    line_count = min(options.max_lines, math.ceil(len(text) / limit))
    lines: list[str] = []
    rest = text

    for remaining in range(line_count, 1, -1):
        # 残り (remaining - 1) 行に収めるには、今の行に最低でもこれだけ載せる必要がある
        lowest = max(1, len(rest) - (remaining - 1) * limit)
        highest = min(limit, len(rest) - 1)
        ideal = min(max(round(len(rest) / remaining), lowest), highest)
        pos = _best_break(rest, ideal, window=max(2, limit // 3), lo=lowest, hi=highest)
        lines.append(rest[:pos].strip())
        rest = rest[pos:].strip()

    lines.append(rest)

    # 保険：それでも溢れたら機械的に切る（Filmora で行が見切れるより良い）
    guarded: list[str] = []
    for line in lines:
        remainder = line
        while len(remainder) > limit:
            guarded.append(remainder[:limit])
            remainder = remainder[limit:]
        if remainder:
            guarded.append(remainder)
    return guarded


def _word_length(word: Word) -> int:
    return len(word.text.strip())


def _sentences(words: list[Word]) -> list[list[Word]]:
    """句点で文に区切る。"""
    sentences: list[list[Word]] = []
    current: list[Word] = []
    for word in words:
        current.append(word)
        if word.text.strip()[-1] in SENTENCE_END:
            sentences.append(current)
            current = []
    if current:
        sentences.append(current)
    return sentences


def _find_cuts(
    words: list[Word],
    cumulative: list[int],
    pieces: int,
    max_chars: int,
) -> tuple[list[int], float] | None:
    """`pieces` 枚に等分する切れ目を探す。

    切れ目と、その平均的な「切り心地」を返す。どこかで詰むなら None。
    """
    total = cumulative[-1]
    target = total / pieces
    cuts: list[int] = []
    scores: list[float] = []
    previous = 0

    for piece in range(1, pieces):
        want = target * piece
        best_index, best_score = None, -math.inf
        for index in range(previous + 1, len(words)):
            if cumulative[index] - cumulative[previous] > max_chars:
                break
            # 残りが、残り枚数に収まらなくなる切り方はしない
            if total - cumulative[index] > max_chars * (pieces - piece):
                continue

            previous_char = words[index - 1].text.strip()[-1]
            next_char = words[index].text.strip()[0]

            score = -abs(cumulative[index] - want)
            if previous_char in BREAK_AFTER:
                score += max_chars * 0.3
            if next_char in NO_LINE_START:
                score -= max_chars * 2
            score += _boundary_bonus(previous_char, next_char) * CUT_BOUNDARY_WEIGHT

            if score > best_score:
                best_index, best_score = index, score

        if best_index is None:
            return None
        cuts.append(best_index)
        scores.append(best_score)
        previous = best_index

    if cumulative[-1] - cumulative[previous] > max_chars:
        return None
    return cuts, (sum(scores) / len(scores) if scores else 0.0)


def _chunks_from_cuts(words: list[Word], cuts: list[int]) -> list[list[Word]]:
    chunks: list[list[Word]] = []
    start = 0
    for cut in [*cuts, len(words)]:
        if cut > start:
            chunks.append(words[start:cut])
            start = cut
    return chunks


def _split_sentence(words: list[Word], max_chars: int) -> list[list[Word]]:
    """1文が長すぎるとき、必要な枚数に「均等に」割る。

    上限まで詰めてから切ると「〜やっていきたい / と思います」のように
    末尾のひと言だけが次の字幕に取り残される。先に必要枚数を決めて
    等分することで、これを防ぐ。

    最小枚数ぴったりだと切れる位置がほとんど残らず、助詞の前で切るような
    不自然な結果になることがある。1〜2枚増やした場合も試して、
    切り心地の良い方を採る。
    """
    lengths = [_word_length(word) for word in words]
    total = sum(lengths)
    if total <= max_chars:
        return [words]

    cumulative = [0]
    for length in lengths:
        cumulative.append(cumulative[-1] + length)

    minimum = math.ceil(total / max_chars)
    # 枚数が増えるほど字幕は細切れになるので、その分だけ不利に評価する
    extra_piece_cost = max_chars * 0.15

    best_chunks: list[list[Word]] | None = None
    best_quality = -math.inf

    for pieces in range(minimum, len(words) + 1):
        found = _find_cuts(words, cumulative, pieces, max_chars)
        if found is not None:
            cuts, quality = found
            adjusted = quality - (pieces - minimum) * extra_piece_cost
            if adjusted > best_quality:
                best_chunks, best_quality = _chunks_from_cuts(words, cuts), adjusted
        if pieces >= minimum + 2 and best_chunks is not None:
            break

    if best_chunks is not None:
        return best_chunks

    # 1語だけで上限を超える場合。折り返し側の保険で機械的に切ってもらう。
    return [[word] for word in words]


def _split_words(words: list[Word], options: LayoutOptions) -> list[list[Word]]:
    """単語タイムスタンプを使って、文字数上限に収まるまとまりに切る。

    文を単位にして、
      - 短い文どうしは 1 枚にまとめる
      - 長い文は必要な枚数に等分する
    という方針。文の途中で切るのは、どうやっても収まらないときだけ。
    """
    words = [word for word in words if word.text.strip()]
    if not words:
        return []

    max_chars = options.max_chars
    chunks: list[list[Word]] = []
    pending: list[Word] = []
    pending_length = 0

    for sentence in _sentences(words):
        sentence_length = sum(_word_length(word) for word in sentence)

        if sentence_length > max_chars:
            if pending:
                chunks.append(pending)
            split = _split_sentence(sentence, max_chars)
            chunks.extend(split[:-1])
            # 割った最後のかたまりは、次の短い文と同居できる余地を残す
            pending = split[-1]
            pending_length = sum(_word_length(word) for word in pending)
        elif pending_length + sentence_length <= max_chars:
            pending += sentence
            pending_length += sentence_length
        else:
            chunks.append(pending)
            pending, pending_length = sentence, sentence_length

    if pending:
        chunks.append(pending)
    return [chunk for chunk in chunks if chunk]


def _split_text_evenly(text: str, max_chars: int) -> list[str]:
    """上限に収まるまで、テキストを均等に割る（折り返しと同じ考え方）。"""
    if len(text) <= max_chars:
        return [text]

    pieces_needed = math.ceil(len(text) / max_chars)
    pieces: list[str] = []
    rest = text

    for remaining in range(pieces_needed, 1, -1):
        lowest = max(1, len(rest) - (remaining - 1) * max_chars)
        highest = min(max_chars, len(rest) - 1)
        ideal = min(max(round(len(rest) / remaining), lowest), highest)
        pos = _best_break(rest, ideal, window=max(2, max_chars // 3), lo=lowest, hi=highest)
        pieces.append(rest[:pos].strip())
        rest = rest[pos:].strip()

    pieces.append(rest)
    return [piece for piece in pieces if piece]


def _text_to_cues(
    text: str,
    start: float,
    end: float,
    speaker: str | None,
    options: LayoutOptions,
) -> list[Cue]:
    """テキストを（必要なら分割して）キューにする。尺は文字数で按分する。"""
    text = normalize_text(text, strip_punctuation=options.strip_punctuation)
    if not text:
        return []

    pieces = _split_text_evenly(text, options.max_chars)
    if len(pieces) == 1:
        return [Cue(start=start, end=end, lines=wrap_lines(text, options), speaker=speaker)]

    total_chars = sum(len(piece) for piece in pieces) or 1
    duration = max(end - start, 0.001)
    cues: list[Cue] = []
    cursor = start
    for piece in pieces:
        span = duration * (len(piece) / total_chars)
        cues.append(
            Cue(
                start=cursor,
                end=cursor + span,
                lines=wrap_lines(piece, options),
                speaker=speaker,
            )
        )
        cursor += span
    return cues


def segment_to_cues(segment: Segment, options: LayoutOptions) -> list[Cue]:
    text = normalize_text(segment.text, strip_punctuation=options.strip_punctuation)
    if not text:
        return []

    if len(text) <= options.max_chars:
        return [
            Cue(
                start=segment.start,
                end=segment.end,
                lines=wrap_lines(text, options),
                speaker=segment.speaker,
            )
        ]

    if segment.words:
        cues: list[Cue] = []
        for chunk in _split_words(segment.words, options):
            cues.extend(
                _text_to_cues(
                    "".join(word.text for word in chunk),
                    chunk[0].start,
                    chunk[-1].end,
                    segment.speaker,
                    options,
                )
            )
        if cues:
            return cues

    # 単語タイムスタンプが無いとき
    return _text_to_cues(
        segment.text, segment.start, segment.end, segment.speaker, options
    )


def normalize_timing(cues: list[Cue], options: LayoutOptions) -> list[Cue]:
    """短すぎる/長すぎる/重なっている表示時間を直す。"""
    cues = sorted(cues, key=lambda cue: (cue.start, cue.end))
    fixed: list[Cue] = []

    for i, cue in enumerate(cues):
        start = max(0.0, cue.start)
        end = max(cue.end, start + 0.2)

        if fixed and start < fixed[-1].end + options.min_gap:
            start = fixed[-1].end + options.min_gap
            end = max(end, start + 0.2)

        next_start = cues[i + 1].start if i + 1 < len(cues) else math.inf

        if end - start < options.min_duration:
            # 次のキューを踏まない範囲で伸ばす
            end = min(start + options.min_duration, next_start - options.min_gap)
            end = max(end, start + 0.2)
        if end - start > options.max_duration:
            end = start + options.max_duration

        fixed.append(replace(cue, start=start, end=end))

    return fixed


def build_cues(segments: list[Segment], options: LayoutOptions) -> list[Cue]:
    cues: list[Cue] = []
    for segment in segments:
        cues.extend(segment_to_cues(segment, options))
    return normalize_timing(cues, options)


def build_cues_by_speaker(
    segments: list[Segment], options: LayoutOptions
) -> dict[str | None, list[Cue]]:
    """話者ごとに分けてキューを組み立てる。

    表示時間の調整（重なりの除去）は**話者ごとに独立して**かける。
    別々のSRTとして別トラックに載せるので、話者どうしの発言が時間的に
    重なるのはむしろ自然であり、そこをずらしてしまうと音とズレる。
    """
    grouped: dict[str | None, list[Segment]] = {}
    for segment in segments:
        grouped.setdefault(segment.speaker, []).append(segment)

    result: dict[str | None, list[Cue]] = {}
    for speaker, items in grouped.items():
        cues = build_cues(items, options)
        if cues:
            result[speaker] = cues
    return result


def format_timestamp(seconds: float) -> str:
    if seconds < 0:
        seconds = 0.0
    milliseconds = round(seconds * 1000)
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    secs, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{milliseconds:03d}"


def render_srt(cues: list[Cue]) -> str:
    lines: list[str] = []
    for index, cue in enumerate(cues, start=1):
        lines.append(str(index))
        lines.append(f"{format_timestamp(cue.start)} --> {format_timestamp(cue.end)}")
        lines.extend(cue.lines)
        lines.append("")
    # SRT は CRLF が無難（Windows 側のツールで崩れにくい）
    return "\r\n".join(lines)
