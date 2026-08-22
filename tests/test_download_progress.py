from __future__ import annotations

from youtube_downloader_app import (
    PROGRESS_MARKER,
    format_bytes,
    format_duration,
    parse_ytdlp_progress,
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
