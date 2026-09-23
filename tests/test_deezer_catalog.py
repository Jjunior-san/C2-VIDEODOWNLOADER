from pathlib import Path
from types import SimpleNamespace

import pytest

import audio_library
import deezer_catalog
import queue_service
from audio_library import create_deezer_playlists
from deezer_catalog import DeezerCollection, DeezerTrack
from download_control import DownloadControl
from download_queue import QueueRepository, queue_item


@pytest.mark.parametrize("mode,custom,expected", [
    ("Original / automática", "192", None),
    ("64 kbps", "192", 64),
    ("Personalizada", "173", 173),
])
def test_audio_bitrate_selection(mode, custom, expected):
    assert audio_library.audio_bitrate_kbps(mode, custom) == expected


@pytest.mark.parametrize("value", ["", "31", "321", "invalid"])
def test_custom_audio_bitrate_is_validated(value):
    with pytest.raises(ValueError):
        audio_library.audio_bitrate_kbps("Personalizada", value)


def test_recognizes_only_supported_public_deezer_pages():
    assert deezer_catalog.parse_deezer_url("https://www.deezer.com/br/track/3135556") == ("track", "3135556")
    assert deezer_catalog.parse_deezer_url("https://deezer.com/album/302127?utm=x") == ("album", "302127")
    assert deezer_catalog.parse_deezer_url("https://www.deezer.com/playlist/123/") == ("playlist", "123")
    assert deezer_catalog.parse_deezer_url("https://evil.example/track/3135556") is None
    assert deezer_catalog.parse_deezer_url("https://www.deezer.com/artist/27") == ("artist", "27")


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


def test_deezer_preview_respects_selected_audio_format_and_bitrate(tmp_path, monkeypatch):
    item = queue_item(
        "https://www.deezer.com/track/1", "Artist - Title (prévia Deezer)",
        kind="deezer_preview", media_id="1", collection_title="Album",
    )
    options = {
        "folder": str(tmp_path), "format": "Apenas áudio (Opus)", "playlist": True,
        "fragments": 4, "cookies_browser": "Nenhum", "cookies_file": "",
        "audio_bitrate_mode": "Personalizada", "audio_custom_bitrate": "128",
    }
    repository = QueueRepository(tmp_path / "queue-opus.db")
    repository.replace([item], options, [item["source"]])
    output = tmp_path / "preview.opus"
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
        "1", "Title", "Artist", "Album", "https://cdnt-preview.dzcdn.net/fresh.mp3",
    ))
    monkeypatch.setattr(queue_service, "apply_deezer_metadata", lambda *args: None)

    queue_service.run_queue(owner, repository, options, Path("engine"))

    args, kwargs = commands[0]
    assert args[2] == "Apenas áudio (Opus)"
    assert kwargs["audio_bitrate"] == 128
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

def test_public_deezer_search_encodes_query_limits_results_and_deduplicates(monkeypatch):
    requested = []

    def fake_api(url):
        requested.append(url)
        return {"data": [
            {"id": 10, "title": "La Câlin", "preview": "https://cdnt-preview.dzcdn.net/a.mp3",
             "artist": {"name": "Serhat Durmus"}, "album": {"title": "La Câlin"}},
            {"id": 10, "title": "La Câlin", "preview": "https://cdnt-preview.dzcdn.net/a.mp3",
             "artist": {"name": "Serhat Durmus"}, "album": {"title": "La Câlin"}},
            {"id": 11, "title": "Other", "preview": "https://cdnt-preview.dzcdn.net/b.mp3",
             "artist": {"name": "Artist"}, "album": {"title": "Album"}},
        ]}

    monkeypatch.setattr(deezer_catalog, "_api_json", fake_api)
    tracks = deezer_catalog.search_deezer_tracks("Serhat Durmus La Câlin", limit=50)

    assert [track.track_id for track in tracks] == ["10", "11"]
    assert "q=Serhat%20Durmus%20La%20C%C3%A2lin" in requested[0]
    assert "limit=50" in requested[0]


def test_deezer_search_source_adds_results_to_queue(tmp_path, monkeypatch):
    tracks = (
        DeezerTrack("10", "La Câlin", "Serhat Durmus", "La Câlin",
                    "https://cdnt-preview.dzcdn.net/a.mp3"),
        DeezerTrack("11", "Other", "Artist", "Album", None),
    )
    calls = []
    monkeypatch.setattr(
        queue_service,
        "search_deezer_tracks",
        lambda query, limit=25: calls.append((query, limit)) or tracks,
    )
    options = {"folder": str(tmp_path), "format": "Apenas áudio (MP3)", "playlist": True,
               "fragments": 4, "cookies_browser": "Nenhum", "cookies_file": ""}

    items = queue_service.discover(
        ["deezer: Serhat Durmus"], options, Path("engine"),
        DownloadControl(), {}, lambda line: None,
    )

    assert calls == [("Serhat Durmus", 25)]
    assert [item["media_id"] for item in items] == ["10", "11"]
    assert items[0]["source"] == "https://www.deezer.com/track/10"
    assert items[0]["collection_title"] == "Pesquisa Deezer - Serhat Durmus"
    assert items[1]["status"] == "skipped" and not items[1]["enabled"]

@pytest.mark.parametrize("query", ["Adele", "Coldplay Yellow", "Serhat Durmus La Câlin"])
def test_music_mode_plain_text_searches_deezer(tmp_path, monkeypatch, query):
    calls = []
    tracks = (
        DeezerTrack("10", "Result", "Artist", "Album",
                    "https://cdnt-preview.dzcdn.net/a.mp3"),
    )
    monkeypatch.setattr(
        queue_service,
        "search_deezer_tracks",
        lambda value, limit=25: calls.append((value, limit)) or tracks,
    )
    options = {
        "folder": str(tmp_path),
        "format": "Apenas áudio (MP3)",
        "playlist": True,
        "fragments": 4,
        "cookies_browser": "Nenhum",
        "cookies_file": "",
        "work_mode": "music",
    }

    items = queue_service.discover(
        [query], options, Path("engine"),
        DownloadControl(), {}, lambda line: None,
    )

    assert calls == [(query, 25)]
    assert len(items) == 1
    assert items[0]["kind"] == "deezer_preview"


def test_plain_text_in_video_mode_is_not_treated_as_deezer_search(tmp_path, monkeypatch):
    monkeypatch.setattr(
        queue_service,
        "search_deezer_tracks",
        lambda *args, **kwargs: pytest.fail("Deezer search should not run in video mode"),
    )
    monkeypatch.setattr(
        queue_service,
        "read_metadata",
        lambda *args, **kwargs: {"id": "v1", "title": "Generic result", "webpage_url": "https://example.com/v1"},
    )
    options = {
        "folder": str(tmp_path),
        "format": "Melhor MP4 compatível",
        "playlist": True,
        "fragments": 4,
        "cookies_browser": "Nenhum",
        "cookies_file": "",
        "work_mode": "video",
    }

    items = queue_service.discover(
        ["Adele"], options, Path("engine"),
        DownloadControl(), {}, lambda line: None,
    )

    assert items[0]["kind"] == "ytdlp"

@pytest.mark.parametrize(
    "kind,endpoint,title_key",
    [
        ("track", "/search?q=", "title"),
        ("artist", "/search/artist?q=", "name"),
        ("album", "/search/album?q=", "title"),
        ("playlist", "/search/playlist?q=", "title"),
    ],
)
def test_catalog_search_supports_music_artist_album_and_playlist(monkeypatch, kind, endpoint, title_key):
    requested = []

    def fake_api(url):
        requested.append(url)
        if kind == "track":
            return {"data": [{
                "id": 1, "title": "Song",
                "artist": {"name": "Artist"},
                "album": {"title": "Album", "cover_medium": "https://cdn-images.dzcdn.net/cover.jpg"},
            }]}
        if kind == "artist":
            return {"data": [{
                "id": 2, "name": "Artist",
                "picture_medium": "https://cdn-images.dzcdn.net/artist.jpg",
            }]}
        if kind == "album":
            return {"data": [{
                "id": 3, "title": "Album",
                "artist": {"name": "Artist"},
                "cover_medium": "https://cdn-images.dzcdn.net/album.jpg",
            }]}
        return {"data": [{
            "id": 4, "title": "Playlist",
            "user": {"name": "Owner"},
            "picture_medium": "https://cdn-images.dzcdn.net/playlist.jpg",
        }]}

    monkeypatch.setattr(deezer_catalog, "_api_json", fake_api)
    result = deezer_catalog.search_deezer_catalog("hello", kind=kind, limit=10)

    assert len(result) == 1
    assert result[0].kind == kind
    assert result[0].page_url == f"https://www.deezer.com/{kind}/{result[0].item_id}"
    assert endpoint in requested[0]


def test_artist_url_resolves_top_tracks(monkeypatch):
    calls = []

    def fake_api(url):
        calls.append(url)
        if "/artist/27/top" in url:
            return {"data": [{
                "id": 101,
                "title": "Top Song",
                "artist": {"name": "Daft Punk"},
                "album": {"title": "Album"},
                "preview": "https://cdnt-preview.dzcdn.net/a.mp3",
            }]}
        if "/artist/27" in url:
            return {"id": 27, "name": "Daft Punk"}
        raise AssertionError(url)

    monkeypatch.setattr(deezer_catalog, "_api_json", fake_api)
    collection = deezer_catalog.resolve_deezer_url("https://www.deezer.com/artist/27")

    assert collection.kind == "artist"
    assert collection.title == "Daft Punk"
    assert collection.tracks[0].title == "Top Song"

