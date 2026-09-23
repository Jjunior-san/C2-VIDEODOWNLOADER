from pathlib import Path

import pytest

import video_player
import youtube_downloader_app as app


class FakeMedia:
    def __init__(self, source):
        self.source = source
        self.options = []

    def add_option(self, option):
        self.options.append(option)


class FakeNativePlayer:
    def __init__(self):
        self.handle = None
        self.media = None
        self.playing = False
        self.paused = False
        self.position = 0.25
        self.volume = 100

    def set_hwnd(self, handle):
        self.handle = handle

    def set_media(self, media):
        self.media = media

    def play(self):
        self.playing = True
        self.paused = False
        return 0

    def set_pause(self, value):
        self.paused = bool(value)
        self.playing = not self.paused

    def stop(self):
        self.playing = False
        self.paused = False

    def release(self):
        pass

    def is_playing(self):
        return self.playing

    def get_state(self):
        if self.paused:
            return "State.Paused"
        return "State.Playing" if self.playing else "State.Stopped"

    def get_time(self):
        return 30_000

    def get_length(self):
        return 120_000

    def get_position(self):
        return self.position

    def set_position(self, position):
        self.position = position

    def audio_set_volume(self, volume):
        self.volume = volume


class FakeInstance:
    def __init__(self):
        self.player = FakeNativePlayer()

    def media_player_new(self):
        return self.player

    def media_new(self, source):
        return FakeMedia(source)

    def release(self):
        pass


class FakeVlc:
    def __init__(self):
        self.instance = FakeInstance()

    def Instance(self, *_args):
        return self.instance


def test_embedded_player_controls_stream(monkeypatch):
    fake = FakeVlc()
    monkeypatch.setattr(video_player, "_load_vlc", lambda: fake)
    player = video_player.EmbeddedVideoPlayer()

    player.play("https://cdn.example/video.mp4", 1234)

    assert player.is_playing()
    assert fake.instance.player.handle == 1234
    assert fake.instance.player.media.source.endswith("video.mp4")
    assert ":network-caching=1500" in fake.instance.player.media.options
    player.pause()
    assert player.is_paused()
    player.resume()
    player.set_position(0.5)
    player.set_volume(72)
    assert fake.instance.player.position == 0.5
    assert fake.instance.player.volume == 72
    player.stop()
    assert not player.is_active()


def test_embedded_player_accepts_existing_local_file(monkeypatch, tmp_path):
    fake = FakeVlc()
    monkeypatch.setattr(video_player, "_load_vlc", lambda: fake)
    media = tmp_path / "episode.mp4"
    media.write_bytes(b"video")

    player = video_player.EmbeddedVideoPlayer()
    player.play(media, 9)

    assert player.source == str(media)


def test_resolve_video_stream_uses_single_compatible_format(monkeypatch, tmp_path):
    engine = tmp_path / "yt-dlp.exe"
    engine.write_bytes(b"engine")
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return type("Result", (), {
            "returncode": 0,
            "stdout": "https://cdn.example/stream.mp4\n",
            "stderr": "",
        })()

    monkeypatch.setattr(app, "is_kanald_url", lambda _url: False)
    monkeypatch.setattr(app.subprocess, "run", fake_run)

    result = app.resolve_video_stream(
        "https://example.com/watch/1",
        engine,
        {"PATH": "runtime"},
        {"cookies_browser": "Nenhum", "cookies_file": ""},
    )

    assert result == "https://cdn.example/stream.mp4"
    assert "--no-playlist" in captured["command"]
    assert captured["command"][-2:] == ["--", "https://example.com/watch/1"]
    assert captured["kwargs"]["timeout"] == 60


def test_resolve_video_stream_rejects_non_web_source(tmp_path):
    with pytest.raises(video_player.VideoPlayerError):
        app.resolve_video_stream(str(Path("movie.mp4")), tmp_path / "yt-dlp.exe", {}, {})


@pytest.mark.parametrize(
    ("milliseconds", "expected"),
    [(0, "00:00"), (65_000, "01:05"), (3_665_000, "1:01:05")],
)
def test_format_player_time(milliseconds, expected):
    assert app.format_player_time(milliseconds) == expected
