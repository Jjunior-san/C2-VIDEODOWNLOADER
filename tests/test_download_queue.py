import json
import queue
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

import queue_service
from download_control import DownloadCancelled, DownloadControl, DownloadSkipped
from download_queue import QueueRepository, queue_item, queue_summary
from kanald_downloader import KanalDVideo, KanalDError, KanalDCollection
from ui_layout import choose_font
from process_monitor import ProcessInactivityError
from queue_ui import queue_options_compatible
import youtube_downloader_app as app


def options(tmp_path):
    return {"folder": str(tmp_path), "format": "Melhor MP4 compatível", "playlist": True,
            "fragments": 4, "cookies_browser": "Nenhum", "cookies_file": ""}


def test_video_queue_ignores_changes_to_inactive_music_folder():
    saved = {
        "work_mode": "video", "folder": r"C:\Videos", "video_folder": r"C:\Videos",
        "music_folder": r"C:\Music-A", "format": "1080p", "video_format": "1080p",
        "music_format": "Apenas áudio (MP3)", "playlist": True, "fragments": 4,
        "cookies_browser": "Nenhum", "cookies_file": "",
    }
    current = dict(saved, music_folder=r"D:\Music-B", music_format="Apenas áudio (Opus)")
    assert queue_options_compatible(current, saved)
    assert not queue_options_compatible(dict(current, video_folder=r"D:\Videos"), saved)


def test_music_queue_ignores_changes_to_inactive_video_folder():
    saved = {
        "work_mode": "music", "folder": r"C:\Music", "music_folder": r"C:\Music",
        "video_folder": r"C:\Videos-A", "format": "Apenas áudio (MP3)",
        "music_format": "Apenas áudio (MP3)", "video_format": "1080p",
        "playlist": True, "fragments": 4, "cookies_browser": "Nenhum", "cookies_file": "",
        "audio_bitrate_mode": "Original / automática", "audio_custom_bitrate": "192",
        "music_structure": "Artista\\Álbum", "music_filename_template": "{titulo}",
        "deezer_arl": "", "deezer_quality": "auto", "create_collection_zip": False,
    }
    current = dict(saved, video_folder=r"D:\Videos-B", video_format="720p")
    assert queue_options_compatible(current, saved)
    assert not queue_options_compatible(dict(current, music_folder=r"D:\Music"), saved)


def test_queue_routes_public_music_and_video_to_independent_folders(tmp_path, monkeypatch):
    from deezer_catalog import DeezerTrack

    music_folder = tmp_path / "music"
    video_folder = tmp_path / "video"
    items = [
        queue_item(
            "https://www.deezer.com/track/1", "Music", kind="deezer_preview",
            media_id="1", collection_title="Album",
        ),
        queue_item("https://example.com/video", "Video"),
    ]
    saved_options = {
        "folder": str(video_folder), "music_folder": str(music_folder),
        "video_folder": str(video_folder), "format": "720p", "video_format": "720p",
        "music_format": "Apenas áudio (MP3)", "playlist": True, "fragments": 4,
        "cookies_browser": "Nenhum", "cookies_file": "", "work_mode": "video",
        "audio_bitrate_mode": "Original / automática", "audio_custom_bitrate": "192",
        "music_structure": "Pasta raiz", "music_filename_template": "{titulo}",
    }
    repository = QueueRepository(tmp_path / "separate-folders.db")
    repository.replace(items, saved_options, [])
    owner = make_owner()
    owner.finalized_files = []
    calls = []

    def build_command(engine, folder, format_choice, url, **kwargs):
        calls.append((Path(folder), format_choice))
        return [str(folder), format_choice]

    def download(command):
        folder = Path(command[0])
        suffix = ".mp3" if "MP3" in command[1] else ".mp4"
        output = folder / f"output-{len(calls)}{suffix}"
        output.write_bytes(b"media")
        return 0, [output]

    owner._build_command = build_command
    owner._run_downloader = download
    owner._finalize_downloaded_files = lambda code, outputs, fmt: (
        setattr(owner, "finalized_files", outputs) or True
    )
    monkeypatch.setattr(queue_service, "resolve_deezer_track", lambda track_id: DeezerTrack(
        "1", "Music", "Artist", "Album", "https://cdnt-preview.dzcdn.net/preview.mp3",
    ))
    monkeypatch.setattr(queue_service, "apply_deezer_metadata", lambda *args: None)

    queue_service.run_queue(owner, repository, saved_options, Path("engine"))

    assert calls[0][0] == music_folder
    assert calls[1][0] == video_folder
    completed = repository.snapshot()["items"]
    assert Path(completed[0]["files"][0]).parent == music_folder
    assert Path(completed[1]["files"][0]).parent == video_folder


def test_persistence_recovers_only_unfinished_items(tmp_path):
    repository = QueueRepository(tmp_path / "queue.db")
    items = [queue_item(f"https://example.com/{i}", f"Video {i}") for i in range(4)]
    repository.replace(items, options(tmp_path), ["https://example.com/playlist"])
    repository.update(items[0]["id"], status="completed", files=["video.mp4"])
    repository.update(items[1]["id"], status="finalizing", downloaded_files=["partial.mp4"])
    repository.update(items[2]["id"], enabled=False)
    repository.update(items[3]["id"], status="failed", error="Connection failed")
    restored = QueueRepository(repository.path).recover()
    assert [item["status"] for item in restored["items"]] == ["completed", "interrupted", "pending", "failed"]
    assert restored["options"] == options(tmp_path)
    assert restored["items"][0]["files"] == ["video.mp4"]
    assert restored["items"][1]["downloaded_files"] == ["partial.mp4"]
    assert restored["items"][2]["enabled"] is False


def test_unknown_queue_schema_is_preserved(tmp_path):
    repository = QueueRepository(tmp_path / "queue.db")
    with sqlite3.connect(repository.path) as connection:
        connection.execute("INSERT INTO queue VALUES (1, ?)", (json.dumps({"version": 99}),))
    with pytest.raises(ValueError):
        repository.recover()
    with sqlite3.connect(repository.path) as connection:
        assert json.loads(connection.execute("SELECT data FROM queue").fetchone()[0])["version"] == 99


def test_removing_queue_records_never_deletes_downloaded_files(tmp_path):
    repository = QueueRepository(tmp_path / "queue.db")
    completed_file = tmp_path / "completed.mp4"
    partial_file = tmp_path / "partial.mp4.part"
    completed_file.write_bytes(b"completed")
    partial_file.write_bytes(b"partial")
    completed = queue_item("https://example.com/completed", "Completed")
    completed.update(status="completed", files=[str(completed_file)])
    pending = queue_item("https://example.com/pending", "Pending")
    pending["downloaded_files"] = [str(partial_file)]
    repository.replace([completed, pending], options(tmp_path), ["https://example.com/playlist"])

    assert repository.remove_many([completed["id"]]) == 1
    assert [item["id"] for item in repository.snapshot()["items"]] == [pending["id"]]
    assert completed_file.read_bytes() == b"completed"
    assert repository.clear() == 1
    assert repository.snapshot()["items"] == []
    assert repository.snapshot()["sources"] == []
    assert partial_file.read_bytes() == b"partial"


def test_global_progress_counts_selected_videos_not_input_links():
    items = [queue_item("https://example.com", str(i)) for i in range(5)]
    items[0]["status"] = "completed"
    items[1]["status"] = "failed"
    items[2]["status"] = "downloading"
    items[4]["enabled"] = False
    summary = queue_summary(items, items[2]["id"], 50)
    assert summary == {"total": 4, "done": 2, "overall": 62.5}
    assert queue_summary(items, items[2]["id"], 100)["overall"] < 75


def test_san_francisco_uses_installed_families_and_has_fallback():
    fonts = ["Segoe UI", "SF UI Text", "SF UI Display"]
    assert choose_font(fonts) == "SF UI Text"
    assert choose_font(fonts, True) == "SF UI Display"
    assert choose_font(["Arial", "Segoe UI"]) == "Segoe UI"


def test_flat_playlist_preserves_stable_links_and_unavailable_rows():
    info = {"_type": "playlist", "entries": [
        {"id": "1", "title": "first", "url": "https://example.com/first"},
        {"id": "2", "title": "[Private video]", "url": "https://example.com/second"},
        {"id": "3", "title": "third", "webpage_url": "https://example.com/third", "url": "https://cdn.example.com/signed?token=secret"},
    ]}
    items = queue_service.metadata_items(info, "https://example.com/playlist")
    assert len(items) == 3
    assert items[1]["status"] == "skipped" and not items[1]["enabled"]
    assert items[2]["source"] == "https://example.com/third"
    assert "secret" not in json.dumps(items)


def make_owner():
    owner = object.__new__(app.DownloadApp)
    owner.download_control = DownloadControl()
    owner.event_queue = queue.Queue()
    owner.queue_log = lambda message: None
    owner._begin_download_item = lambda *args: owner.download_control.checkpoint()
    owner._build_command = lambda *args, **kwargs: []
    owner._ensure_player_compatibility = lambda path: path
    return owner


def test_download_retries_once_after_engine_inactivity():
    owner = make_owner()
    owner.queue_log = lambda message: logs.append(message)
    calls, logs = [], []

    def run_once(command):
        calls.append(command)
        if len(calls) == 1:
            raise ProcessInactivityError("silent")
        return 0, []

    owner._run_downloader_once = run_once
    assert owner._run_downloader(["engine"]) == (0, [])
    assert len(calls) == 2
    assert any("reiniciando" in line for line in logs)


def test_cancel_one_video_continues_the_remaining_queue(tmp_path):
    repository = QueueRepository(tmp_path / "queue.db")
    items = [queue_item(f"https://example.com/{i}", str(i)) for i in range(3)]
    repository.replace(items, options(tmp_path), [])
    owner = make_owner()
    calls = []

    def download(command):
        calls.append(owner.active_queue_id)
        if len(calls) == 2:
            owner.download_control.skip()
            owner.download_control.checkpoint()
        return 0, [tmp_path / f"{len(calls)}.mp4"]

    owner._run_downloader = download
    queue_service.run_queue(owner, repository, options(tmp_path), Path("engine"))
    assert len(calls) == 3
    assert [item["status"] for item in repository.snapshot()["items"]] == ["completed", "cancelled", "completed"]
    owner.download_control.checkpoint()  # Cancelling one entry did not cancel the job.


def test_stop_recovers_queue_and_does_not_repeat_completed_items(tmp_path):
    repository = QueueRepository(tmp_path / "queue.db")
    items = [queue_item(f"https://example.com/{i}", str(i)) for i in range(3)]
    repository.replace(items, options(tmp_path), [])
    owner = make_owner()
    calls = []

    def download(command):
        calls.append(owner.active_queue_id)
        if len(calls) == 2:
            owner.download_control.cancel()
            owner.download_control.checkpoint()
        return 0, [tmp_path / "first.mp4"]

    owner._run_downloader = download
    queue_service.run_queue(owner, repository, options(tmp_path), Path("engine"))
    assert [item["status"] for item in repository.snapshot()["items"]] == ["completed", "interrupted", "pending"]
    repository = QueueRepository(repository.path)
    repository.recover()
    resumed = make_owner()
    resumed_ids = []
    resumed._run_downloader = lambda command: (resumed_ids.append(resumed.active_queue_id) or 0, [tmp_path / "next.mp4"])
    queue_service.run_queue(resumed, repository, options(tmp_path), Path("engine"))
    assert resumed_ids == [items[1]["id"], items[2]["id"]]
    assert all(item["status"] == "completed" for item in repository.snapshot()["items"])


def test_received_file_is_finalized_without_downloading_again(tmp_path):
    repository = QueueRepository(tmp_path / "queue.db")
    item = queue_item("https://example.com/video", "Video")
    file = tmp_path / "received.mp4"
    file.write_bytes(b"received")
    item.update(status="finalizing", downloaded_files=[str(file)])
    repository.replace([item], options(tmp_path), [])
    repository.recover()
    owner = make_owner()
    owner._run_downloader = lambda command: pytest.fail("A received file must not be downloaded again")
    queue_service.run_queue(owner, repository, options(tmp_path), Path("engine"))
    assert repository.snapshot()["items"][0]["status"] == "completed"


def test_filename_escapes_templates_and_is_safe():
    item = queue_item("https://example.com", '../hello/%(title)s: 100%')
    template = queue_service.filename_template(item, 1)
    assert "/" not in template and "\\" not in template
    assert "%%(title)s" in template and "100%%" in template
    assert template.endswith(".%(ext)s")


def test_kanald_retry_refreshes_url_without_changing_partial_filename(tmp_path, monkeypatch):
    repository = QueueRepository(tmp_path / "queue.db")
    item = queue_item("https://www.kanald.com.tr/uzak-sehir/bolumler/episode", "slug title", kind="kanald")
    repository.replace([item], options(tmp_path), [])
    generation = [1]
    monkeypatch.setattr(queue_service, "resolve_kanald_video", lambda source: KanalDVideo(
        f"Title {generation[0]}", "video-id", f"https://kanaldvod.duhnet.tv/video?token={generation[0]}",
    ))
    owner = make_owner()
    commands = []
    owner._build_command = lambda *args, **kwargs: commands.append((args[3], kwargs["output_template"])) or []
    owner._run_downloader = lambda command: (1, [])
    queue_service.run_queue(owner, repository, options(tmp_path), Path("engine"))
    generation[0] = 2
    repository.update(item["id"], status="pending")
    owner._run_downloader = lambda command: (0, [tmp_path / "video.mp4"])
    queue_service.run_queue(owner, repository, options(tmp_path), Path("engine"))
    assert commands[0][0] != commands[1][0]
    assert commands[0][1] == commands[1][1]
    assert "token=" not in json.dumps(repository.snapshot())


def test_discovered_season_continues_when_middle_episode_is_unavailable(tmp_path, monkeypatch):
    source = "https://www.kanald.com.tr/uzak-sehir/bolumler"
    urls = tuple(source + "/" + name for name in ("first", "hidden", "last"))
    monkeypatch.setattr(queue_service, "resolve_kanald_collection", lambda url: KanalDCollection(url, urls))
    items = queue_service.discover([source], options(tmp_path), Path("engine"), DownloadControl(), {}, lambda line: None)
    assert len(items) == 3
    repository = QueueRepository(tmp_path / "queue.db")
    repository.replace(items, options(tmp_path), [source])

    def resolve(url):
        name = url.rsplit("/", 1)[1]
        if name == "hidden":
            raise KanalDError("Vídeo indisponível")
        return KanalDVideo(name, name, "https://kanaldvod.duhnet.tv/" + name)

    monkeypatch.setattr(queue_service, "resolve_kanald_video", resolve)
    owner = make_owner()
    owner._run_downloader = lambda command: (0, [tmp_path / "video.mp4"])
    queue_service.run_queue(owner, repository, options(tmp_path), Path("engine"))
    assert [item["status"] for item in repository.snapshot()["items"]] == ["completed", "failed", "completed"]
