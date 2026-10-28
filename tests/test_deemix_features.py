import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from audio_library import (
    DEEMIX_TEMPLATE_VARIABLES,
    OVERWRITE_CHOICES,
    music_output_template,
    resolve_unique_path,
    save_album_artwork,
    should_overwrite_file,
)
from deezer_catalog import DEEZER_COUNTRY_CHARTS, get_deezer_top_country


def test_deemix_template_variables_exist():
    expected = ["%artist%", "%title%", "%album%", "%tracknumber%", "%discnumber%", "%year%", "%genre%", "%isrc%", "%bpm%"]
    for var in expected:
        assert var in DEEMIX_TEMPLATE_VARIABLES


def test_music_output_template_deemix_tags():
    item = {
        "artist": "Daft Punk",
        "title": "Get Lucky",
        "album": "Random Access Memories",
        "track_number": 8,
        "disc_number": 1,
        "release_year": "2013",
        "genre": "Disco",
        "isrc": "USX1R1300057",
        "bpm": 116,
    }
    rendered = music_output_template(item, 8, "%artist% - %title%", pad_tracks=True)
    assert rendered == "Daft Punk - Get Lucky.%(ext)s"

    rendered_padded = music_output_template(item, 8, "%tracknumber% - %title%", pad_tracks=True)
    assert rendered_padded == "08 - Get Lucky.%(ext)s"

    rendered_unpadded = music_output_template(item, 8, "%tracknumber% - %title%", pad_tracks=False)
    assert rendered_unpadded == "8 - Get Lucky.%(ext)s"

    rendered_disc = music_output_template(item, 8, "%discnumber%-%tracknumber% %title%", pad_tracks=True)
    assert rendered_disc == "1-08 Get Lucky.%(ext)s"

    rendered_subfolder = music_output_template(item, 8, "%artist%/%album%/%tracknumber% - %title%", pad_tracks=True)
    assert rendered_subfolder == "Daft Punk/Random Access Memories/08 - Get Lucky.%(ext)s"


def test_music_output_template_legacy_format():
    item = {
        "artist": "Queen",
        "title": "Bohemian Rhapsody",
        "album": "A Night at the Opera",
        "track_number": 11,
    }
    rendered = music_output_template(item, 11, "{faixa:02} - {titulo}", pad_tracks=True)
    assert rendered == "11 - Bohemian Rhapsody.%(ext)s"


def test_should_overwrite_file_policy(tmp_path):
    existing_file = tmp_path / "song.mp3"
    existing_file.write_text("dummy")

    # Policy: Always overwrite
    assert should_overwrite_file(existing_file, "mp3", 320, "Sempre sobrescrever") is True

    # Policy: Skip if exists
    assert should_overwrite_file(existing_file, "mp3", 320, "Pular se já existir") is False

    # Policy: Keep both (triggers rename)
    assert should_overwrite_file(existing_file, "mp3", 320, "Manter ambos (renomear)") is False

    # Policy: Overwrite if higher quality
    # Existing MP3 default score 320 vs new FLAC 1411 -> True
    assert should_overwrite_file(existing_file, "flac", 1411, "Sobrescrever se qualidade maior") is True

    # Existing FLAC file
    flac_file = tmp_path / "song.flac"
    flac_file.write_text("dummy")
    # Existing FLAC 1411 vs new MP3 320 -> False
    assert should_overwrite_file(flac_file, "mp3", 320, "Sobrescrever se qualidade maior") is False


def test_resolve_unique_path(tmp_path):
    target = tmp_path / "track.mp3"
    assert resolve_unique_path(target) == target

    target.write_text("content")
    first_copy = resolve_unique_path(target)
    assert first_copy.name == "track (1).mp3"

    first_copy.write_text("content 1")
    second_copy = resolve_unique_path(target)
    assert second_copy.name == "track (2).mp3"


def test_save_album_artwork(tmp_path):
    folder = tmp_path / "AlbumFolder"
    folder.mkdir()
    item = {
        "cover_url": "https://e-cdns-images.dzcdn.net/images/cover/123/250x250-000000-80-0-0.jpg"
    }
    with patch("audio_library.urlopen") as mock_urlopen:
        mock_response = MagicMock()
        mock_response.read.return_value = b"FAKE_JPG_DATA"
        mock_urlopen.return_value.__enter__.return_value = mock_response

        art_path = save_album_artwork(folder, item, "cover.jpg")
        assert art_path is not None
        assert art_path.exists()
        assert art_path.read_bytes() == b"FAKE_JPG_DATA"
        # Ensure it requested 1200x1200 high-res image
        req = mock_urlopen.call_args[0][0]
        url = req.full_url if hasattr(req, "full_url") else str(req)
        assert "1200x1200" in url


def test_deezer_country_charts():
    assert "Brasil 🇧🇷" in DEEZER_COUNTRY_CHARTS
    assert "Global 🌍" in DEEZER_COUNTRY_CHARTS
    assert "Estados Unidos 🇺🇸" in DEEZER_COUNTRY_CHARTS
    assert "Reino Unido 🇬🇧" in DEEZER_COUNTRY_CHARTS

    with patch("deezer_catalog._api_json") as mock_api:
        mock_api.return_value = {
            "data": [
                {
                    "id": 12345,
                    "title": "Starboy",
                    "artist": {"name": "The Weeknd"},
                    "album": {"title": "Starboy", "cover_medium": "https://e-cdns-images.dzcdn.net/cover.jpg"},
                    "link": "https://www.deezer.com/track/12345",
                }
            ]
        }
        results = get_deezer_top_country("Estados Unidos 🇺🇸", limit=25)
        assert len(results) == 1
        assert results[0].title == "Starboy"
        mock_api.assert_called_once()
        assert "1313621735" in mock_api.call_args[0][0]
