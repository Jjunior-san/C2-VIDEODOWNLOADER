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
import youtube_downloader_app as app


def options(tmp_path):
    return {"folder": str(tmp_path), "format": "Melhor MP4 compatível", "playlist": True,
            "fragments": 4, "cookies_browser": "Nenhum", "cookies_file": ""}


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
