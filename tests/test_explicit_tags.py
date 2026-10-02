"""Tests for Explicit (🅴) tag detection, UI display, audio metadata, and player tracking."""
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import deezer_catalog
import spotify_catalog
import queue_service
import audio_library
from music_player import MusicPlayer
from ui_layout import format_explicit_badge, format_title_with_explicit, format_quality_badge


def test_deezer_track_explicit_from_payload():
    # Test with explicit_lyrics = 1
    payload_explicit = {
        "id": "12345",
        "title": "Explicit Track",
        "artist": {"name": "Artist Name"},
        "album": {"title": "Album Name"},
        "explicit_lyrics": 1,
        "preview": "https://cdns-preview-d.dzcdn.net/stream/sample.mp3",
    }
    track = deezer_catalog._track_from_payload(payload_explicit)
    assert track.explicit is True
    assert track.title == "Explicit Track"

    # Test with explicit_content_lyrics = 1
    payload_content_lyrics = {
        "id": "12346",
        "title": "Clean Title but explicit content",
        "artist": {"name": "Artist Name"},
        "album": {"title": "Album Name"},
        "explicit_content_lyrics": 1,
        "preview": "https://cdns-preview-d.dzcdn.net/stream/sample.mp3",
    }
    track2 = deezer_catalog._track_from_payload(payload_content_lyrics)
    assert track2.explicit is True

    # Test with clean track
    payload_clean = {
        "id": "12347",
        "title": "Clean Track",
        "artist": {"name": "Artist Name"},
        "album": {"title": "Album Name"},
        "explicit_lyrics": 0,
        "explicit_content_lyrics": 0,
        "preview": "https://cdns-preview-d.dzcdn.net/stream/sample.mp3",
    }
    track_clean = deezer_catalog._track_from_payload(payload_clean)
    assert track_clean.explicit is False


def test_deezer_search_result_explicit(monkeypatch):
    sample_response = {
        "data": [
            {
                "id": "1001",
                "title": "Rap God",
                "artist": {"name": "Eminem"},
                "album": {"title": "The Marshall Mathers LP2", "id": "2001"},
                "explicit_lyrics": 1,
            },
            {
                "id": "1002",
                "title": "Happy",
                "artist": {"name": "Pharrell Williams"},
                "album": {"title": "G I R L", "id": "2002"},
                "explicit_lyrics": 0,
            },
        ]
    }
    monkeypatch.setattr(deezer_catalog, "_api_json", lambda url: sample_response)

    results = deezer_catalog.search_deezer_catalog("test", kind="track", limit=10)
    assert len(results) == 2
    assert results[0].explicit is True
    assert results[1].explicit is False


def test_spotify_track_explicit():
    track_explicit = spotify_catalog.SpotifyTrack(
        track_id="sp123",
        title="Bad Guy",
        artist="Billie Eilish",
        explicit=True,
    )
    assert track_explicit.explicit is True

    track_clean = spotify_catalog.SpotifyTrack(
        track_id="sp124",
        title="Ocean Eyes",
        artist="Billie Eilish",
        explicit=False,
    )
    assert track_clean.explicit is False


def test_queue_service_preserves_explicit():
    track = deezer_catalog.DeezerTrack(
        track_id="999",
        title="Explicit Song",
        artist="Artist",
        album="Album",
        preview_url="https://cdns-preview-d.dzcdn.net/stream/sample.mp3",
        explicit=True,
    )
    options = {"format": "Apenas áudio (MP3)", "audio_bitrate_mode": "Original / automática"}
    items = queue_service._deezer_queue_items([track], "Collection", options)
    assert len(items) == 1
    assert items[0].get("explicit") is True


def test_audio_metadata_read_write_explicit_mp3(tmp_path: Path):
    from mutagen.mp3 import MP3
    from mutagen.id3 import ID3

    mp3_file = tmp_path / "test_track.mp3"
    # Create empty valid MP3 file
    mp3_file.write_bytes(b"\xff\xfb\x90\x44" + b"\x00" * 2000)

    # Write explicit metadata
    item = {
        "track_title": "Parental Advisory",
        "artist": "Explicit Artist",
        "album": "Explicit Album",
        "explicit": True,
    }
    audio_library.write_audio_metadata(mp3_file, item)

    # Read back metadata
    read_meta = audio_library.read_audio_metadata(mp3_file)
    assert read_meta.get("track_title") == "Parental Advisory"
    assert read_meta.get("explicit") is True

    # Test toggling explicit to False
    item["explicit"] = False
    audio_library.write_audio_metadata(mp3_file, item)
    read_meta_clean = audio_library.read_audio_metadata(mp3_file)
    assert read_meta_clean.get("explicit") is not True


def test_music_player_explicit_tracking(tmp_path: Path, monkeypatch):
    player = MusicPlayer(tmp_path)
    mock_mixer = MagicMock()
    monkeypatch.setattr(player, "_ensure_mixer", lambda: mock_mixer)

    audio_file = tmp_path / "song.mp3"
    audio_file.write_bytes(b"\xff\xfb\x90\x44" + b"\x00" * 1000)

    # Play with explicit=True
    player.play_file(
        audio_file,
        allow_external=False,
        title="Explicit Tune",
        artist="Test Artist",
        explicit=True,
    )
    assert player.track_explicit is True
    assert player.track_title == "Explicit Tune"

    # Stop resets explicit
    player.stop()
    assert player.track_explicit is False
