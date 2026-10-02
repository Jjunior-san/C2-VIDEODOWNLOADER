"""Synced lyrics service integrating with the free, public LRCLIB API.

Provides Apple Music-style time-synchronized lyrics (karaoke format)
and plain text lyrics without requiring any API keys or registration.
"""
from __future__ import annotations

import json
import re
import urllib.parse
from pathlib import Path
from urllib.request import Request, urlopen


LRCLIB_API_URL = "https://lrclib.net/api/get"
DEFAULT_USER_AGENT = "C2VideoDownloader/2.3.0 (https://github.com/Jjunior-san/C2-VIDEODOWNLOADER)"

_LYRICS_CACHE: dict[str, dict] = {}


def _clean_string(value: str) -> str:
    cleaned = re.sub(r"[\(\[\{].*?[\)\]\}]", "", value)
    cleaned = re.sub(r"(?i)\b(feat|ft|official|audio|video|lyrics|hd|4k)\b.*", "", cleaned)
    return cleaned.strip(" -_")


def parse_lrc(lrc_text: str) -> list[tuple[int, str]]:
    """Parse LRC format string into a list of (timestamp_ms, line_text) sorted chronologically."""
    if not lrc_text or not lrc_text.strip():
        return []

    pattern = re.compile(r"\[(\d{2}):(\d{2})(?:\.(\d{2,3}))?\]")
    parsed_lines: list[tuple[int, str]] = []

    for raw_line in lrc_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        matches = list(pattern.finditer(line))
        if not matches:
            continue
        text = pattern.sub("", line).strip()
        for m in matches:
            mins = int(m.group(1))
            secs = int(m.group(2))
            millis_str = m.group(3) or "0"
            if len(millis_str) == 2:
                millis = int(millis_str) * 10
            else:
                millis = int(millis_str[:3])
            time_ms = (mins * 60 + secs) * 1000 + millis
            parsed_lines.append((time_ms, text))

    parsed_lines.sort(key=lambda item: item[0])
    return parsed_lines


def fetch_lyrics(
    track_name: str,
    artist_name: str = "",
    album_name: str = "",
    duration: float = 0.0,
    timeout: float = 8.0,
) -> dict:
    """Fetch synced and plain lyrics from LRCLIB.

    Returns a dict:
    {
        "found": bool,
        "track": str,
        "artist": str,
        "synced": list[tuple[int, str]],  # (ms, text)
        "plain": str,
        "raw_lrc": str,
    }
    """
    clean_track = _clean_string(track_name) or track_name
    clean_artist = _clean_string(artist_name) or artist_name

    cache_key = f"{clean_artist.lower()}::{clean_track.lower()}"
    if cache_key in _LYRICS_CACHE:
        return _LYRICS_CACHE[cache_key]

    params = {"track_name": clean_track}
    if clean_artist:
        params["artist_name"] = clean_artist
    if album_name:
        params["album_name"] = album_name
    if duration and duration > 0:
        params["duration"] = str(int(duration))

    query_str = urllib.parse.urlencode(params)
    url = f"{LRCLIB_API_URL}?{query_str}"

    req = Request(url, headers={"User-Agent": DEFAULT_USER_AGENT})
    result = {
        "found": False,
        "track": track_name,
        "artist": artist_name,
        "synced": [],
        "plain": "",
        "raw_lrc": "",
    }

    try:
        with urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            raw_lrc = data.get("syncedLyrics") or ""
            plain = data.get("plainLyrics") or ""
            synced = parse_lrc(raw_lrc) if raw_lrc else []

            result = {
                "found": bool(synced or plain),
                "track": data.get("trackName") or track_name,
                "artist": data.get("artistName") or artist_name,
                "synced": synced,
                "plain": plain,
                "raw_lrc": raw_lrc,
            }
    except Exception:
        # Fallback: if search with artist failed or timeout, try with just track name
        if clean_artist and not result["found"]:
            try:
                fallback_url = f"{LRCLIB_API_URL}?{urllib.parse.urlencode({'track_name': clean_track})}"
                req = Request(fallback_url, headers={"User-Agent": DEFAULT_USER_AGENT})
                with urlopen(req, timeout=timeout / 2) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    raw_lrc = data.get("syncedLyrics") or ""
                    plain = data.get("plainLyrics") or ""
                    synced = parse_lrc(raw_lrc) if raw_lrc else []
                    result = {
                        "found": bool(synced or plain),
                        "track": data.get("trackName") or track_name,
                        "artist": data.get("artistName") or artist_name,
                        "synced": synced,
                        "plain": plain,
                        "raw_lrc": raw_lrc,
                    }
            except Exception:
                pass

    _LYRICS_CACHE[cache_key] = result
    return result


def save_lrc_file(target_audio_path: Path, raw_lrc: str) -> Path | None:
    """Save raw LRC lyrics alongside an audio file with matching stem."""
    if not raw_lrc or not raw_lrc.strip():
        return None
    try:
        path = Path(target_audio_path)
        lrc_path = path.with_suffix(".lrc")
        lrc_path.write_text(raw_lrc, encoding="utf-8")
        return lrc_path
    except Exception:
        return None


def get_active_lyric_index(synced_lyrics: list[tuple[int, str]], current_ms: int) -> int:
    """Given parsed synced lyrics and current playback position in ms, return the index of the active line."""
    if not synced_lyrics:
        return -1

    active_idx = -1
    for idx, (timestamp_ms, _) in enumerate(synced_lyrics):
        if current_ms >= timestamp_ms:
            active_idx = idx
        else:
            break
    return active_idx
