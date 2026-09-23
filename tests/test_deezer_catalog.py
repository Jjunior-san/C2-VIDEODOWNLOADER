from pathlib import Path
from types import SimpleNamespace

import audio_library
import deezer_catalog
import queue_service
from audio_library import create_deezer_playlists
from deezer_catalog import DeezerCollection, DeezerTrack
from download_control import DownloadControl
from download_queue import QueueRepository, queue_item


def test_recognizes_only_supported_public_deezer_pages():
    assert deezer_catalog.parse_deezer_url("https://www.deezer.com/br/track/3135556") == ("track", "3135556")
    assert deezer_catalog.parse_deezer_url("https://deezer.com/album/302127?utm=x") == ("album", "302127")
    assert deezer_catalog.parse_deezer_url("https://www.deezer.com/playlist/123/") == ("playlist", "123")
    assert deezer_catalog.parse_deezer_url("https://evil.example/track/3135556") is None
    assert deezer_catalog.parse_deezer_url("https://www.deezer.com/artist/27") is None


def test_album_uses_public_metadata_and_filters_untrusted_preview(monkeypatch):
    payload = {
        "title": "Discovery", "release_date": "2001-03-07",
        "cover_big": "https://cdn-images.dzcdn.net/images/cover/id/500x500.jpg",
        "tracks": {"data": [
            {"id": 1, "title": "One More Time", "duration": 320,
             "preview": "https://cdnt-preview.dzcdn.net/api/preview.mp3",
             "artist": {"name": "Daft Punk"}},
            {"id": 2, "title": "Unsafe", "preview": "https://evil.example/audio.mp3",
             "artist": {"name": "Daft Punk"}},
        ]},
    }
    monkeypatch.setattr(deezer_catalog, "_api_json", lambda url: payload)
    collection = deezer_catalog.resolve_deezer_url("https://www.deezer.com/album/302127")
    assert collection.title == "Discovery"
    assert collection.tracks[0].album == "Discovery"
    assert collection.tracks[0].release_year == "2001"
    assert collection.tracks[0].preview_url.endswith("preview.mp3")
    assert collection.tracks[1].preview_url is None


def test_discovery_marks_missing_previews_and_never_persists_signed_url(tmp_path, monkeypatch):
    tracks = (
        DeezerTrack("1", "Available", "Artist", "Album", "https://cdnt-preview.dzcdn.net/a.mp3"),
        DeezerTrack("2", "Missing", "Artist", "Album", None),
    )
    monkeypatch.setattr(queue_service, "resolve_deezer_url", lambda url: DeezerCollection(url, "Album", "album", tracks))
    options = {"folder": str(tmp_path), "format": "Apenas áudio (MP3)", "playlist": True,
               "fragments": 4, "cookies_browser": "Nenhum", "cookies_file": ""}
    items = queue_service.discover(
        ["https://www.deezer.com/album/123"], options, Path("engine"),
        DownloadControl(), {}, lambda line: None,
    )
    assert [item["kind"] for item in items] == ["deezer_preview", "deezer_preview"]
    assert items[1]["status"] == "skipped" and not items[1]["enabled"]
    assert "dzcdn" not in str(items)


def test_deezer_preview_refreshes_url_omits_cookies_and_uses_mp3(tmp_path, monkeypatch):
    item = queue_item(
        "https://www.deezer.com/track/1", "Artist - Title (prévia Deezer)",
        kind="deezer_preview", media_id="1", collection_title="Album",
        track_title="Title", artist="Artist", album="Album", duration=30,
        quality="Prévia oficial MP3",
    )
    options = {"folder": str(tmp_path), "format": "Melhor MP4 compatível", "playlist": True,
               "fragments": 4, "cookies_browser": "Chrome", "cookies_file": "cookies.txt"}
    repository = QueueRepository(tmp_path / "queue.db")
    repository.replace([item], options, ["https://www.deezer.com/track/1"])
    output = tmp_path / "preview.mp3"
    output.write_bytes(b"preview")
    owner = SimpleNamespace(
        download_control=DownloadControl(), event_queue=SimpleNamespace(put=lambda value: None),
        queue_log=lambda message: None, active_queue_id=None, download_completed_files=0,
        _begin_download_item=lambda *args: None, finalized_files=[],
    )
    commands = []
    owner._build_command = lambda *args, **kwargs: commands.append((args, kwargs)) or ["engine"]
    owner._run_downloader = lambda command: (0, [output])
    owner._finalize_downloaded_files = lambda code, outputs, fmt: (
        setattr(owner, "finalized_files", outputs) or True
    )
    monkeypatch.setattr(queue_service, "resolve_deezer_track", lambda track_id: DeezerTrack(
        "1", "Title", "Artist", "Album", "https://cdnt-preview.dzcdn.net/fresh.mp3", duration=30,
    ))
    monkeypatch.setattr(queue_service, "apply_deezer_metadata", lambda *args: None)
    queue_service.run_queue(owner, repository, options, Path("engine"))
    args, kwargs = commands[0]
    assert args[2] == "Apenas áudio (MP3)"
    assert args[3] == "https://cdnt-preview.dzcdn.net/fresh.mp3"
    assert kwargs["include_cookies"] is False
    assert repository.snapshot()["items"][0]["status"] == "completed"


def test_local_m3u8_contains_only_completed_existing_previews(tmp_path):
    files = []
    items = []
    for index in range(2):
        path = tmp_path / f"{index + 1}.mp3"
        path.write_bytes(b"audio")
        files.append(path)
        items.append({
            "kind": "deezer_preview", "status": "completed", "collection_title": "My Album",
            "title": f"Track {index + 1}", "duration": 30, "files": [str(path)],
        })
    playlists = create_deezer_playlists(items, tmp_path)
    assert playlists == [tmp_path / "My Album.m3u8"]
    text = playlists[0].read_text(encoding="utf-8-sig")
    assert "#EXTM3U" in text and "1.mp3" in text and "2.mp3" in text


def test_mp3_metadata_writer_is_atomic_and_replaces_old_id3_tag(tmp_path, monkeypatch):
    path = tmp_path / "preview.mp3"
    audio_payload = b"\xff\xfb" + b"audio" * 100
    path.write_bytes(audio_payload)
    monkeypatch.setattr(audio_library, "_cover_bytes", lambda url: b"\xff\xd8cover\xff\xd9")
    item = {
        "track_title": "Título", "artist": "Artista", "album": "Álbum",
        "track_number": 2, "disc_number": 1, "release_year": "2026",
        "cover_url": "https://cdn-images.dzcdn.net/cover.jpg",
    }
    audio_library.apply_deezer_metadata(path, item)
    first = path.read_bytes()
    assert first.startswith(b"ID3\x03") and first.endswith(audio_payload)
    assert all(frame in first for frame in (b"TIT2", b"TPE1", b"TALB", b"TRCK", b"TPOS", b"TDRC", b"APIC"))

    item["track_title"] = "Novo título"
    audio_library.apply_deezer_metadata(path, item)
    second = path.read_bytes()
    assert second.count(b"ID3\x03") == 1
    assert second.endswith(audio_payload)


def test_mp3_metadata_writer_preserves_file_with_corrupt_oversized_id3(tmp_path, monkeypatch):
    path = tmp_path / "corrupt.mp3"
    original = b"ID3\x03\x00\x00\x7f\x7f\x7f\x7f" + b"original-audio"
    path.write_bytes(original)
    monkeypatch.setattr(audio_library, "_cover_bytes", lambda url: None)

    audio_library.apply_deezer_metadata(path, {"track_title": "Safe"})

    result = path.read_bytes()
    assert result.startswith(b"ID3\x03")
    assert result.endswith(original)
