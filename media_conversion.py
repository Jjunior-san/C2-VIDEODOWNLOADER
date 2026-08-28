from __future__ import annotations

import queue
import re
import subprocess
import threading
from collections import deque
from collections.abc import Callable

from download_control import DownloadControl


def stream_compatibility(video: str, audio: str) -> tuple[bool, bool]:
    video = video.lower()
    audio = audio.lower()
    video_ok = "video: h264" in video and re.search(r"\byuv420p\b", video) is not None
    audio_ok = all(
        "audio: aac" in line and "he-aac" not in line and "he_aac" not in line
        for line in audio.splitlines() if line.strip()
    )
    return video_ok, audio_ok


def codec_arguments(video_ok: bool, audio_ok: bool) -> list[str]:
    video = ["-c:v", "copy"] if video_ok else [
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
    ]
    audio = ["-c:a", "copy"] if audio_ok else [
        "-c:a", "aac", "-profile:a", "aac_low", "-b:a", "160k",
    ]
    return video + audio


def duration_from_probe(output: str) -> float | None:
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", output)
    if not match:
        return None
    hours, minutes, seconds = map(float, match.groups())
    duration = hours * 3600 + minutes * 60 + seconds
    return duration if duration > 0 else None


def run_conversion(
    command: list[str],
    control: DownloadControl,
    duration: float | None,
    progress: Callable[[dict[str, object]], None],
    *,
    creationflags: int = 0,
    timeout: float = 7200,
) -> None:
    process = control.popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", creationflags=creationflags,
    )
    lines: queue.Queue[str | None] = queue.Queue()
    errors: deque[str] = deque(maxlen=25)

    def read_output() -> None:
        try:
            assert process.stdout is not None
            for line in process.stdout:
                lines.put(line.rstrip())
        finally:
            lines.put(None)

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()
    started = control.clock()
    converted = 0.0
    try:
        while True:
            control.checkpoint()
            if control.clock() - started > timeout:
                raise TimeoutError("A conversão excedeu o tempo permitido.")
            try:
                line = lines.get(timeout=0.2)
            except queue.Empty:
                continue
            if line is None:
                break
            key, separator, value = line.partition("=")
            if key == "out_time_us":
                try:
                    converted = max(0.0, float(value) / 1_000_000)
                except ValueError:
                    pass
            elif key == "progress":
                elapsed = max(0.001, control.clock() - started)
                percent = min(99.9, converted * 100 / duration) if duration else None
                eta = max(0.0, duration - converted) * elapsed / converted if duration and converted else None
                progress({"percent": percent, "eta": eta, "elapsed": elapsed})
            elif not re.fullmatch(r"[a-z0-9_]+", key) or not separator:
                errors.append(line)
        return_code = process.wait()
        if return_code:
            raise RuntimeError(errors[-1] if errors else f"FFmpeg: código {return_code}")
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        reader.join(timeout=2)
        if process.stdout:
            process.stdout.close()
        control.release(process)
