"""Public Deezer catalog integration.

Only metadata and the official preview URL exposed by the public catalog are
used.  This module never accepts account cookies or requests protected media.
"""
from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass
from urllib.parse import quote, urlparse

import requests

from app_config import APP_VERSION


API_ROOT = "https://api.deezer.com"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/153.0.0.0 Safari/537.36 "
    f"C2VideoDownloader/{APP_VERSION}"
)
DEEZER_PAGE_HOSTS = {"deezer.com", "www.deezer.com"}
MAX_RESPONSE_BYTES = 10 * 1024 * 1024
MAX_TRACKS = 1000
MAX_SEARCH_RESULTS = 50
_HTTP_LOCAL = threading.local()


def _http_session() -> requests.Session:
    """Reuse HTTP connections inside the persistent catalog workers."""
    session = getattr(_HTTP_LOCAL, "session", None)
    if session is None:
        session = requests.Session()
        session.headers.update({
            "Accept": "application/json,text/plain,*/*",
            "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
            "User-Agent": USER_AGENT,
        })
        _HTTP_LOCAL.session = session
    return session


class DeezerCatalogError(RuntimeError):
    pass


@dataclass(frozen=True)
class DeezerTrack:
    track_id: str
    title: str
    artist: str
    album: str
    preview_url: str | None
    cover_url: str | None = None
    track_number: int | None = None
    disc_number: int | None = None
    release_year: str | None = None
    duration: int | None = None

    @property
    def page_url(self) -> str:
        return f"https://www.deezer.com/track/{self.track_id}"

    @property
    def display_title(self) -> str:
        return f"{self.artist} - {self.title}" if self.artist else self.title


@dataclass(frozen=True)
class DeezerSearchResult:
    kind: str
    item_id: str
    title: str
    subtitle: str
    cover_url: str | None
    page_url: str

    @property
    def kind_label(self) -> str:
        return {
            "track": "Música",
            "artist": "Artista",
            "album": "Álbum",
            "playlist": "Playlist",
        }.get(self.kind, self.kind.title())


@dataclass(frozen=True)
class DeezerCollection:
    source_url: str
    title: str
    kind: str
    tracks: tuple[DeezerTrack, ...]


def parse_deezer_url(url: str) -> tuple[str, str] | None:
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or host not in DEEZER_PAGE_HOSTS:
        return None
    match = re.search(r"/(?:[a-z]{2}(?:-[a-z]{2})?/)?(track|artist|album|playlist)/(\d+)(?:/|$)", parsed.path, re.I)
    if not match:
        return None
    return match.group(1).lower(), match.group(2)


def is_deezer_url(url: str) -> bool:
    return parse_deezer_url(url) is not None


def _api_json(url: str) -> dict:
    parsed = urlparse(url)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() != "api.deezer.com":
        raise DeezerCatalogError("A consulta tentou acessar um endereço não autorizado.")

    try:
        response = _http_session().get(
            url,
            timeout=(5, 12),
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        detail = f" HTTP {status}." if status else ""
        raise DeezerCatalogError(
            f"Não foi possível consultar o catálogo da Deezer.{detail} {exc}"
        ) from exc

    raw = response.content
    if len(raw) > MAX_RESPONSE_BYTES:
        raise DeezerCatalogError("A resposta do catálogo excedeu o limite permitido.")
    try:
        payload = response.json()
    except (ValueError, json.JSONDecodeError) as exc:
        raise DeezerCatalogError("A Deezer retornou uma resposta inválida.") from exc
    if not isinstance(payload, dict):
        raise DeezerCatalogError("A Deezer retornou dados em formato inesperado.")
    if isinstance(payload.get("error"), dict):
        message = payload["error"].get("message") or "item não encontrado"
        raise DeezerCatalogError(f"Erro do catálogo da Deezer: {message}")
    return payload


def _positive_int(value) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _track_from_payload(payload: dict, *, album_fallback: dict | None = None) -> DeezerTrack:
    album = payload.get("album") if isinstance(payload.get("album"), dict) else {}
    fallback = album_fallback or {}
    artist = payload.get("artist") if isinstance(payload.get("artist"), dict) else {}
    track_id = str(payload.get("id") or "").strip()
    title = str(payload.get("title") or payload.get("title_short") or "").strip()
    if not track_id or not title:
        raise DeezerCatalogError("Uma faixa retornada pelo catálogo não possui identificação.")
    release_date = str(payload.get("release_date") or fallback.get("release_date") or "")
    preview = str(payload.get("preview") or "").strip() or None
    if preview:
        preview_host = (urlparse(preview).hostname or "").lower()
        if not (preview.startswith("https://") and preview_host.endswith("dzcdn.net")):
            preview = None
    cover = str(album.get("cover_big") or fallback.get("cover_big") or "").strip() or None
    return DeezerTrack(
        track_id=track_id,
        title=title,
        artist=str(artist.get("name") or "").strip(),
        album=str(album.get("title") or fallback.get("title") or "").strip(),
        preview_url=preview,
        cover_url=cover,
        track_number=_positive_int(payload.get("track_position")),
        disc_number=_positive_int(payload.get("disk_number")),
        release_year=release_date[:4] if re.fullmatch(r"\d{4}", release_date[:4]) else None,
        duration=_positive_int(payload.get("duration")),
    )


def resolve_deezer_track(track_id: str) -> DeezerTrack:
    if not str(track_id).isdigit():
        raise DeezerCatalogError("Identificador de faixa inválido.")
    return _track_from_payload(_api_json(f"{API_ROOT}/track/{track_id}"))


def _paged_tracks(first_page: dict, *, album_fallback: dict | None = None) -> tuple[DeezerTrack, ...]:
    tracks: list[DeezerTrack] = []
    page = first_page
    while True:
        entries = page.get("data")
        if not isinstance(entries, list):
            raise DeezerCatalogError("A lista de faixas retornada pela Deezer é inválida.")
        for entry in entries:
            if isinstance(entry, dict):
                tracks.append(_track_from_payload(entry, album_fallback=album_fallback))
                if len(tracks) >= MAX_TRACKS:
                    return tuple(tracks)
        next_url = str(page.get("next") or "").strip()
        if not next_url:
            return tuple(tracks)
        page = _api_json(next_url)



def _catalog_cover(payload: dict, *keys: str) -> str | None:
    for key in keys:
        value = str(payload.get(key) or "").strip()
        if not value:
            continue
        parsed = urlparse(value)
        if parsed.scheme == "https" and (parsed.hostname or "").lower().endswith("dzcdn.net"):
            return value
    return None


def search_deezer_catalog(
    query: str,
    kind: str = "track",
    limit: int = 20,
) -> tuple[DeezerSearchResult, ...]:
    """Search tracks, artists, albums or playlists in the public Deezer catalog."""
    search = str(query or "").strip()
    if len(search) < 2:
        return ()

    kind = str(kind or "track").lower()
    if kind not in {"track", "artist", "album", "playlist"}:
        raise DeezerCatalogError("Tipo de pesquisa Deezer inválido.")
    try:
        requested = int(limit)
    except (TypeError, ValueError):
        requested = 20
    requested = max(1, min(MAX_SEARCH_RESULTS, requested))

    endpoint = "search" if kind == "track" else f"search/{kind}"
    payload = _api_json(f"{API_ROOT}/{endpoint}?q={quote(search, safe='')}&limit={requested}")
    entries = payload.get("data")
    if not isinstance(entries, list):
        raise DeezerCatalogError("A Deezer retornou uma pesquisa em formato inesperado.")

    results: list[DeezerSearchResult] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        item_id = str(entry.get("id") or "").strip()
        if not item_id or item_id in seen:
            continue
        seen.add(item_id)

        if kind == "track":
            title = str(entry.get("title") or entry.get("title_short") or "").strip()
            artist = entry.get("artist") if isinstance(entry.get("artist"), dict) else {}
            album = entry.get("album") if isinstance(entry.get("album"), dict) else {}
            artist_name = str(artist.get("name") or "").strip()
            album_name = str(album.get("title") or "").strip()
            subtitle = " • ".join(value for value in (artist_name, album_name) if value)
            cover = _catalog_cover(album, "cover_medium", "cover_big", "cover")
        elif kind == "artist":
            title = str(entry.get("name") or "").strip()
            subtitle = "Artista"
            cover = _catalog_cover(entry, "picture_medium", "picture_big", "picture")
        elif kind == "album":
            title = str(entry.get("title") or "").strip()
            artist = entry.get("artist") if isinstance(entry.get("artist"), dict) else {}
            subtitle = str(artist.get("name") or "").strip() or "Álbum"
            cover = _catalog_cover(entry, "cover_medium", "cover_big", "cover")
        else:
            title = str(entry.get("title") or "").strip()
            user = entry.get("user") if isinstance(entry.get("user"), dict) else {}
            creator = str(user.get("name") or "").strip()
            subtitle = f"Por {creator}" if creator else "Playlist"
            cover = _catalog_cover(entry, "picture_medium", "picture_big", "picture")

        if not title:
            continue
        results.append(DeezerSearchResult(
            kind=kind,
            item_id=item_id,
            title=title,
            subtitle=subtitle,
            cover_url=cover,
            page_url=f"https://www.deezer.com/{kind}/{item_id}",
        ))
        if len(results) >= requested:
            break
    return tuple(results)


def search_deezer_tracks(query: str, limit: int = 25) -> tuple[DeezerTrack, ...]:
    """Search the public Deezer catalog and return stable track metadata."""
    search = str(query or "").strip()
    if len(search) < 2:
        raise DeezerCatalogError("Informe pelo menos dois caracteres para pesquisar na Deezer.")
    try:
        requested = int(limit)
    except (TypeError, ValueError):
        requested = 25
    requested = max(1, min(MAX_SEARCH_RESULTS, requested))

    payload = _api_json(f"{API_ROOT}/search?q={quote(search, safe='')}&limit={requested}")
    entries = payload.get("data")
    if not isinstance(entries, list):
        raise DeezerCatalogError("A Deezer retornou uma pesquisa em formato inesperado.")

    tracks: list[DeezerTrack] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        try:
            track = _track_from_payload(entry)
        except DeezerCatalogError:
            continue
        if track.track_id in seen:
            continue
        seen.add(track.track_id)
        tracks.append(track)
        if len(tracks) >= requested:
            break
    return tuple(tracks)

def resolve_deezer_url(url: str) -> DeezerCollection:
    parsed = parse_deezer_url(url)
    if not parsed:
        raise DeezerCatalogError("O endereço da Deezer não é uma faixa, álbum ou playlist reconhecida.")
    kind, identifier = parsed
    if kind == "track":
        track = resolve_deezer_track(identifier)
        return DeezerCollection(url, track.display_title, kind, (track,))

    payload = _api_json(f"{API_ROOT}/{kind}/{identifier}")
    title = str(
        payload.get("title")
        or payload.get("name")
        or f"{kind.title()} {identifier}"
    ).strip()

    if kind == "artist":
        tracks_page = _api_json(f"{API_ROOT}/artist/{identifier}/top?limit=50")
        fallback = None
    else:
        tracks_page = payload.get("tracks")
        fallback = payload if kind == "album" else None

    if not isinstance(tracks_page, dict):
        raise DeezerCatalogError("A Deezer não informou as faixas desta coleção.")
    tracks = _paged_tracks(tracks_page, album_fallback=fallback)
    if not tracks:
        raise DeezerCatalogError("Nenhuma faixa foi encontrada nesta coleção.")
    return DeezerCollection(url, title, kind, tracks)
