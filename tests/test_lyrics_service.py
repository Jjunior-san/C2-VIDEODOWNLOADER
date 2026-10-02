import pytest
from lyrics_service import parse_lrc, get_active_lyric_index, _clean_string


def test_parse_lrc_valid():
    sample = """
    [00:12.50]Primeira linha da música
    [00:15.80]Segunda linha
    [01:02.100]Refrão vibrante
    """
    parsed = parse_lrc(sample)
    assert len(parsed) == 3
    assert parsed[0] == (12500, "Primeira linha da música")
    assert parsed[1] == (15800, "Segunda linha")
    assert parsed[2] == (62100, "Refrão vibrante")


def test_get_active_lyric_index():
    lyrics = [
        (10000, "Linha 1"),
        (20000, "Linha 2"),
        (30000, "Linha 3"),
    ]
    assert get_active_lyric_index(lyrics, 5000) == -1
    assert get_active_lyric_index(lyrics, 10000) == 0
    assert get_active_lyric_index(lyrics, 15000) == 0
    assert get_active_lyric_index(lyrics, 20000) == 1
    assert get_active_lyric_index(lyrics, 29999) == 1
    assert get_active_lyric_index(lyrics, 30000) == 2
    assert get_active_lyric_index(lyrics, 50000) == 2


def test_clean_string():
    assert _clean_string("Song Title (Official Music Video)") == "Song Title"
    assert _clean_string("Artist feat. Someone") == "Artist"
    assert _clean_string("Title [Lyrics]") == "Title"
