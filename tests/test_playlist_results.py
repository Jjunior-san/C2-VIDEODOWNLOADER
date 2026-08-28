import queue
from pathlib import Path
from types import SimpleNamespace

import pytest

import c2_launcher
import youtube_downloader_app as app
from download_control import DownloadCancelled, DownloadControl
from kanald_downloader import KanalDCollection, KanalDError, KanalDVideo


def make_app():
    instance = object.__new__(app.DownloadApp)
    instance.download_control = DownloadControl()
    instance.event_queue = queue.Queue()
    instance.dependencies = SimpleNamespace(ensure=lambda *a, **kw: SimpleNamespace(yt_dlp_path=Path("yt-dlp")))
    instance.queue_log = lambda message: None
    instance._begin_download_item = lambda *a: None
    instance._build_command = lambda *a, **kw: []
    return instance


def test_launcher_finalizes_partial_playlist_and_continues_next_url(tmp_path):
    instance = make_app()
    first, last, next_file = (tmp_path / name for name in ("first.mp4", "last.mp4", "next.mp4"))
    results = iter([(1, [first, last]), (0, [next_file])])
    instance._run_downloader = lambda command: next(results)
    converted = []
    instance._ensure_player_compatibility = converted.append
    c2_launcher._download_with_jw_categories(instance, ["https://example.com/playlist", "https://example.com/video"], tmp_path, "Melhor MP4 compatível")
    assert converted == [first, last, next_file]
    assert instance.event_queue.get_nowait() == ("download_finished", {"failures": 1, "completed": 3})


def test_conversion_error_does_not_skip_remaining_completed_files(tmp_path):
    instance = make_app()
    files = [tmp_path / "first.mp4", tmp_path / "last.mp4"]
    converted = []

    def convert(path):
        converted.append(path)
        if path == files[0]:
            raise RuntimeError("Conversion failed")

    instance._ensure_player_compatibility = convert
    assert not instance._finalize_downloaded_files(0, files, "Melhor MP4 compatível")
    assert converted == files
    assert instance.download_completed_files == 1


def test_cancellation_is_not_treated_as_unavailable_entry(tmp_path):
    instance = make_app()
    instance.download_control.cancel()
    with pytest.raises(DownloadCancelled):
        instance._finalize_downloaded_files(1, [tmp_path / "video.mp4"], "Melhor MP4 compatível")


def test_kanald_season_continues_after_episode_without_source(tmp_path, monkeypatch):
    instance = make_app()
    instance.playlist_var = SimpleNamespace(get=lambda: True)
    source = "https://www.kanald.com.tr/uzak-sehir/bolumler"
    episodes = tuple(source + "/" + name for name in ("first", "hidden", "last"))
    monkeypatch.setattr(c2_launcher, "resolve_kanald_collection", lambda url: KanalDCollection(url, episodes))

    def resolve(url):
        name = url.rsplit("/", 1)[1]
        if name == "hidden":
            raise KanalDError("A página não informou uma fonte de vídeo compatível.")
        return KanalDVideo(name, name, "https://kanaldvod.duhnet.tv/" + name + ".m3u8")

    monkeypatch.setattr(c2_launcher, "resolve_kanald_video", resolve)
    files = [tmp_path / "first.mp4", tmp_path / "last.mp4"]
    results = iter((0, [path]) for path in files)
    instance._run_downloader = lambda command: next(results)
    converted = []
    instance._ensure_player_compatibility = converted.append
    c2_launcher._download_with_jw_categories(instance, [source], tmp_path, "Melhor MP4 compatível")
    assert converted == files
    assert instance.event_queue.get_nowait() == ("download_finished", {"failures": 1, "completed": 2})
