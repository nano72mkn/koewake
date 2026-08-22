import builtins
from pathlib import Path

from koewake.prompt import ask_inputs, ask_speakers, parse_dropped_paths


def _answer(monkeypatch, text: str) -> None:
    monkeypatch.setattr(builtins, "input", lambda *_: text)


def test_parse_dropped_paths_plain():
    assert parse_dropped_paths("/tmp/a.mp4") == [Path("/tmp/a.mp4")]


def test_parse_dropped_paths_multiple():
    got = parse_dropped_paths("/tmp/a.mp4 /tmp/b.mp4")
    assert got == [Path("/tmp/a.mp4"), Path("/tmp/b.mp4")]


def test_parse_dropped_paths_with_escaped_spaces():
    # macOS の Terminal は空白を \ でエスケープして貼り付ける
    got = parse_dropped_paths(r"/tmp/my\ video.mp4")
    assert got == [Path("/tmp/my video.mp4")]


def test_parse_dropped_paths_with_quotes():
    got = parse_dropped_paths('"/tmp/my video.mp4"')
    assert got == [Path("/tmp/my video.mp4")]


def test_parse_dropped_paths_empty():
    assert parse_dropped_paths("") == []
    assert parse_dropped_paths("   ") == []


def test_parse_dropped_paths_unbalanced_quote_is_not_an_error():
    # 引用符が閉じていなくても落ちない
    assert parse_dropped_paths('"/tmp/a.mp4') != []


def test_ask_speakers_enter_means_no_split(monkeypatch):
    _answer(monkeypatch, "")
    assert ask_speakers() is None


def test_ask_speakers_number(monkeypatch):
    _answer(monkeypatch, "2")
    assert ask_speakers() == "2"


def test_ask_speakers_auto_letters(monkeypatch):
    for text in ("a", "A", "auto", "自動"):
        _answer(monkeypatch, text)
        assert ask_speakers() == "auto"


def test_ask_speakers_passes_through_per_track(monkeypatch):
    _answer(monkeypatch, "1,2")
    assert ask_speakers() == "1,2"


def test_prompts_survive_closed_stdin(monkeypatch):
    def boom(*_):
        raise EOFError

    monkeypatch.setattr(builtins, "input", boom)
    assert ask_speakers() is None
    assert ask_inputs() == []
