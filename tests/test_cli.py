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
    assert parse_speakers("auto") == [0]
    assert parse_speakers("自動") == [0]
    assert parse_speakers("2") == [2]
    assert parse_speakers(" 3 ") == [3]
    # トラックごとの人数
    assert parse_speakers("1,2") == [1, 2]
    assert parse_speakers("auto,2") == [0, 2]

    for bad in ("いち", "0", "-1", "", ",", "1,x"):
        with pytest.raises(ValueError):
            parse_speakers(bad)


def test_speakers_for_track():
    from koewake.cli import speakers_for_track

    # 1つだけなら全トラックに同じ値
    assert speakers_for_track([2], 0, 3) == 2
    assert speakers_for_track([2], 2, 3) == 2
    # トラック数と同じ個数ならトラックごと
    assert speakers_for_track([1, 2], 0, 2) == 1
    assert speakers_for_track([1, 2], 1, 2) == 2
    # 数が合わなければエラー
    with pytest.raises(ValueError, match="トラック"):
        speakers_for_track([1, 2], 0, 3)


def _slot(**kwargs):
    from koewake.audio import AudioTrack
    from koewake.cli import NameSlot

    base = {
        "track": AudioTrack(index=0),
        "track_count": 1,
        "speaker": None,
        "speakers_in_track": 1,
        "name": None,
    }
    return NameSlot(**{**base, **kwargs})


def test_output_name_single_track():
    from koewake.cli import output_name

    # 1トラック・1人 -> そのまま
    assert output_name("配信", _slot()) == "配信.srt"
    # 1トラック・複数人 -> 話者だけ付く
    assert (
        output_name("配信", _slot(speaker="話者2", speakers_in_track=2)) == "配信_話者2.srt"
    )


def test_output_name_multi_track():
    from koewake.audio import AudioTrack
    from koewake.cli import output_name

    track2 = AudioTrack(index=1)
    # 複数トラック・そのトラックは1人 -> トラック名だけ
    assert (
        output_name("配信", _slot(track=track2, track_count=2, speaker="話者1"))
        == "配信_トラック2.srt"
    )
    # 複数トラック・そのトラックに複数人 -> 両方付く
    assert (
        output_name(
            "配信",
            _slot(track=track2, track_count=2, speaker="話者2", speakers_in_track=2),
        )
        == "配信_トラック2_話者2.srt"
    )


def test_output_name_uses_embedded_track_title():
    from koewake.audio import AudioTrack
    from koewake.cli import output_name

    track = AudioTrack(index=1, title="BCさんマイク")
    assert (
        output_name("配信", _slot(track=track, track_count=2, speaker="話者1"))
        == "配信_BCさんマイク.srt"
    )


def test_output_name_prefers_given_name_over_track():
    from koewake.audio import AudioTrack
    from koewake.cli import output_name

    track = AudioTrack(index=1, title="BCさんマイク")
    slot = _slot(track=track, track_count=2, speaker="話者2", speakers_in_track=2, name="C")
    assert output_name("配信", slot) == "配信_C.srt"


def test_output_name_sanitises_unsafe_characters():
    from koewake.audio import AudioTrack
    from koewake.cli import output_name

    track = AudioTrack(index=1, title='a/b:c*d?e')
    name = output_name("配信", _slot(track=track, track_count=2, speaker="話者1"))
    assert not any(ch in name for ch in '/:*?"<>|')
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


def test_plain_srt_also_blocks_a_rerun_when_splitting(tmp_path, capsys):
    """話者が1人だったときは接尾辞なしで出るので、そちらも既存として見る。"""
    video = tmp_path / "ソロ.mp4"
    video.write_bytes(b"not really a video")
    (tmp_path / "ソロ.srt").write_text("既にある", encoding="utf-8")
    assert main([str(video), "--speakers", "auto"]) == 0
    assert "すでにSRTがあります" in capsys.readouterr().out


def test_existing_outputs_finds_both_shapes(tmp_path):
    from koewake.cli import _existing_outputs

    (tmp_path / "配信.srt").write_text("x", encoding="utf-8")
    (tmp_path / "配信_話者2.srt").write_text("x", encoding="utf-8")
    (tmp_path / "別の動画.srt").write_text("x", encoding="utf-8")

    names = {path.name for path in _existing_outputs(tmp_path, "配信", wide=True)}
    assert names == {"配信.srt", "配信_話者2.srt"}

    names = {path.name for path in _existing_outputs(tmp_path, "配信", wide=False)}
    assert names == {"配信.srt"}
