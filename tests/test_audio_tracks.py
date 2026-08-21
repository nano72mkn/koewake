from koewake.audio import AudioTrack


def test_label_falls_back_to_a_number():
    assert AudioTrack(index=0).label() == "トラック1"
    assert AudioTrack(index=2).label() == "トラック3"


def test_label_uses_the_embedded_title():
    assert AudioTrack(index=1, title="BCさんマイク").label() == "BCさんマイク"


def _parse(stderr: str):
    """probe_audio_tracks の解析部分だけを、ffmpeg を起動せずに確かめる。"""
    from koewake.audio import _STREAM_RE, _TRACK_TITLE_RE

    tracks, in_audio = [], False
    for line in stderr.splitlines():
        stream = _STREAM_RE.match(line)
        if stream:
            in_audio = stream.group(1) == "Audio"
            if in_audio:
                tracks.append(AudioTrack(index=len(tracks)))
            continue
        if in_audio and tracks:
            title = _TRACK_TITLE_RE.match(line)
            if title and tracks[-1].title is None:
                tracks[-1] = AudioTrack(index=tracks[-1].index, title=title.group(1))
    return tracks


FFMPEG_OUTPUT = """
  Stream #0:0[0x1](und): Video: h264 (High) (avc1 / 0x31637661), 1920x1080, 3 kb/s
    Metadata:
      handler_name    : VideoHandler
  Stream #0:1[0x2](und): Audio: aac (LC), 16000 Hz, mono, 27 kb/s (default)
    Metadata:
      handler_name    : SoundHandler
      name            : Aさんマイク
  Stream #0:2[0x3](und): Audio: aac (LC), 16000 Hz, mono, 31 kb/s
    Metadata:
      handler_name    : SoundHandler
      name            : BCさんマイク
"""


def test_parses_multiple_audio_tracks_with_titles():
    tracks = _parse(FFMPEG_OUTPUT)
    assert [t.index for t in tracks] == [0, 1]
    assert [t.title for t in tracks] == ["Aさんマイク", "BCさんマイク"]


def test_video_stream_is_not_counted_as_audio():
    tracks = _parse(FFMPEG_OUTPUT)
    assert len(tracks) == 2


def test_untitled_tracks_keep_none():
    stderr = """
  Stream #0:0[0x1](und): Video: h264, 1920x1080
  Stream #0:1[0x2](und): Audio: aac (LC), 16000 Hz, mono
    Metadata:
      handler_name    : SoundHandler
"""
    tracks = _parse(stderr)
    assert len(tracks) == 1
    assert tracks[0].title is None
    assert tracks[0].label() == "トラック1"
