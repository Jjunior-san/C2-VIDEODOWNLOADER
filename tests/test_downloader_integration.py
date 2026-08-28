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
    state = {"active": 0, "maximum": 0}
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
                body = BLOB
                content_type = "video/mp4"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
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
