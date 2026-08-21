import pytest

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


def test_parse_speakers():
    from koewake.cli import parse_speakers

    assert parse_speakers(None) is None
    assert parse_speakers("auto") == 0
    assert parse_speakers("自動") == 0
    assert parse_speakers("2") == 2
    assert parse_speakers(" 3 ") == 3

    for bad in ("いち", "0", "-1", ""):
        with pytest.raises(ValueError):
            parse_speakers(bad)


def test_parse_speaker_names():
    from koewake.cli import parse_speaker_names

    assert parse_speaker_names(None) == []
    assert parse_speaker_names("") == []
    assert parse_speaker_names("ホスト, ゲスト ,") == ["ホスト", "ゲスト"]


def test_speaker_filename():
    from koewake.cli import speaker_filename

    assert speaker_filename("配信", None, []) == "配信.srt"
    assert speaker_filename("配信", "話者1", []) == "配信_話者1.srt"
    assert speaker_filename("配信", "話者2", ["ホスト", "ゲスト"]) == "配信_ゲスト.srt"
    # 名前が足りない分は 話者N のまま
    assert speaker_filename("配信", "話者3", ["ホスト", "ゲスト"]) == "配信_話者3.srt"


def test_speaker_filename_sanitises_unsafe_characters():
    from koewake.cli import speaker_filename

    name = speaker_filename("配信", "話者1", ["a/b:c*d?e"])
    assert "/" not in name and ":" not in name and "*" not in name and "?" not in name
    assert name.endswith(".srt")


def test_speaker_names_without_speakers_is_an_error(tmp_path, capsys):
    assert main([str(tmp_path / "x.mp4"), "--speaker-names", "ホスト,ゲスト"]) == 1
    assert "--speakers と一緒に" in capsys.readouterr().out


def test_bad_speakers_value_is_an_error(tmp_path, capsys):
    assert main([str(tmp_path / "x.mp4"), "--speakers", "たくさん"]) == 1
    assert "--speakers には" in capsys.readouterr().out


def test_existing_per_speaker_srt_is_skipped(tmp_path, capsys):
    video = tmp_path / "コラボ.mp4"
    video.write_bytes(b"not really a video")
    (tmp_path / "コラボ_話者1.srt").write_text("既にある", encoding="utf-8")
    assert main([str(video), "--speakers", "2"]) == 0
    assert "すでにSRTがあります" in capsys.readouterr().out
