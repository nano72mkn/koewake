from koewake.cli import Outcome, build_parser, collect_inputs, main, resolve_layout
from koewake.subtitle import VERTICAL_LAYOUT, LayoutOptions


def _args(*argv: str):
    return build_parser().parse_args([*argv])


def test_missing_file_is_an_error(tmp_path, capsys):
    assert main([str(tmp_path / "ない.mp4")]) == 1
    assert "ファイルが見つかりません" in capsys.readouterr().out


def test_unsupported_file_is_skipped_not_failed(tmp_path, capsys):
    other = tmp_path / "メモ.txt"
    other.write_text("hello", encoding="utf-8")
    assert main([str(other)]) == 0
    assert "対応していない形式" in capsys.readouterr().out


def test_existing_srt_is_skipped(tmp_path, capsys):
    video = tmp_path / "配信.mp4"
    video.write_bytes(b"not really a video")
    (tmp_path / "配信.srt").write_text("既にある", encoding="utf-8")
    assert main([str(video)]) == 0
    assert "すでにSRTがあります" in capsys.readouterr().out


def test_missing_vocab_file_is_an_error(tmp_path, capsys):
    assert main([str(tmp_path / "x.mp4"), "--vocab", str(tmp_path / "ない.txt")]) == 1
    assert "単語リストが見つかりません" in capsys.readouterr().out


def test_collect_inputs_expands_directories(tmp_path):
    (tmp_path / "b.mp4").write_bytes(b"")
    (tmp_path / "a.mov").write_bytes(b"")
    (tmp_path / "メモ.txt").write_text("x", encoding="utf-8")
    found = collect_inputs([tmp_path])
    assert [path.name for path in found] == ["a.mov", "b.mp4"]


def test_layout_flags_override_orientation(tmp_path):
    video = tmp_path / "x.mp4"
    assert resolve_layout(_args("v", "--layout", "vertical"), video) == VERTICAL_LAYOUT
    assert resolve_layout(_args("v", "--layout", "horizontal"), video) == LayoutOptions()

    custom = resolve_layout(
        _args("v", "--layout", "horizontal", "--chars-per-line", "9", "--max-lines", "3"), video
    )
    assert custom.max_chars_per_line == 9
    assert custom.max_lines == 3

    kept = resolve_layout(_args("v", "--layout", "horizontal", "--keep-punctuation"), video)
    assert kept.strip_punctuation is False


def test_outcome_statuses_are_distinct():
    assert len({Outcome.MADE, Outcome.SKIPPED, Outcome.FAILED}) == 3
