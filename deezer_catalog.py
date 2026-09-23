"""Public Deezer catalog integration.

Only metadata and the official preview URL exposed by the public catalog are
used.  This module never accepts account cookies or requests protected media.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from app_config import APP_VERSION


API_ROOT = "https://api.deezer.com"
USER_AGENT = f"C2VideoDownloader/{APP_VERSION} (+https://github.com/Jjunior-san/C2-VIDEODOWNLOADER)"
DEEZER_PAGE_HOSTS = {"deezer.com", "www.deezer.com"}
MAX_RESPONSE_BYTES = 10 * 1024 * 1024
MAX_TRACKS = 1000


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
    match = re.search(r"/(?:[a-z]{2}(?:-[a-z]{2})?/)?(track|album|playlist)/(\d+)(?:/|$)", parsed.path, re.I)
    if not match:
        return None
    return match.group(1).lower(), match.group(2)


def is_deezer_url(url: str) -> bool:
    return parse_deezer_url(url) is not None


def _api_json(url: str) -> dict:
    parsed = urlparse(url)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() != "api.deezer.com":
        raise DeezerCatalogError("A consulta tentou acessar um endereço não autorizado.")
    request = Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=20) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except OSError as exc:
        raise DeezerCatalogError(f"Não foi possível consultar o catálogo da Deezer: {exc}") from exc
    if len(raw) > MAX_RESPONSE_BYTES:
        raise DeezerCatalogError("A resposta do catálogo excedeu o limite permitido.")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
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


def resolve_deezer_url(url: str) -> DeezerCollection:
    parsed = parse_deezer_url(url)
    if not parsed:
        raise DeezerCatalogError("O endereço da Deezer não é uma faixa, álbum ou playlist reconhecida.")
    kind, identifier = parsed
    if kind == "track":
        track = resolve_deezer_track(identifier)
        return DeezerCollection(url, track.display_title, kind, (track,))

    payload = _api_json(f"{API_ROOT}/{kind}/{identifier}")
    title = str(payload.get("title") or f"{kind.title()} {identifier}").strip()
    tracks_page = payload.get("tracks")
    if not isinstance(tracks_page, dict):
        raise DeezerCatalogError("A Deezer não informou as faixas desta coleção.")
    fallback = payload if kind == "album" else None
    tracks = _paged_tracks(tracks_page, album_fallback=fallback)
    if not tracks:
        raise DeezerCatalogError("Nenhuma faixa foi encontrada nesta coleção.")
    return DeezerCollection(url, title, kind, tracks)
