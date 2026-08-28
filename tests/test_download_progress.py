from __future__ import annotations

from youtube_downloader_app import (
    PROGRESS_MARKER,
    format_bytes,
    format_duration,
    parse_ytdlp_progress,
    ProgressTracker,
    fragment_count,
)


def test_parses_exact_progress_values() -> None:
    progress = parse_ytdlp_progress(
        f"{PROGRESS_MARKER}52428800|104857600|NA|5242880|10| 50.0%",
        elapsed=20,
    )
    assert progress is not None
    assert progress["downloaded"] == 52_428_800
    assert progress["total"] == 104_857_600
    assert progress["total_is_estimate"] is False
    assert progress["speed"] == 5_242_880
    assert progress["average_speed"] == 2_621_440
    assert progress["eta"] == 10
    assert progress["percent"] == 50


def test_uses_estimated_total_when_exact_size_is_unknown() -> None:
    progress = parse_ytdlp_progress(
        f"{PROGRESS_MARKER}1048576|NA|10485760|NA|NA| 10.0%",
        elapsed=2,
    )
    assert progress is not None
    assert progress["total"] == 10_485_760
    assert progress["total_is_estimate"] is True
    assert progress["speed"] is None
    assert progress["average_speed"] == 524_288
    assert progress["percent"] == 10


def test_ignores_non_progress_output() -> None:
    assert parse_ytdlp_progress("[download] preparando", elapsed=1) is None


def test_formats_sizes_and_durations_for_the_ui() -> None:
    assert format_bytes(1_572_864) == "1,5 MB"
    assert format_duration(65) == "1min 05s"
    assert format_duration(3_661) == "1h 01min"


def test_estimates_hls_size_from_duration_and_bitrate():
    progress = parse_ytdlp_progress(
        f"{PROGRESS_MARKER}1000|NA|NA|NA|NA|NA|1|0|500|NA|120|800|downloading|file.mp4",
        elapsed=1,
    )
    assert progress["total"] == 12_000_000
    assert progress["total_is_estimate"]


def test_estimates_size_from_completed_fragments():
    progress = parse_ytdlp_progress(
        f"{PROGRESS_MARKER}2000|NA|NA|NA|NA|NA|1|2|10|NA|NA|NA|downloading|file.mp4",
        elapsed=1,
    )
    assert progress["total"] == 10_000
    assert progress["percent"] == 20


def test_average_excludes_resumed_bytes_and_resets_on_new_stream():
    tracker = ProgressTracker()
    def line(size, filename):
        return f"{PROGRESS_MARKER}{size}|100000|NA|500|1|NA|1|NA|NA|NA|NA|NA|downloading|{filename}"
    assert tracker.parse(line(50000, "video"), 10)["average_speed"] is None
    payload = tracker.parse(line(52000, "video"), 12)
    assert payload["average_speed"] == 1000
    assert payload["speed"] == 1000
    assert payload["eta"] == 48
    assert tracker.parse(line(1000, "audio"), 13)["average_speed"] is None


def test_fragment_count_is_bounded():
    for value in ("abc", "0", "999", None):
        assert fragment_count(value) == 4
    for value in (1, 2, 4, 8):
        assert fragment_count(str(value)) == value
