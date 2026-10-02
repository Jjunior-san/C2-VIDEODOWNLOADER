"""Public Spotify playlist, album, and track resolver without requiring developer credentials.

Extracts track details from Spotify links and optionally matches them to Deezer
for high-fidelity lossless/320kbps downloads, or provides structured entries for yt-dlp.
"""
from __future__ import annotations

import json
import re
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

import requests

from app_config import APP_VERSION
from deezer_catalog import DeezerTrack, search_deezer_tracks

SPOTIFY_PAGE_HOSTS = {
    "open.spotify.com",
    "play.spotify.com",
    "spotify.com",
    "spotify.link",
}
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/135.0.0.0 Safari/537.36 "
    f"C2VideoDownloader/{APP_VERSION}"
)

_HTTP_LOCAL = threading.local()


def _http_session() -> requests.Session:
    session = getattr(_HTTP_LOCAL, "session", None)
    if session is None:
        session = requests.Session()
        session.headers.update({
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
            "User-Agent": USER_AGENT,
        })
        _HTTP_LOCAL.session = session
    return session


class SpotifyCatalogError(RuntimeError):
    pass


@dataclass(frozen=True)
class SpotifyTrack:
    track_id: str
    title: str
    artist: str
    album: str = ""
    duration: int | None = None
    cover_url: str | None = None
    page_url: str = ""
    explicit: bool = False

    @property
    def display_title(self) -> str:
        return f"{self.artist} - {self.title}" if self.artist else self.title


@dataclass(frozen=True)
class SpotifyCollection:
    source_url: str
    title: str
    kind: str
    tracks: tuple[SpotifyTrack, ...]


def parse_spotify_url(url: str) -> tuple[str, str] | None:
    """Parse Spotify track, album, or playlist from URL or URI."""
    raw = str(url or "").strip()
    if not raw:
        return None

    if raw.startswith("spotify:"):
        parts = raw.split(":")
        if len(parts) >= 3 and parts[1] in {"track", "album", "playlist"}:
            return parts[1], parts[2]

    try:
        parsed = urlparse(raw)
        host = (parsed.hostname or "").lower()
        if host not in SPOTIFY_PAGE_HOSTS and not host.endswith(".spotify.com"):
            return None

        # Check embed path or normal path
        path_clean = parsed.path
        if path_clean.startswith("/embed/"):
            path_clean = path_clean[len("/embed"):]

        match = re.search(r"/(track|album|playlist)/([a-zA-Z0-9]+)(?:/|$)", path_clean, re.I)
        if match:
            return match.group(1).lower(), match.group(2)
    except Exception:
        pass
    return None


def is_spotify_url(url: str) -> bool:
    """Return True if the URL or URI represents a supported Spotify resource."""
    return parse_spotify_url(url) is not None


def fetch_spotify_oembed(url: str) -> dict:
    """Fetch basic info (title, thumbnail) via official public oEmbed."""
    clean_url = str(url or "").strip()
    oembed_url = f"https://open.spotify.com/oembed?url={quote(clean_url, safe='')}"
    try:
        req = Request(oembed_url, headers={"User-Agent": USER_AGENT})
        with urlopen(req, timeout=6) as response:
            return json.loads(response.read().decode("utf-8", errors="replace"))
    except Exception:
        return {}


def _resolve_with_ytdlp(url: str, engine_path: Path | None) -> SpotifyCollection | None:
    """Extract Spotify tracks using yt-dlp's native extractor if engine is available."""
    if not engine_path or not Path(engine_path).is_file():
        return None

    try:
        cmd = [
            str(engine_path),
            "--dump-single-json",
            "--flat-playlist",
            "--skip-download",
            "--no-warnings",
            "--socket-timeout", "8",
            url,
        ]
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=18,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            return None

        data = json.loads(proc.stdout)
        title = str(data.get("title") or data.get("playlist_title") or "Spotify").strip()
        tracks: list[SpotifyTrack] = []

        entries = data.get("entries")
        if entries and isinstance(entries, list):
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                track_id = str(entry.get("id") or "")
                track_title = str(entry.get("title") or "").strip()
                artist = str(entry.get("artist") or entry.get("uploader") or entry.get("creator") or "").strip()
                if " - " in track_title and not artist:
                    parts = track_title.split(" - ", 1)
                    artist, track_title = parts[0].strip(), parts[1].strip()
                album = str(entry.get("album") or "").strip()
                duration = entry.get("duration")
                if duration is not None:
                    try:
                        duration = int(duration)
                    except (ValueError, TypeError):
                        duration = None
                cover = str(entry.get("thumbnail") or "")
                page_url = str(entry.get("url") or entry.get("webpage_url") or f"https://open.spotify.com/track/{track_id}")
                is_explicit = bool(entry.get("explicit") or entry.get("is_explicit"))
                if track_title:
                    tracks.append(SpotifyTrack(
                        track_id=track_id,
                        title=track_title,
                        artist=artist,
                        album=album,
                        duration=duration,
                        cover_url=cover or None,
                        page_url=page_url,
                        explicit=is_explicit,
                    ))
        elif data.get("title"):
            track_id = str(data.get("id") or "")
            track_title = str(data.get("title") or "").strip()
            artist = str(data.get("artist") or data.get("uploader") or "").strip()
            if " - " in track_title and not artist:
                parts = track_title.split(" - ", 1)
                artist, track_title = parts[0].strip(), parts[1].strip()
            is_explicit = bool(data.get("explicit") or data.get("is_explicit"))
            tracks.append(SpotifyTrack(
                track_id=track_id,
                title=track_title,
                artist=artist,
                album=str(data.get("album") or "").strip(),
                duration=int(data.get("duration")) if data.get("duration") else None,
                cover_url=str(data.get("thumbnail") or "") or None,
                page_url=str(data.get("webpage_url") or url),
                explicit=is_explicit,
            ))

        if tracks:
            kind, _ = parse_spotify_url(url) or ("playlist", "")
            return SpotifyCollection(url, title, kind, tuple(tracks))
    except Exception:
        pass
    return None


def _resolve_with_embed(url: str, kind: str, identifier: str) -> SpotifyCollection:
    """Scrape the Spotify embed page to extract track listing without credentials."""
    embed_url = f"https://open.spotify.com/embed/{kind}/{identifier}"
    sess = _http_session()
    resp = sess.get(embed_url, timeout=8)
    resp.raise_for_status()
    html = resp.text

    # Extract __NEXT_DATA__ JSON payload
    match = re.search(r'<script\s+id="__NEXT_DATA__"\s+type="application/json">\s*({.*?})\s*</script>', html, re.DOTALL)
    if not match:
        # Fallback to oEmbed if script tag not found
        oembed = fetch_spotify_oembed(url)
        title = oembed.get("title") or f"Spotify {kind.title()}"
        if kind == "track":
            track_title = oembed.get("title") or "Faixa Spotify"
            return SpotifyCollection(
                url,
                title,
                kind,
                (SpotifyTrack(identifier, track_title, "", cover_url=oembed.get("thumbnail_url"), page_url=url),),
            )
        raise SpotifyCatalogError(f"Não foi possível extrair a lista de faixas do Spotify ({kind}).")

    try:
        payload = json.loads(match.group(1))
        entity = payload.get("props", {}).get("pageProps", {}).get("state", {}).get("data", {}).get("entity", {})
    except Exception as exc:
        raise SpotifyCatalogError("A resposta do Spotify não pôde ser decodificada.") from exc

    title = str(entity.get("title") or entity.get("name") or f"Spotify {kind.title()}").strip()
    tracks: list[SpotifyTrack] = []

    track_list = entity.get("trackList") or []
    if not track_list and entity.get("type") == "track":
        track_list = [entity]

    for item in track_list:
        if not isinstance(item, dict):
            continue
        track_title = str(item.get("title") or item.get("name") or "").strip()
        subtitle = str(item.get("subtitle") or "").strip()
        artist_obj = item.get("artists") or []
        if isinstance(artist_obj, list) and artist_obj:
            artist = ", ".join(str(a.get("name", "")) for a in artist_obj if isinstance(a, dict))
        else:
            artist = subtitle

        track_id = str(item.get("id") or item.get("uri") or "").split(":")[-1]
        duration_ms = item.get("duration") or item.get("duration_ms")
        duration = int(duration_ms) // 1000 if duration_ms else None
        is_explicit = bool(item.get("isExplicit") or item.get("explicit"))
        tracks.append(SpotifyTrack(
            track_id=track_id or identifier,
            title=track_title,
            artist=artist,
            duration=duration,
            page_url=f"https://open.spotify.com/track/{track_id}" if track_id else url,
            explicit=is_explicit,
        ))

    if not tracks:
        raise SpotifyCatalogError("Nenhuma faixa foi encontrada no link do Spotify.")

    return SpotifyCollection(url, title, kind, tuple(tracks))


def resolve_spotify_url(url: str, engine_path: Path | None = None) -> SpotifyCollection:
    """Resolve a Spotify track, album, or playlist URL into structured SpotifyCollection."""
    parsed = parse_spotify_url(url)
    if not parsed:
        raise SpotifyCatalogError("O endereço informado não é um link válido do Spotify.")
    kind, identifier = parsed

    # 1. Try yt-dlp first if available (fast and extracts all metadata)
    if engine_path:
        ytdlp_res = _resolve_with_ytdlp(url, engine_path)
        if ytdlp_res and ytdlp_res.tracks:
            return ytdlp_res

    # 2. Try direct embed scraping
    try:
        return _resolve_with_embed(url, kind, identifier)
    except Exception as exc:
        # 3. Last attempt with yt-dlp without strict validation if not attempted before
        if not engine_path:
            raise exc
        raise SpotifyCatalogError(f"Falha ao consultar o link do Spotify: {exc}") from exc


def match_spotify_track_to_deezer(track: SpotifyTrack) -> DeezerTrack | None:
    """Search Deezer for a matching track to enable lossless/320kbps download."""
    query = f"{track.artist} {track.title}".strip() if track.artist else track.title.strip()
    if len(query) < 2:
        return None
    try:
        candidates = search_deezer_tracks(query, limit=5)
        if not candidates:
            return None

        # Exact or close match comparison
        clean_track = re.sub(r"[^\w\s]", "", track.title.lower()).strip()
        matched = None
        for cand in candidates:
            clean_cand = re.sub(r"[^\w\s]", "", cand.title.lower()).strip()
            if clean_track in clean_cand or clean_cand in clean_track:
                # If Spotify track is explicit, prioritize candidate that is also explicit!
                if track.explicit and cand.explicit:
                    return cand
                if matched is None:
                    matched = cand
        if matched is not None:
            if track.explicit and not matched.explicit:
                from dataclasses import replace
                return replace(matched, explicit=True)
            return matched
        # Fallback to the top candidate if reasonably confident
        top = candidates[0]
        if track.explicit and not top.explicit:
            from dataclasses import replace
            return replace(top, explicit=True)
        return top
    except Exception:
        return None
