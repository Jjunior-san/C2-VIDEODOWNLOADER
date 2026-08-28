"""Exercise the actual yt-dlp CLI: option-only tests missed the quiet-mode bug."""
import os
import queue
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

import youtube_downloader_app as app
from download_control import DownloadControl

pytest.importorskip("yt_dlp")
BLOB = bytes(range(256)) * 8192


@pytest.fixture
def server():
    state = {"active": 0, "maximum": 0, "ranges": []}
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == "/deleted.mp4":
                self.send_error(410, "Gone")
                return
            is_segment = self.path.endswith(".ts")
            if self.path.endswith(".m3u8"):
                body = (
                    "#EXTM3U\n#EXT-X-TARGETDURATION:2\n#EXT-X-MEDIA-SEQUENCE:0\n"
                    + "".join(f"#EXTINF:2,\nsegment{i}.ts\n" for i in range(8))
                    + "#EXT-X-ENDLIST\n"
                ).encode()
                content_type = "application/vnd.apple.mpegurl"
            elif is_segment:
                body = BLOB[:65536]
                content_type = "video/mp2t"
            else:
                body = state.get("blob", BLOB)
                content_type = "video/mp4"
            offset = 0
            if self.headers.get("Range"):
                offset = int(self.headers["Range"].split("=")[1].split("-")[0])
                if self.headers.get("If-Range") and self.headers["If-Range"] != state.get("etag", '"fixture-v1"'):
                    offset = 0
                state["ranges"].append(offset)
            total_size = len(body)
            body = body[offset:]
            self.send_response(206 if offset else 200)
            if offset:
                self.send_header("Content-Range", f"bytes {offset}-{total_size - 1}/{total_size}")
            self.send_header("Content-Type", content_type)
            self.send_header("ETag", state.get("etag", '"fixture-v1"'))
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if is_segment:
                with lock:
                    state["active"] += 1
                    state["maximum"] = max(state["maximum"], state["active"])
            try:
                if is_segment:
                    time.sleep(0.1)
                for offset in range(0, len(body), 8192):
                    self.wfile.write(body[offset:offset + 8192])
                    if not self.path.endswith(".m3u8"):
                        time.sleep(0.006)
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                if is_segment:
                    with lock:
                        state["active"] -= 1

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}", state
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def make_download(tmp_path, monkeypatch, url, fragments=4, playlist=False):
    monkeypatch.setattr(app, "FFMPEG_PATH", None)
    instance = object.__new__(app.DownloadApp)
    instance.download_control = DownloadControl()
    instance.download_fragments = fragments
    instance.playlist_var = SimpleNamespace(get=lambda: playlist)
    instance.dependencies = SimpleNamespace(runtime_environment=lambda: os.environ.copy())
    instance.event_queue = queue.Queue()
    events = []
    ready = threading.Event()
    instance._emit_media_progress = lambda payload: (events.append(payload), ready.set())
    instance.queue_log = lambda message: None
    command = instance._build_command(Path("yt-dlp"), tmp_path, "Melhor MP4 compatível", url, include_cookies=False)
    command = [sys.executable, "-m", "yt_dlp", "--ignore-config", "--fixup", "never", *command[1:]]
    return instance, command, events, ready


def test_real_cli_reports_size_speed_and_resumes_without_changing_bytes(tmp_path, monkeypatch, server):
    instance, command, events, ready = make_download(tmp_path, monkeypatch, server[0] + "/video.mp4")
    results, errors = [], []

    def download():
        try:
            results.append(instance._run_downloader(command))
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=download)
    worker.start()
    try:
        assert ready.wait(20), "The real command emitted no progress (check --print / --progress)"
        instance.download_control.pause()
        assert instance.download_control.paused
        partials = list(tmp_path.glob("*.part"))
        sizes = {path: path.stat().st_size for path in partials}
        time.sleep(0.2)
        assert all(path.stat().st_size == size for path, size in sizes.items())
        instance.download_control.resume()
        worker.join(timeout=25)
        assert not worker.is_alive()
        assert not errors
        assert results[0][0] == 0
        assert results[0][1][0].read_bytes() == BLOB
        assert any(event["total"] == len(BLOB) for event in events)
        assert any(event["speed"] and event["speed"] > 0 for event in events)
    finally:
        instance.download_control.cancel()
        worker.join(timeout=10)


def test_hls_downloads_multiple_fragments_concurrently(tmp_path, monkeypatch, server):
    instance, command, events, _ = make_download(tmp_path, monkeypatch, server[0] + "/media.m3u8")
    result, files = instance._run_downloader(command)
    assert result == 0
    assert events
    assert server[1]["maximum"] >= 2
    assert files[0].read_bytes() == BLOB[:65536] * 8


def test_real_playlist_continues_after_private_and_removed_entries(tmp_path, monkeypatch, server):
    port = server[0].rsplit(":", 1)[1]
    instance, command, events, _ = make_download(
        tmp_path, monkeypatch, f"c2fixture:playlist:{port}", playlist=True,
    )
    plugin = Path(__file__).parent / "fixtures"
    command[3:3] = ["--plugin-dirs", str(plugin)]
    logs = []
    instance.queue_log = logs.append
    return_code, files = instance._run_downloader(command)
    assert return_code != 0  # The unavailable entries remain visible as warnings.
    assert any("Private video" in line for line in logs), logs
    assert any("410" in line for line in logs)
    assert len(files) == 2
    assert "first" in files[0].name and "last" in files[1].name
    assert all(path.read_bytes() == BLOB for path in files)
    assert events

    finalized = []
    instance._ensure_player_compatibility = finalized.append
    assert not instance._finalize_downloaded_files(return_code, files, "Melhor MP4 compatível")
    assert finalized == files
    assert instance.download_completed_files == 2


def test_playlist_options_do_not_allow_incomplete_video_fragments(tmp_path, monkeypatch):
    _, command, _, _ = make_download(tmp_path, monkeypatch, "https://example.com/playlist", playlist=True)
    assert "--yes-playlist" in command
    assert "--no-abort-on-error" in command
    assert "no-youtube-unavailable-videos" in command
    assert "--abort-on-unavailable-fragments" in command
    assert "--ignore-errors" not in command


def test_discovery_uses_actual_cli_to_expand_playlist(tmp_path, monkeypatch, server):
    from queue_service import metadata_items, read_metadata
    port = server[0].rsplit(":", 1)[1]
    instance, _, _, _ = make_download(tmp_path, monkeypatch, server[0] + "/video.mp4")
    original_popen = instance.download_control.popen
    plugin = Path(__file__).parent / "fixtures"
    instance.download_control.popen = lambda command, **kwargs: original_popen(
        [sys.executable, "-m", "yt_dlp", "--plugin-dirs", str(plugin), *command[1:]], **kwargs,
    )
    source = f"c2fixture:playlist:{port}"
    metadata = read_metadata(Path("yt-dlp"), source, {"playlist": True}, instance.download_control, os.environ.copy(), lambda line: None)
    items = metadata_items(metadata, source)
    assert len(items) == 4
    assert all(item["source"].startswith("c2fixture:") for item in items)


def test_persisted_queue_resumes_real_bytes_and_skips_completed_video(tmp_path, monkeypatch, server):
    from download_queue import QueueRepository, queue_item
    from queue_service import run_queue
    items = [queue_item(server[0] + f"/{name}.mp4", name) for name in ("first", "second")]
    options = {"folder": str(tmp_path), "format": "Melhor MP4 compatível", "playlist": True, "fragments": 4}
    repository = QueueRepository(tmp_path / "queue.db")
    repository.replace(items, options, [server[0]])
    ready = threading.Event()

    def owner():
        instance, _, _, _ = make_download(tmp_path, monkeypatch, server[0] + "/unused.mp4")
        instance.download_options = options
        original = instance._build_command
        instance._build_command = lambda *args, **kwargs: [sys.executable, "-m", "yt_dlp", "--fixup", "never", *original(*args, **kwargs)[1:]]
        instance._ensure_player_compatibility = lambda path: path
        instance._emit_media_progress = lambda event: ready.set() if instance.active_queue_id == items[1]["id"] and event["downloaded"] > 65536 else None
        return instance

    first = owner()
    worker = threading.Thread(target=run_queue, args=(first, repository, options, Path("yt-dlp")))
    worker.start()
    try:
        assert ready.wait(25)
        first.download_control.cancel()
        worker.join(timeout=15)
        assert not worker.is_alive()
        states = repository.snapshot()["items"]
        assert states[0]["status"] == "completed"
        assert states[1]["status"] == "interrupted"
        first_file = Path(states[0]["files"][0])
        first_mtime = first_file.stat().st_mtime_ns
        assert list(tmp_path.glob("*.part"))
        restored = QueueRepository(repository.path)
        restored.recover()
        second = owner()
        run_queue(second, restored, options, Path("yt-dlp"))
        assert all(item["status"] == "completed" for item in restored.snapshot()["items"])
        assert first_file.stat().st_mtime_ns == first_mtime
        assert any(offset > 0 for offset in server[1]["ranges"])
        for item in restored.snapshot()["items"]:
            assert Path(item["files"][0]).read_bytes() == BLOB
    finally:
        first.download_control.cancel()
        worker.join(timeout=10)


def test_direct_download_resumes_with_if_range_validator(tmp_path, server):
    from download_control import DownloadCancelled
    from jw_org_downloader import JWDownloadItem, download_item
    media = JWDownloadItem(media_id="test", title="Test", download_url=server[0] + "/video.mp4",
                           extension=".mp4", filesize=len(BLOB), height=1080, source_kind="video")

    def interrupt(received, total):
        if received >= 1024 * 1024:
            raise DownloadCancelled("Test interruption")

    with pytest.raises(DownloadCancelled):
        download_item(media, tmp_path, 1, 1, progress=interrupt)
    assert list(tmp_path.glob("*.part"))
    complete = download_item(media, tmp_path, 1, 1)
    assert complete.read_bytes() == BLOB
    assert 1024 * 1024 in server[1]["ranges"]


def test_direct_download_restarts_if_server_content_changed(tmp_path, server):
    from download_control import DownloadCancelled
    from jw_org_downloader import JWDownloadItem, download_item
    media = JWDownloadItem(title="Test", media_id="test", download_url=server[0] + "/video.mp4",
                           filesize=len(BLOB), height=1080, extension=".mp4", source_kind="video")

    def interrupt(received, total):
        if received >= 1024 * 1024:
            raise DownloadCancelled("Test interruption")

    with pytest.raises(DownloadCancelled):
        download_item(media, tmp_path, 1, 1, progress=interrupt)
    server[1]["etag"] = '"fixture-v2"'
    server[1]["blob"] = b"z" * len(BLOB)
    complete = download_item(media, tmp_path, 1, 1)
    assert complete.read_bytes() == server[1]["blob"]
