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
DEEZER_PAGE_HOSTS = {"deezer.com", "www.deezer.com", "link.deezer.com", "deezer.page.link"}
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
    album_id: str | None = None

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
    album_id: str | None = None
    album_title: str | None = None

    @property
    def kind_label(self) -> str:
        return {
            "track": "Música",
            "artist": "Artista",
            "album": "Álbum",
            "playlist": "Playlist",
            "loved": "Favoritos",
        }.get(self.kind, self.kind.title())


@dataclass(frozen=True)
class DeezerCollection:
    source_url: str
    title: str
    kind: str
    tracks: tuple[DeezerTrack, ...]


def resolve_deezer_shortlink(url: str, timeout: float = 6.0) -> str:
    """Resolve link.deezer.com and deezer.page.link shortlinks to canonical web URLs."""
    raw = str(url or "").strip()
    try:
        parsed = urlparse(raw)
        host = (parsed.hostname or "").lower()
        if host in {"link.deezer.com", "deezer.page.link"}:
            sess = _http_session()
            resp = sess.head(raw, allow_redirects=True, timeout=timeout)
            if resp.url and resp.url != raw:
                return resp.url
    except Exception:
        pass
    return raw


def parse_deezer_url(url: str) -> tuple[str, str] | None:
    raw = str(url or "").strip()
    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or host not in DEEZER_PAGE_HOSTS:
        return None

    if host in {"link.deezer.com", "deezer.page.link"}:
        resolved = resolve_deezer_shortlink(raw)
        if resolved != raw:
            return parse_deezer_url(resolved)

    match = re.search(r"/(?:[a-z]{2}(?:-[a-z]{2})?/)?(track|artist|album|playlist)/(\d+)(?:/|$)", parsed.path, re.I)
    if match:
        return match.group(1).lower(), match.group(2)

    match_loved = re.search(r"/(?:[a-z]{2}(?:-[a-z]{2})?/)?(?:profile|user)/(\d+)(?:/(?:loved|tracks))?(?:/|$)", parsed.path, re.I)
    if match_loved:
        return "loved", match_loved.group(1)

    return None


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
    raw_album_id = str(album.get("id") or fallback.get("id") or "").strip()
    album_id = raw_album_id if raw_album_id.isdigit() else None
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
        album_id=album_id,
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

        album_id: str | None = None
        album_title: str | None = None

        if kind == "track":
            title = str(entry.get("title") or entry.get("title_short") or "").strip()
            artist = entry.get("artist") if isinstance(entry.get("artist"), dict) else {}
            album = entry.get("album") if isinstance(entry.get("album"), dict) else {}
            artist_name = str(artist.get("name") or "").strip()
            album_name = str(album.get("title") or "").strip()
            raw_alb_id = str(album.get("id") or "").strip()
            album_id = raw_alb_id if raw_alb_id.isdigit() else None
            album_title = album_name or None
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
            album_id = item_id
            album_title = title
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
            album_id=album_id,
            album_title=album_title,
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
    resolved_url = resolve_deezer_shortlink(url)
    parsed = parse_deezer_url(resolved_url)
    if not parsed:
        raise DeezerCatalogError("O endereço da Deezer não é uma faixa, álbum ou playlist reconhecida.")
    kind, identifier = parsed
    if kind == "track":
        track = resolve_deezer_track(identifier)
        return DeezerCollection(resolved_url, track.display_title, kind, (track,))

    if kind == "loved":
        try:
            user_data = _api_json(f"{API_ROOT}/user/{identifier}")
            user_name = user_data.get("name") or identifier
        except Exception:
            user_name = identifier
        title = f"Músicas Favoritas de {user_name}"
        tracks_page = _api_json(f"{API_ROOT}/user/{identifier}/tracks?limit=100")
        if not isinstance(tracks_page, dict):
            raise DeezerCatalogError("A Deezer não informou as faixas deste usuário.")
        tracks = _paged_tracks(tracks_page)
        if not tracks:
            raise DeezerCatalogError("Nenhuma faixa favorita encontrada para este usuário.")
        return DeezerCollection(resolved_url, title, "loved", tracks)

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
    return DeezerCollection(resolved_url, title, kind, tracks)


def get_artist_top_tracks(artist_id: str, limit: int = 30) -> tuple[DeezerTrack, ...]:
    """Fetch top tracks for an artist."""
    if not str(artist_id).isdigit():
        raise DeezerCatalogError("Identificador de artista inválido.")
    payload = _api_json(f"{API_ROOT}/artist/{artist_id}/top?limit={min(100, max(1, limit))}")
    return _paged_tracks(payload)


def get_artist_albums(artist_id: str, limit: int = 50) -> tuple[DeezerSearchResult, ...]:
    """Fetch albums by an artist as search results."""
    if not str(artist_id).isdigit():
        raise DeezerCatalogError("Identificador de artista inválido.")
    payload = _api_json(f"{API_ROOT}/artist/{artist_id}/albums?limit={min(100, max(1, limit))}")
    entries = payload.get("data")
    if not isinstance(entries, list):
        return ()
    results: list[DeezerSearchResult] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        item_id = str(entry.get("id") or "").strip()
        title = str(entry.get("title") or "").strip()
        if not item_id or not title:
            continue
        year = str(entry.get("release_date") or "")[:4]
        subtitle = f"Álbum • {year}" if year else "Álbum"
        cover = _catalog_cover(entry, "cover_medium", "cover_big", "cover")
        results.append(DeezerSearchResult(
            kind="album",
            item_id=item_id,
            title=title,
            subtitle=subtitle,
            cover_url=cover,
            page_url=f"https://www.deezer.com/album/{item_id}",
            album_id=item_id,
            album_title=title,
        ))
    return tuple(results)


def get_album_tracks(album_id: str) -> tuple[DeezerTrack, ...]:
    """Fetch all tracks for a specific album."""
    if not str(album_id).isdigit():
        raise DeezerCatalogError("Identificador de álbum inválido.")
    payload = _api_json(f"{API_ROOT}/album/{album_id}")
    tracks_page = payload.get("tracks")
    if not isinstance(tracks_page, dict):
        raise DeezerCatalogError("A Deezer não informou as faixas deste álbum.")
    return _paged_tracks(tracks_page, album_fallback=payload)


def get_track_album_info(track_id: str) -> tuple[str, str] | None:
    """Resolve a track and return (album_id, album_title), or None if unavailable."""
    try:
        track = resolve_deezer_track(str(track_id).strip())
        if track.album_id:
            return track.album_id, track.album
    except Exception:
        pass
    return None


def get_deezer_top_brasil(limit: int = 50) -> tuple[DeezerTrack, ...]:
    """Fetch the official Deezer Top Brasil tracks."""
    limit = min(100, max(1, int(limit)))
    try:
        payload = _api_json(f"{API_ROOT}/playlist/1111141961/tracks?limit={limit}")
        tracks = _paged_tracks(payload)
        if tracks:
            return tracks[:limit]
    except Exception:
        pass
    # Fallback to general charts if playlist is temporarily unavailable
    payload = _api_json(f"{API_ROOT}/chart/0/tracks?limit={limit}")
    return _paged_tracks(payload)[:limit]


def get_deezer_top_global(limit: int = 50) -> tuple[DeezerTrack, ...]:
    """Fetch the official Deezer Global Top tracks."""
    limit = min(100, max(1, int(limit)))
    payload = _api_json(f"{API_ROOT}/chart/0/tracks?limit={limit}")
    return _paged_tracks(payload)[:limit]


def get_deezer_user_favorites(arl: str, limit: int = 100) -> tuple[DeezerTrack, ...]:
    """Fetch user's loved tracks using their authenticated ARL session."""
    clean_arl = str(arl or "").strip()
    if not clean_arl:
        raise DeezerCatalogError("Nenhum cookie ARL configurado para carregar favoritos.")
    try:
        from deezer_auth import validate_deezer_arl
        account = validate_deezer_arl(clean_arl)
        if not account.get("valid") or not account.get("user_id"):
            raise DeezerCatalogError(account.get("error") or "ARL inválido ou não autenticado.")
        user_id = account["user_id"]
    except Exception as exc:
        raise DeezerCatalogError(f"Falha ao validar conta Deezer: {exc}") from exc

    limit = min(300, max(1, int(limit)))
    payload = _api_json(f"{API_ROOT}/user/{user_id}/tracks?limit={limit}")
    tracks = _paged_tracks(payload)
    if not tracks:
        raise DeezerCatalogError("Nenhuma música favorita encontrada nesta conta.")
    return tracks[:limit]


def analyze_deezer_metadata(url_or_id: str) -> dict:
    """Analyze Deezer URL or ID and return complete metadata (like deemix Link Analyzer)."""
    raw = str(url_or_id or "").strip()
    parsed = parse_deezer_url(raw)
    if parsed:
        kind, item_id = parsed
    elif raw.isdigit():
        kind, item_id = "track", raw
    else:
        raise DeezerCatalogError("Endereço ou identificador Deezer não reconhecido.")

    result: dict = {
        "kind": kind,
        "id": item_id,
        "title": "",
        "artist": "",
        "album": "",
        "cover_url": None,
        "isrc": None,
        "upc": None,
        "duration": None,
        "duration_formatted": "—",
        "bpm": None,
        "release_date": None,
        "label": None,
        "genres": None,
        "track_position": None,
        "disk_number": None,
        "explicit": False,
        "readable": None,
        "available_countries_count": None,
        "page_url": f"https://www.deezer.com/{kind}/{item_id}",
    }

    if kind == "track":
        track_data = _api_json(f"{API_ROOT}/track/{item_id}")
        result["title"] = str(track_data.get("title") or track_data.get("title_short") or "")
        artist_obj = track_data.get("artist") if isinstance(track_data.get("artist"), dict) else {}
        album_obj = track_data.get("album") if isinstance(track_data.get("album"), dict) else {}
        result["artist"] = str(artist_obj.get("name") or "")
        result["album"] = str(album_obj.get("title") or "")
        result["cover_url"] = _catalog_cover(album_obj, "cover_xl", "cover_big", "cover_medium")
        result["isrc"] = track_data.get("isrc")
        dur = _positive_int(track_data.get("duration"))
        if dur:
            result["duration"] = dur
            result["duration_formatted"] = f"{dur // 60}:{dur % 60:02d}"
        result["bpm"] = track_data.get("bpm")
        result["release_date"] = track_data.get("release_date")
        result["track_position"] = track_data.get("track_position")
        result["disk_number"] = track_data.get("disk_number")
        result["explicit"] = bool(track_data.get("explicit_lyrics"))
        result["readable"] = track_data.get("readable")
        countries = track_data.get("available_countries")
        if isinstance(countries, list):
            result["available_countries_count"] = len(countries)

        # Retrieve UPC and label from album if album_id is available
        alb_id = album_obj.get("id")
        if alb_id and str(alb_id).isdigit():
            try:
                alb_data = _api_json(f"{API_ROOT}/album/{alb_id}")
                result["upc"] = alb_data.get("upc")
                result["label"] = alb_data.get("label")
                genres_obj = alb_data.get("genres", {}).get("data", [])
                if isinstance(genres_obj, list) and genres_obj:
                    result["genres"] = ", ".join(g.get("name") for g in genres_obj if g.get("name"))
            except Exception:
                pass
    elif kind == "album":
        alb_data = _api_json(f"{API_ROOT}/album/{item_id}")
        result["title"] = str(alb_data.get("title") or "")
        artist_obj = alb_data.get("artist") if isinstance(alb_data.get("artist"), dict) else {}
        result["artist"] = str(artist_obj.get("name") or "")
        result["album"] = result["title"]
        result["cover_url"] = _catalog_cover(alb_data, "cover_xl", "cover_big", "cover_medium")
        result["upc"] = alb_data.get("upc")
        result["label"] = alb_data.get("label")
        result["release_date"] = alb_data.get("release_date")
        result["explicit"] = bool(alb_data.get("explicit_lyrics"))
        dur = _positive_int(alb_data.get("duration"))
        if dur:
            result["duration"] = dur
            result["duration_formatted"] = f"{dur // 60}:{dur % 60:02d}"
        genres_obj = alb_data.get("genres", {}).get("data", [])
        if isinstance(genres_obj, list) and genres_obj:
            result["genres"] = ", ".join(g.get("name") for g in genres_obj if g.get("name"))
        result["track_position"] = alb_data.get("nb_tracks")
        countries = alb_data.get("available_countries")
        if isinstance(countries, list):
            result["available_countries_count"] = len(countries)
    elif kind == "artist":
        art_data = _api_json(f"{API_ROOT}/artist/{item_id}")
        result["title"] = str(art_data.get("name") or "")
        result["artist"] = result["title"]
        result["cover_url"] = _catalog_cover(art_data, "picture_xl", "picture_big", "picture_medium")
        result["track_position"] = art_data.get("nb_album")
    elif kind == "playlist":
        play_data = _api_json(f"{API_ROOT}/playlist/{item_id}")
        result["title"] = str(play_data.get("title") or "")
        user_obj = play_data.get("creator") if isinstance(play_data.get("creator"), dict) else {}
        result["artist"] = str(user_obj.get("name") or "")
        result["cover_url"] = _catalog_cover(play_data, "picture_xl", "picture_big", "picture_medium")
        result["track_position"] = play_data.get("nb_tracks")

    return result


def fetch_track_lyrics(
    track_id: str | int = "",
    artist: str = "",
    title: str = "",
    album: str = "",
    session: requests.Session | None = None,
) -> dict:
    """Fetch synchronized and plain lyrics using Deezer gw-light API and LRCLIB fallback."""
    lyrics_res = {"synced": None, "unsynced": None, "source": None}
    sess = session or _http_session()

    # 1. Attempt Deezer internal gw-light API if track_id is available
    tid = str(track_id or "").strip()
    if tid.isdigit():
        try:
            ud_resp = sess.get(
                "https://www.deezer.com/ajax/gw-light.php?method=deezer.getUserData&input=3&api_version=1.0&api_token=",
                timeout=6,
            )
            if ud_resp.status_code == 200:
                ud_data = ud_resp.json()
                token = ud_data.get("results", {}).get("checkForm")
                if token:
                    lyr_resp = sess.post(
                        f"https://www.deezer.com/ajax/gw-light.php?method=song.getLyrics&api_version=1.0&api_token={token}&input=3",
                        json={"SNG_ID": tid},
                        timeout=8,
                    )
                    if lyr_resp.status_code == 200:
                        results = lyr_resp.json().get("results") or {}
                        if isinstance(results, dict) and results:
                            unsync = results.get("LYRICS_TEXT")
                            sync_json = results.get("LYRICS_SYNC_JSON")
                            if sync_json and isinstance(sync_json, list):
                                lrc_lines = []
                                for item in sync_json:
                                    ts = item.get("lrc_timestamp") or ""
                                    line = str(item.get("line") or "").strip()
                                    if ts:
                                        lrc_lines.append(f"{ts} {line}" if line else ts)
                                if lrc_lines:
                                    lyrics_res["synced"] = "\n".join(lrc_lines) + "\n"
                            if unsync:
                                lyrics_res["unsynced"] = str(unsync)
                            if lyrics_res["synced"] or lyrics_res["unsynced"]:
                                lyrics_res["source"] = "deezer"
                                return lyrics_res
        except Exception:
            pass

    # 2. Fallback to LRCLIB (open-source synchronized lyrics database)
    clean_artist = str(artist or "").strip()
    clean_title = str(title or "").strip()
    if clean_artist and clean_title:
        try:
            params = {"artist_name": clean_artist, "track_name": clean_title}
            if album:
                params["album_name"] = str(album).strip()
            resp = sess.get("https://lrclib.net/api/get", params=params, timeout=7)
            if resp.status_code == 200:
                data = resp.json()
                synced = data.get("syncedLyrics")
                plain = data.get("plainLyrics")
                if synced:
                    lyrics_res["synced"] = synced
                    lyrics_res["source"] = "lrclib"
                if plain:
                    lyrics_res["unsynced"] = plain
                    if not lyrics_res["source"]:
                        lyrics_res["source"] = "lrclib"
                if lyrics_res["synced"] or lyrics_res["unsynced"]:
                    return lyrics_res
        except Exception:
            pass

    return lyrics_res

