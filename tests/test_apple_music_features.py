from unittest.mock import MagicMock
from tkinter import Tk, Toplevel
import pytest

import youtube_downloader_app as app
from lyrics_ui import LyricsDialog
from mini_player_ui import MiniPlayer
from music_player import MusicPlayer


@pytest.fixture(scope="module")
def tk_root():
    try:
        root = Tk()
        root.withdraw()
    except Exception:
        pytest.skip("Tk desktop unavailable")
    yield root
    try:
        root.destroy()
    except Exception:
        pass


def test_sidebar_buttons_navigate_tabs(tk_root, tmp_path, monkeypatch):
    monkeypatch.setattr(app, "load_user_settings", lambda: {})
    monkeypatch.setattr(app.DownloadApp, "start_maintenance", lambda *args: None)
    monkeypatch.setattr(app, "QUEUE_FILE", tmp_path / "downloads.sqlite3")
    monkeypatch.setattr(app, "ACTIVITY_LOG_FILE", tmp_path / "activity.log")

    top = Toplevel(tk_root)
    top.withdraw()
    instance = app.DownloadApp(top)

    assert hasattr(instance, "sidebar_frame")
    assert hasattr(instance, "_sidebar_buttons")
    assert "music" in instance._sidebar_buttons
    assert "video" in instance._sidebar_buttons
    assert "queue" in instance._sidebar_buttons
    assert "completed" in instance._sidebar_buttons
    assert "activity" in instance._sidebar_buttons
    assert "about" in instance._sidebar_buttons

    # Test clicking navigation buttons
    btn_video, page_video = instance._sidebar_buttons["video"]
    btn_video.invoke()
    top.update()
    assert instance.tabs.select() == str(page_video)

    btn_music, page_music = instance._sidebar_buttons["music"]
    btn_music.invoke()
    top.update()
    assert instance.tabs.select() == str(page_music)

    top.destroy()


def test_bottom_now_playing_bar_widgets_exist(tk_root, tmp_path, monkeypatch):
    monkeypatch.setattr(app, "load_user_settings", lambda: {})
    monkeypatch.setattr(app.DownloadApp, "start_maintenance", lambda *args: None)
    monkeypatch.setattr(app, "QUEUE_FILE", tmp_path / "downloads.sqlite3")
    monkeypatch.setattr(app, "ACTIVITY_LOG_FILE", tmp_path / "activity.log")

    top = Toplevel(tk_root)
    top.withdraw()
    instance = app.DownloadApp(top)

    assert hasattr(instance, "bottom_player_bar")
    assert hasattr(instance, "bottom_play_button")
    assert hasattr(instance, "bottom_stop_button")
    assert hasattr(instance, "bottom_seek_scale")
    assert hasattr(instance, "bottom_track_var")
    assert hasattr(instance, "bottom_artist_var")
    assert hasattr(instance, "bottom_badge_var")

    top.destroy()


def test_mini_player_creation(tk_root, tmp_path):
    player = MusicPlayer(tmp_path)
    mini = MiniPlayer(tk_root, player)
    assert mini.winfo_exists()
    assert mini.track_var.get() == "Nenhuma faixa ativa"
    mini.destroy()


def test_lyrics_dialog_creation(tk_root, tmp_path):
    player = MusicPlayer(tmp_path)
    dialog = LyricsDialog(tk_root, player)
    assert dialog.winfo_exists()
    dialog.destroy()
