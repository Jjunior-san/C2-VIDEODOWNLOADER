from unittest.mock import MagicMock, patch
from tkinter import Tk, Toplevel
from pathlib import Path
import pytest

import youtube_downloader_app as app
import deezer_catalog as catalog
from music_player import MusicPlayer
from ui_layout import PROGRAM_BLUE


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


def test_deezer_track_and_search_result_have_album_fields():
    t = catalog.DeezerTrack(
        track_id="123",
        title="Song A",
        artist="Artist B",
        album="Album C",
        preview_url=None,
        album_id="999",
    )
    assert t.album_id == "999"

    res = catalog.DeezerSearchResult(
        kind="track",
        item_id="123",
        title="Song A",
        subtitle="Artist B • Album C",
        cover_url=None,
        page_url="https://www.deezer.com/track/123",
        album_id="999",
        album_title="Album C",
    )
    assert res.album_id == "999"
    assert res.album_title == "Album C"


def test_music_player_play_deezer_full(tmp_path, monkeypatch):
    player = MusicPlayer(tmp_path)

    # Mock download_and_decrypt_track
    fake_file = tmp_path / "full_12345.mp3"
    fake_file.write_bytes(b"dummy mp3 audio content " * 1000)

    monkeypatch.setattr(
        "deezer_auth.download_and_decrypt_track",
        lambda track_id, output_path, arl, **kwargs: fake_file,
    )
    monkeypatch.setattr(player, "play_file", MagicMock(return_value="internal"))

    result_path = player.play_deezer_full(
        "12345",
        "valid_arl_cookie",
        title="Song Test",
        artist="Artist Test",
        album="Album Test",
        duration=180.0,
    )
    assert result_path == fake_file
    assert player.current_source == "deezer_full_12345"
    player.play_file.assert_called_once()


def test_catalog_album_buttons_and_navigation(tk_root, tmp_path, monkeypatch):
    monkeypatch.setattr(app, "load_user_settings", lambda: {"deezer_arl": "dummy_arl_123"})
    monkeypatch.setattr(app.DownloadApp, "start_maintenance", lambda *args: None)
    monkeypatch.setattr(app, "QUEUE_FILE", tmp_path / "downloads.sqlite3")
    monkeypatch.setattr(app, "ACTIVITY_LOG_FILE", tmp_path / "activity.log")

    top = Toplevel(tk_root)
    top.withdraw()
    instance = app.DownloadApp(top)

    # Check button existence
    assert hasattr(instance, "catalog_album_download_button")
    assert hasattr(instance, "catalog_drill_button")
    assert hasattr(instance, "catalog_tracklist_button")
    assert hasattr(instance, "catalog_play_button")
    assert hasattr(instance, "catalog_download_button")
    assert hasattr(instance, "catalog_load_button")

    # Verify no "C² Music" text is present in branding
    assert "C² Music" not in instance.bottom_artist_var.get()
    assert instance.bottom_artist_var.get() == "C² Downloader"

    # Mock search results with track containing album_id
    track_item = catalog.DeezerSearchResult(
        kind="track",
        item_id="101",
        title="Awesome Song",
        subtitle="Artist X • Great Album",
        cover_url=None,
        page_url="https://www.deezer.com/track/101",
        album_id="505",
        album_title="Great Album",
    )
    album_item = catalog.DeezerSearchResult(
        kind="album",
        item_id="505",
        title="Great Album",
        subtitle="Artist X",
        cover_url=None,
        page_url="https://www.deezer.com/album/505",
        album_id="505",
        album_title="Great Album",
    )

    instance._music_search_results = [track_item, album_item]
    instance.music_results_tree.insert("", "end", iid="0", values=("Música", track_item.title, track_item.subtitle))
    instance.music_results_tree.insert("", "end", iid="1", values=("Álbum", album_item.title, album_item.subtitle))

    # Select track item
    instance.music_results_tree.selection_set("0")
    instance._show_selected_catalog_result()

    # Buttons should be active for track's album
    assert str(instance.catalog_drill_button.cget("state")) == "normal"
    assert "Álbum" in instance.catalog_drill_button.cget("text")
    assert str(instance.catalog_album_download_button.cget("state")) == "normal"

    # Select album item
    instance.music_results_tree.selection_set("1")
    instance._show_selected_catalog_result()
    assert str(instance.catalog_drill_button.cget("state")) == "normal"
    assert str(instance.catalog_album_download_button.cget("state")) == "normal"

    # Test invoking _view_album_for_selected on track
    drilled_albums = []
    instance._drill_down_album = lambda alb_id, alb_title: drilled_albums.append((alb_id, alb_title))
    instance.music_results_tree.selection_set("0")
    instance._view_album_for_selected()
    assert drilled_albums == [("505", "Great Album")]

    # Test invoking _download_album_for_selected on track
    downloaded_sources = []
    instance._prepare_sources = lambda urls, autostart: downloaded_sources.append((urls, autostart))
    instance._download_album_for_selected()
    assert downloaded_sources == [(["https://www.deezer.com/album/505"], True)]

    top.destroy()
