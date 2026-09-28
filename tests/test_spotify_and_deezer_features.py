from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import deezer_catalog
import queue_service
import spotify_catalog
from deezer_catalog import DeezerSearchResult, DeezerTrack
from download_control import DownloadControl
from spotify_catalog import SpotifyCollection, SpotifyTrack


def test_parse_spotify_url_various_formats():
    assert spotify_catalog.parse_spotify_url("https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT") == ("track", "4cOdK2wGLETKBW3PvgPWqT")
    assert spotify_catalog.parse_spotify_url("https://open.spotify.com/album/1DFixLWuPkv3KT3TnV35m3?si=123") == ("album", "1DFixLWuPkv3KT3TnV35m3")
    assert spotify_catalog.parse_spotify_url("https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M") == ("playlist", "37i9dQZF1DXcBWIGoYBM5M")
    assert spotify_catalog.parse_spotify_url("https://open.spotify.com/embed/playlist/37i9dQZF1DXcBWIGoYBM5M") == ("playlist", "37i9dQZF1DXcBWIGoYBM5M")
    assert spotify_catalog.parse_spotify_url("spotify:track:4cOdK2wGLETKBW3PvgPWqT") == ("track", "4cOdK2wGLETKBW3PvgPWqT")
    assert spotify_catalog.parse_spotify_url("spotify:album:1DFixLWuPkv3KT3TnV35m3") == ("album", "1DFixLWuPkv3KT3TnV35m3")
    assert spotify_catalog.parse_spotify_url("spotify:playlist:37i9dQZF1DXcBWIGoYBM5M") == ("playlist", "37i9dQZF1DXcBWIGoYBM5M")
    assert spotify_catalog.parse_spotify_url("https://example.com/playlist/123") is None
    assert spotify_catalog.is_spotify_url("https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT") is True
    assert spotify_catalog.is_spotify_url("https://example.com") is False


def test_deezer_shortlink_parsing(monkeypatch):
    monkeypatch.setattr(
        deezer_catalog,
        "resolve_deezer_shortlink",
        lambda url, timeout=6.0: "https://www.deezer.com/track/3135556" if "link.deezer.com" in url else url,
    )
    assert deezer_catalog.parse_deezer_url("https://link.deezer.com/s/AbCdEf") == ("track", "3135556")
    assert deezer_catalog.is_deezer_url("https://link.deezer.com/s/AbCdEf") is True


def test_deezer_loved_tracks_parsing_and_resolution(monkeypatch):
    assert deezer_catalog.parse_deezer_url("https://www.deezer.com/profile/998877/loved") == ("loved", "998877")
    assert deezer_catalog.parse_deezer_url("https://www.deezer.com/br/user/998877/tracks") == ("loved", "998877")
    assert deezer_catalog.parse_deezer_url("https://www.deezer.com/user/998877") == ("loved", "998877")

    def mock_api(url):
        if "/user/998877/tracks" in url:
            return {
                "data": [
                    {
                        "id": 101,
                        "title": "Loved Song",
                        "duration": 200,
                        "preview": "https://cdnt-preview.dzcdn.net/loved.mp3",
                        "artist": {"name": "Favorite Artist"},
                        "album": {"title": "Best Hits", "cover_big": "https://cdn-images.dzcdn.net/cover.jpg"},
                    }
                ]
            }
        if "/user/998877" in url:
            return {"name": "Usuario Teste"}
        return {}

    monkeypatch.setattr(deezer_catalog, "_api_json", mock_api)
    collection = deezer_catalog.resolve_deezer_url("https://www.deezer.com/profile/998877/loved")
    assert collection.kind == "loved"
    assert "Usuario Teste" in collection.title
    assert len(collection.tracks) == 1
    assert collection.tracks[0].title == "Loved Song"


def test_deezer_drill_down_functions(monkeypatch):
    def mock_api(url):
        if "/artist/42/top" in url:
            return {
                "data": [
                    {
                        "id": 1,
                        "title": "Hit 1",
                        "duration": 180,
                        "preview": "https://cdnt-preview.dzcdn.net/1.mp3",
                        "artist": {"name": "Artist 42"},
                        "album": {"title": "Album 1", "cover_big": "https://cdn-images.dzcdn.net/1.jpg"},
                    }
                ]
            }
        if "/artist/42/albums" in url:
            return {
                "data": [
                    {
                        "id": 501,
                        "title": "Greatest Album",
                        "release_date": "2024-01-01",
                        "cover_medium": "https://cdn-images.dzcdn.net/alb.jpg",
                    }
                ]
            }
        if "/album/501" in url:
            return {
                "title": "Greatest Album",
                "tracks": {
                    "data": [
                        {
                            "id": 10,
                            "title": "Track 10",
                            "duration": 210,
                            "preview": "https://cdnt-preview.dzcdn.net/10.mp3",
                            "artist": {"name": "Artist 42"},
                        }
                    ]
                }
            }
        return {}

    monkeypatch.setattr(deezer_catalog, "_api_json", mock_api)

    top_tracks = deezer_catalog.get_artist_top_tracks("42")
    assert len(top_tracks) == 1
    assert top_tracks[0].title == "Hit 1"

    albums = deezer_catalog.get_artist_albums("42")
    assert len(albums) == 1
    assert albums[0].title == "Greatest Album"
    assert albums[0].kind == "album"

    album_tracks = deezer_catalog.get_album_tracks("501")
    assert len(album_tracks) == 1
    assert album_tracks[0].title == "Track 10"


def test_spotify_matching_and_queue_discovery(tmp_path, monkeypatch):
    fake_sp_collection = SpotifyCollection(
        source_url="https://open.spotify.com/playlist/test123",
        title="Minha Playlist Spotify",
        kind="playlist",
        tracks=(
            SpotifyTrack("sp1", "One More Time", "Daft Punk", "Discovery", 320),
            SpotifyTrack("sp2", "Rare Track", "Unknown Artist", "Unknown Album", 180),
        ),
    )
    monkeypatch.setattr(queue_service, "resolve_spotify_url", lambda url, engine_path=None: fake_sp_collection)

    # Mock matching first track to Deezer and second track to None (falls back to ytsearch)
    def mock_match(sp_track):
        if sp_track.title == "One More Time":
            return DeezerTrack("3135556", "One More Time", "Daft Punk", "Discovery", "https://cdnt-preview.dzcdn.net/daft.mp3")
        return None

    monkeypatch.setattr(queue_service, "match_spotify_track_to_deezer", mock_match)

    options = {
        "folder": str(tmp_path),
        "format": "Apenas áudio (MP3)",
        "playlist": True,
        "fragments": 4,
        "cookies_browser": "Nenhum",
        "cookies_file": "",
        "deezer_arl": "",
        "work_mode": "music",
    }
    items = queue_service.discover(
        ["https://open.spotify.com/playlist/test123"],
        options,
        Path("engine"),
        DownloadControl(),
        {},
        lambda line: None,
    )
    assert len(items) == 2
    # Item 1 matched Deezer preview
    assert items[0]["kind"] == "deezer_preview"
    assert "Daft Punk" in items[0]["title"]
    assert items[0]["collection_title"] == "Minha Playlist Spotify"
    # Item 2 fallback to YouTube search
    assert items[1]["kind"] == "default"
    assert items[1]["source"].startswith("ytsearch1:")
    assert "Rare Track" in items[1]["title"]
    assert items[1]["collection_title"] == "Minha Playlist Spotify"


def test_spotify_embed_resolver_fallback(monkeypatch):
    html_without_next_data = "<html><head><title>Spotify</title></head><body>No data</body></html>"
    mock_resp = SimpleNamespace(text=html_without_next_data, raise_for_status=lambda: None)
    monkeypatch.setattr(spotify_catalog, "_http_session", lambda: SimpleNamespace(get=lambda url, timeout=8: mock_resp))
    monkeypatch.setattr(
        spotify_catalog,
        "fetch_spotify_oembed",
        lambda url: {"title": "Faixa Exemplo", "thumbnail_url": "https://img.spotify.com/thumb.jpg"},
    )
    col = spotify_catalog.resolve_spotify_url("https://open.spotify.com/track/123456")
    assert col.kind == "track"
    assert col.tracks[0].title == "Faixa Exemplo"
