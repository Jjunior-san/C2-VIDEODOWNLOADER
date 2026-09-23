from pathlib import Path

import youtube_downloader_app as app


def test_user_settings_roundtrip(tmp_path: Path, monkeypatch):
    settings_file = tmp_path / "settings.json"
    monkeypatch.setattr(app, "SETTINGS_FILE", settings_file)

    expected = {
        "download_folder": r"C:\Downloads\Videos",
        "format": "Melhor MP4 compatível",
        "playlist": True,
        "cookies_browser": "Firefox",
        "concurrent_fragments": 8,
    }

    app.save_user_settings(expected)

    assert settings_file.exists()
    assert app.load_user_settings() == expected


def test_invalid_user_settings_returns_empty(tmp_path: Path, monkeypatch):
    settings_file = tmp_path / "settings.json"
    settings_file.write_text("{invalid", encoding="utf-8")
    monkeypatch.setattr(app, "SETTINGS_FILE", settings_file)

    assert app.load_user_settings() == {}

def test_music_workspace_only_offers_audio_formats():
    assert app.MUSIC_DOWNLOAD_FORMATS
    assert "Apenas áudio (MP3)" in app.MUSIC_DOWNLOAD_FORMATS
    assert "Melhor MP4 compatível" not in app.MUSIC_DOWNLOAD_FORMATS
    assert set(app.MUSIC_DOWNLOAD_FORMATS).issubset(set(app.VIDEO_DOWNLOAD_FORMATS))


def test_separate_music_and_video_settings(tmp_path: Path, monkeypatch):
    settings_file = tmp_path / "settings.json"
    monkeypatch.setattr(app, "SETTINGS_FILE", settings_file)

    settings = {
        "download_folder": r"C:\Downloads\Videos",
        "video_download_folder": r"C:\Downloads\Videos",
        "music_download_folder": r"C:\Downloads\Musicas",
        "video_format": "1080p MP4",
        "music_format": "Apenas áudio (FLAC)",
    }
    app.save_user_settings(settings)
    loaded = app.load_user_settings()
    assert loaded["video_download_folder"] == r"C:\Downloads\Videos"
    assert loaded["music_download_folder"] == r"C:\Downloads\Musicas"
    assert loaded["video_format"] == "1080p MP4"
    assert loaded["music_format"] == "Apenas áudio (FLAC)"


def test_fetch_video_preview_info_returns_structure():
    info = app.fetch_video_preview_info("https://example.com/video")
    assert isinstance(info, dict)
    assert "title" in info
    assert "author" in info
    assert "cover_data" in info
