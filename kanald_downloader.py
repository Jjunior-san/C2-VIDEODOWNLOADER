from __future__ import annotations

import json
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen

KANALD_HOSTS = {"kanald.com.tr", "www.kanald.com.tr"}
MAX_HTML_BYTES = 8 * 1024 * 1024
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36 C2-Video-Downloader/1.3"
)
MEDIA_ID_PATTERN = re.compile(
    r'(?:data-id|data-tiak-reference-id)=["\'](?P<id>[0-9a-f]{24})["\']'
    r'|\b_Id\s*:\s*["\'](?P<script_id>[0-9a-f]{24})["\']',
    re.IGNORECASE,
)
SAFE_MEDIA_HOST_SUFFIXES = (
    ".duhnet.tv",
    ".dailymotion.com",
    ".dmcdn.net",
    ".kanald.com.tr",
)


class KanalDError(RuntimeError):
    pass


@dataclass(frozen=True)
class KanalDVideo:
    title: str
    media_id: str
    content_url: str


class _JsonLdParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._inside_json_ld = False
        self._parts: list[str] = []
        self.blocks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "script":
            return
        attributes = {name.lower(): (value or "") for name, value in attrs}
        if attributes.get("type", "").lower() == "application/ld+json":
            self._inside_json_ld = True
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._inside_json_ld:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script" and self._inside_json_ld:
            self.blocks.append("".join(self._parts))
            self._inside_json_ld = False
            self._parts = []


def is_kanald_url(url: str) -> bool:
    parsed = urlparse(url.strip())
    return (
        parsed.scheme in {"http", "https"}
        and (parsed.hostname or "").lower() in KANALD_HOSTS
    )


def _iter_video_objects(value: object):
    if isinstance(value, dict):
        object_type = str(value.get("@type") or "").rsplit("/", 1)[-1].lower()
        if object_type == "videoobject":
            yield value
        for child in value.values():
            yield from _iter_video_objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_video_objects(child)


def _validate_content_url(value: object) -> str | None:
    url = str(value or "").strip()
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not hostname:
        return None
    if not any(hostname.endswith(suffix) for suffix in SAFE_MEDIA_HOST_SUFFIXES):
        return None
    if not parsed.path.lower().endswith((".m3u8", ".mp4")):
        return None
    return url


def _safe_filename_part(value: str, maximum: int) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return (cleaned or "video-kanald")[:maximum].rstrip(" .")


def output_template(video: KanalDVideo) -> str:
    title = _safe_filename_part(video.title, 180).replace("%", "%%")
    media_id = _safe_filename_part(video.media_id, 60).replace("%", "%%")
    return f"{title} [{media_id}].%(ext)s"


def parse_kanald_page(page_url: str, html: str) -> KanalDVideo:
    if not is_kanald_url(page_url):
        raise KanalDError("O endereço informado não pertence ao site Kanal D.")

    parser = _JsonLdParser()
    parser.feed(html)

    selected: dict[str, object] | None = None
    selected_url: str | None = None
    for block in parser.blocks:
        try:
            data = json.loads(block)
        except (TypeError, ValueError):
            continue
        for candidate in _iter_video_objects(data):
            content_url = _validate_content_url(candidate.get("contentUrl"))
            if content_url:
                selected = candidate
                selected_url = content_url
                break
        if selected:
            break

    if not selected or not selected_url:
        raise KanalDError(
            "A página do Kanal D não informou uma fonte de vídeo compatível."
        )

    title = str(selected.get("name") or "").strip()
    if not title:
        slug = unquote(urlparse(page_url).path.rstrip("/").rsplit("/", 1)[-1])
        title = slug.replace("-", " ").strip().title() or "Vídeo Kanal D"

    match = MEDIA_ID_PATTERN.search(html)
    if match:
        media_id = match.group("id") or match.group("script_id")
    else:
        media_id = urlparse(page_url).path.rstrip("/").rsplit("/", 1)[-1]
    media_id = _safe_filename_part(media_id, 60)

    return KanalDVideo(title=title, media_id=media_id, content_url=selected_url)


def resolve_kanald_video(url: str, timeout: int = 45) -> KanalDVideo:
    if not is_kanald_url(url):
        raise KanalDError("O endereço informado não pertence ao site Kanal D.")

    request = Request(
        url,
        headers={
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.7",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            final_url = response.geturl()
            if not is_kanald_url(final_url):
                raise KanalDError("O Kanal D redirecionou para um endereço inesperado.")
            body = response.read(MAX_HTML_BYTES + 1)
            charset = response.headers.get_content_charset() or "utf-8"
    except KanalDError:
        raise
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise KanalDError(f"Não foi possível abrir a página do Kanal D: {exc}") from exc

    if len(body) > MAX_HTML_BYTES:
        raise KanalDError("A página do Kanal D excedeu o limite de tamanho permitido.")
    html = body.decode(charset, errors="replace")
    return parse_kanald_page(final_url, html)
