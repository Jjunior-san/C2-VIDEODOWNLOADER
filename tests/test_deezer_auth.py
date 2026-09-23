from pathlib import Path
from unittest.mock import MagicMock, patch

import deezer_auth
from audio_library import create_collection_zip


def test_calc_blowfish_key():
    # Test known MD5 XOR output for a specific track ID
    key = deezer_auth.calc_blowfish_key("3135556")
    assert isinstance(key, str)
    assert len(key) == 16
    assert key == "llfk9f,7e%u`<d49"


def test_blowfish_decrypt_block():
    from binascii import a2b_hex
    from Crypto.Cipher import Blowfish

    key = deezer_auth.calc_blowfish_key("3135556")
    iv = a2b_hex("0001020304050607")
    plain = b"A" * 2048

    cipher = Blowfish.new(key.encode("ascii"), Blowfish.MODE_CBC, iv)
    encrypted = cipher.encrypt(plain)
    assert encrypted != plain

    decrypted = deezer_auth.blowfish_decrypt_block(encrypted, key)
    assert decrypted == plain


def test_validate_deezer_arl_empty():
    res = deezer_auth.validate_deezer_arl("")
    assert not res["valid"]
    assert res["user_id"] == 0
    assert "Nenhum cookie" in res["error"]


def test_validate_deezer_arl_success():
    mock_payload = {
        "results": {
            "USER": {
                "USER_ID": 987654,
                "BLOG_NAME": "TestUser",
                "OPTIONS": {
                    "license_token": "token123",
                    "web_sound_quality": {"lossless": True, "high": True},
                },
            }
        }
    }
    mock_session = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = mock_payload
    mock_session.get.return_value = mock_resp

    res = deezer_auth.validate_deezer_arl("valid_cookie_123", session=mock_session)
    assert res["valid"] is True
    assert res["user_id"] == 987654
    assert res["user_name"] == "TestUser"
    assert "HiFi" in res["plan"]
    assert res["lossless"] is True


def test_validate_deezer_arl_invalid():
    mock_payload = {
        "results": {
            "USER": {
                "USER_ID": 0,
                "OPTIONS": {},
            }
        }
    }
    mock_session = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = mock_payload
    mock_session.get.return_value = mock_resp

    res = deezer_auth.validate_deezer_arl("expired_cookie", session=mock_session)
    assert res["valid"] is False
    assert res["user_id"] == 0


def test_resolve_sound_format():
    hifi_user = {"lossless": True, "high_quality": True}
    free_user = {"lossless": False, "high_quality": False}

    assert deezer_auth.resolve_sound_format(hifi_user, "auto") == ("FLAC", "flac")
    assert deezer_auth.resolve_sound_format(hifi_user, "flac") == ("FLAC", "flac")
    assert deezer_auth.resolve_sound_format(hifi_user, "mp3_320") == ("MP3_320", "mp3")

    # Free user requests FLAC -> fallback to MP3_128
    assert deezer_auth.resolve_sound_format(free_user, "flac") == ("MP3_128", "mp3")
    assert deezer_auth.resolve_sound_format(free_user, "auto") == ("MP3_128", "mp3")


def test_create_collection_zip(tmp_path: Path):
    track1 = tmp_path / "01 - Song 1.mp3"
    track1.write_bytes(b"audio data 1")
    track2 = tmp_path / "02 - Song 2.mp3"
    track2.write_bytes(b"audio data 2")

    playlist = tmp_path / "Album Test.m3u8"
    playlist.write_text("#EXTM3U\n01 - Song 1.mp3\n02 - Song 2.mp3\n", encoding="utf-8")

    items = [
        {"collection_title": "Album Test", "status": "completed", "files": [str(track1)]},
        {"collection_title": "Album Test", "status": "completed", "files": [str(track2)]},
    ]

    zips = create_collection_zip(items, tmp_path, playlists=[playlist])
    assert len(zips) == 1
    assert zips[0].is_file()
    assert zips[0].name == "Album Test.zip"

    import zipfile
    with zipfile.ZipFile(zips[0], "r") as zf:
        namelist = zf.namelist()
        assert "01 - Song 1.mp3" in namelist
        assert "02 - Song 2.mp3" in namelist
        assert "Album Test.m3u8" in namelist
