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
    }

    app.save_user_settings(expected)

    assert settings_file.exists()
    assert app.load_user_settings() == expected


def test_invalid_user_settings_returns_empty(tmp_path: Path, monkeypatch):
    settings_file = tmp_path / "settings.json"
    settings_file.write_text("{invalid", encoding="utf-8")
    monkeypatch.setattr(app, "SETTINGS_FILE", settings_file)

    assert app.load_user_settings() == {}
