import queue
import subprocess
from pathlib import Path

import pytest

import youtube_downloader_app as app
from download_control import DownloadControl
from media_conversion import codec_arguments, duration_from_probe, run_conversion, stream_compatibility


@pytest.mark.parametrize("video,audio,expected", [
    ("Video: h264, yuv420p(progressive)", "Audio: aac (LC)", (True, True)),
    ("Video: h264, yuv420p10le", "Audio: aac (LC)", (False, True)),
    ("Video: h264, yuv420p", "Audio: aac (HE-AAC)", (True, False)),
    ("Video: h264, yuv420p", "Audio: aac (LC)\nAudio: mp3", (True, False)),
    ("Video: vp9, yuv420p", "", (False, True)),
])
def test_compatibility(video, audio, expected):
    assert stream_compatibility(video, audio) == expected


def test_audio_only_fix_copies_video():
    args = codec_arguments(True, False)
    assert args[args.index("-c:v") + 1] == "copy"
    assert args[args.index("-c:a") + 1] == "aac"
    assert "libx264" not in args
    assert codec_arguments(True, True) == ["-c:v", "copy", "-c:a", "copy"]
    assert duration_from_probe("Duration: 02:17:37.25, start: 0.0") == 8257.25
    assert duration_from_probe("Duration: N/A") is None


def test_failed_conversion_preserves_original_and_existing_destination(tmp_path, monkeypatch):
    source = tmp_path / "video.mkv"
    source.write_bytes(b"original")
    existing = tmp_path / "video.mp4"
    existing.write_bytes(b"existing")
    instance = object.__new__(app.DownloadApp)
    instance.download_control = DownloadControl()
    instance.event_queue = queue.Queue()
    instance.queue_log = lambda message: None
    instance._stream_details = lambda path: ("Video: h264, yuv420p", "Audio: mp3", 2)
    monkeypatch.setattr(app, "FFMPEG_PATH", "ffmpeg")

    def fail(command, *args, **kwargs):
        Path(command[-1]).write_bytes(b"incomplete")
        raise RuntimeError("test error")

    monkeypatch.setattr(app, "run_conversion", fail)
    with pytest.raises(RuntimeError, match="test error"):
        instance._ensure_player_compatibility(source)
    assert source.read_bytes() == b"original"
    assert existing.read_bytes() == b"existing"
    assert not list(tmp_path.glob(".c2-*"))


def test_real_ffmpeg_audio_conversion_with_progress(tmp_path):
    if not app.FFMPEG_PATH:
        pytest.skip("FFmpeg not installed")
    source = tmp_path / "sample.mkv"
    subprocess.run([
        app.FFMPEG_PATH, "-v", "error", "-f", "lavfi", "-i", "color=size=160x90:rate=10",
        "-f", "lavfi", "-i", "sine=frequency=440", "-t", "1", "-c:v", "libx264",
        "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le", str(source),
    ], check=True, capture_output=True, timeout=30)
    instance = object.__new__(app.DownloadApp)
    instance.download_control = DownloadControl()
    instance.event_queue = queue.Queue()
    instance.queue_log = lambda message: None
    output = instance._ensure_player_compatibility(source)
    video, audio, duration = instance._stream_details(output)
    assert stream_compatibility(video, audio) == (True, True)
    assert duration and duration > 0
    assert output.exists()
    payloads = list(instance.event_queue.queue)
    assert any(payload.get("percent") is not None for _, payload in payloads)
    assert any("apenas o áudio" in payload.get("label", "") for _, payload in payloads)


def test_failed_process_reports_error():
    import sys
    with pytest.raises(RuntimeError, match="failure"):
        run_conversion(
            [sys.executable, "-c", "import sys; print('failure'); sys.exit(1)"],
            DownloadControl(), 1, lambda payload: None,
        )


def test_conversion_timeout_excludes_pause():
    import sys
    import threading
    import time
    control = DownloadControl()
    ready = threading.Event()
    errors = []

    def convert():
        try:
            run_conversion(
                [sys.executable, "-u", "-c",
                 "import time; print('out_time_us=100000'); print('progress=continue'); "
                 "time.sleep(0.25); print('out_time_us=1000000'); print('progress=end')"],
                control, 1, lambda payload: ready.set(), timeout=1,
            )
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=convert)
    worker.start()
    try:
        assert ready.wait(10)
        control.pause()
        time.sleep(1.1)
        control.resume()
        worker.join(timeout=10)
        assert not worker.is_alive()
        assert not errors
    finally:
        control.cancel()
        worker.join(timeout=5)
