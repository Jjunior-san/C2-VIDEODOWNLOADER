from pathlib import Path
import audio_library
import youtube_downloader_app as app


def test_ultra_hd_and_audio_formats_available():
    assert "2160p (4K)" in app.VIDEO_DOWNLOAD_FORMATS
    assert "1440p (2K)" in app.VIDEO_DOWNLOAD_FORMATS
    assert "Apenas áudio (FLAC)" in app.VIDEO_DOWNLOAD_FORMATS
    assert "Apenas áudio (WAV)" in app.VIDEO_DOWNLOAD_FORMATS

    assert "Apenas áudio (FLAC)" in app.MUSIC_DOWNLOAD_FORMATS
    assert "Apenas áudio (WAV)" in app.MUSIC_DOWNLOAD_FORMATS
    assert set(app.MUSIC_DOWNLOAD_FORMATS).issubset(set(app.VIDEO_DOWNLOAD_FORMATS))

    assert audio_library.is_audio_format("Apenas áudio (FLAC)")
    assert audio_library.audio_codec("Apenas áudio (FLAC)") == "flac"
    assert audio_library.is_audio_format("Apenas áudio (WAV)")
    assert audio_library.audio_codec("Apenas áudio (WAV)") == "wav"


def test_build_command_subtitles_options(tmp_path: Path):
    class DummyApp:
        _build_command = app.DownloadApp._build_command
        download_fragments = 4
        playlist_var = type("Var", (), {"get": lambda self: False})()

    dummy = DummyApp()
    engine = tmp_path / "yt-dlp.exe"

    # Default without subtitles
    cmd_default = dummy._build_command(engine, tmp_path, "1080p", "https://youtube.com/watch?v=123")
    assert "--write-subs" not in cmd_default

    # With subtitles embedded
    dummy.download_options = {
        "subtitles_enabled": True,
        "subtitles_embed": True,
        "subtitles_auto": False,
        "subtitles_langs": "pt,en",
    }
    cmd_subs = dummy._build_command(engine, tmp_path, "1080p", "https://youtube.com/watch?v=123")
    assert "--write-subs" in cmd_subs
    assert "--embed-subs" in cmd_subs
    assert "--sub-langs" in cmd_subs
    assert "pt,en" in cmd_subs
    assert "--write-auto-subs" not in cmd_subs

    # With auto subtitles and external .srt
    dummy.download_options = {
        "subtitles_enabled": True,
        "subtitles_embed": False,
        "subtitles_auto": True,
        "subtitles_langs": "es,fr",
    }
    cmd_auto = dummy._build_command(engine, tmp_path, "1080p", "https://youtube.com/watch?v=123")
    assert "--write-subs" in cmd_auto
    assert "--write-auto-subs" in cmd_auto
    assert "--embed-subs" not in cmd_auto
    assert "--convert-subs" in cmd_auto
    assert "srt" in cmd_auto
    assert "es,fr" in cmd_auto


def test_build_command_sponsorblock_and_chapters(tmp_path: Path):
    class DummyApp:
        _build_command = app.DownloadApp._build_command
        download_fragments = 4
        playlist_var = type("Var", (), {"get": lambda self: False})()

    dummy = DummyApp()
    engine = tmp_path / "yt-dlp.exe"

    # SponsorBlock and Split Chapters
    dummy.download_options = {
        "sponsorblock": True,
        "split_chapters": True,
        "embed_chapters": False,
    }
    cmd = dummy._build_command(engine, tmp_path, "1080p", "https://youtube.com/watch?v=123")
    assert "--sponsorblock-remove" in cmd
    assert "all" in cmd
    assert "--split-chapters" in cmd
    assert "--no-embed-chapters" not in cmd

    # Embed Chapters enabled
    dummy.download_options = {
        "sponsorblock": False,
        "split_chapters": False,
        "embed_chapters": True,
    }
    cmd_chapters = dummy._build_command(engine, tmp_path, "1080p", "https://youtube.com/watch?v=123")
    assert "--sponsorblock-remove" not in cmd_chapters
    assert "--embed-chapters" in cmd_chapters
    assert "--split-chapters" not in cmd_chapters


def test_build_command_rate_limit_and_proxy(tmp_path: Path):
    class DummyApp:
        _build_command = app.DownloadApp._build_command
        download_fragments = 4
        playlist_var = type("Var", (), {"get": lambda self: False})()

    dummy = DummyApp()
    engine = tmp_path / "yt-dlp.exe"

    dummy.download_options = {
        "rate_limit": "2 MB/s",
        "proxy_url": "http://127.0.0.1:8080",
    }
    cmd = dummy._build_command(engine, tmp_path, "1080p", "https://youtube.com/watch?v=123")
    assert "--limit-rate" in cmd
    assert "2M" in cmd
    assert "--proxy" in cmd
    assert "http://127.0.0.1:8080" in cmd


def test_new_settings_persistence(tmp_path: Path, monkeypatch):
    settings_file = tmp_path / "settings.json"
    monkeypatch.setattr(app, "SETTINGS_FILE", settings_file)

    settings = {
        "download_folder": str(tmp_path),
        "video_format": "2160p (4K)",
        "subtitles_enabled": True,
        "subtitles_embed": True,
        "subtitles_auto": True,
        "subtitles_langs": "pt,pt-BR,en",
        "sponsorblock": True,
        "embed_chapters": True,
        "split_chapters": False,
        "rate_limit": "5 MB/s",
        "proxy_url": "socks5://127.0.0.1:1080",
        "clipboard_monitor": True,
    }
    app.save_user_settings(settings)
    loaded = app.load_user_settings()

    assert loaded["video_format"] == "2160p (4K)"
    assert loaded["subtitles_enabled"] is True
    assert loaded["subtitles_embed"] is True
    assert loaded["subtitles_auto"] is True
    assert loaded["subtitles_langs"] == "pt,pt-BR,en"
    assert loaded["sponsorblock"] is True
    assert loaded["rate_limit"] == "5 MB/s"
    assert loaded["proxy_url"] == "socks5://127.0.0.1:1080"
    assert loaded["clipboard_monitor"] is True


def test_clipboard_detection():
    class DummyText:
        def __init__(self):
            self.content = ""
        def get(self, _start, _end):
            return self.content
        def insert(self, pos, text):
            if pos == "1.0":
                self.content = text + self.content
            else:
                self.content += text

    class DummyApp:
        _check_clipboard_for_media = app.DownloadApp._check_clipboard_for_media
        def __init__(self):
            self.clipboard_monitor_var = type("Var", (), {"get": lambda self: True})()
            self._last_clipboard_url = None
            self.video_url_text = DummyText()
            self.logs = []
            self.preview_scheduled = False
        def queue_log(self, msg):
            self.logs.append(msg)
        def _schedule_video_preview_check(self):
            self.preview_scheduled = True

    dummy = DummyApp()
    dummy.root = type("Root", (), {"clipboard_get": lambda self: "https://www.youtube.com/watch?v=dQw4w9WgXcQ"})()

    dummy._check_clipboard_for_media()
    assert dummy.video_url_text.content == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert dummy._last_clipboard_url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert dummy.preview_scheduled is True
    assert len(dummy.logs) == 1

    # Second check with same URL should not duplicate
    dummy.preview_scheduled = False
    dummy._check_clipboard_for_media()
    assert dummy.preview_scheduled is False
