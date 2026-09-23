from pathlib import Path

import audio_library
from music_player import is_public_deezer_preview


def test_music_target_folder_uses_artist_and_album(tmp_path: Path):
    item = {"artist": "Artist", "album": "Album"}
    assert audio_library.music_target_folder(tmp_path, item, "Pasta raiz") == tmp_path
    assert audio_library.music_target_folder(tmp_path, item, "Artista") == tmp_path / "Artist"
    assert audio_library.music_target_folder(tmp_path, item, "Artista\\Álbum") == tmp_path / "Artist" / "Album"


def test_music_target_folder_sanitizes_windows_names(tmp_path: Path):
    item = {"artist": 'A/B:C*D', "album": 'Album? <2026>'}
    result = audio_library.music_target_folder(tmp_path, item, "Artista\\Álbum")
    assert result == tmp_path / "A_B_C_D" / "Album_ _2026_"


def test_music_output_template_supports_library_fields():
    item = {
        "media_id": "123",
        "track_title": "My Song",
        "artist": "My Artist",
        "album": "My Album",
        "track_number": 3,
        "release_year": "2026",
    }
    result = audio_library.music_output_template(
        item,
        3,
        "{artista} - {faixa:02} - {titulo} ({ano})",
    )
    assert result == "My Artist - 03 - My Song (2026).%(ext)s"


def test_music_output_template_falls_back_when_invalid():
    item = {"track_title": "Song", "track_number": 2}
    result = audio_library.music_output_template(item, 2, "{unknown}")
    assert result == "02 - Song.%(ext)s"


def test_public_deezer_preview_validation():
    assert is_public_deezer_preview("https://cdnt-preview.dzcdn.net/api/1/1.mp3")
    assert is_public_deezer_preview("https://e-cdns-preview-a.dzcdn.net/stream.mp3")
    assert not is_public_deezer_preview("http://cdnt-preview.dzcdn.net/a.mp3")
    assert not is_public_deezer_preview("https://evil.example/a.mp3")
