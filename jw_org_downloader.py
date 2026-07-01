from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen

JW_CATEGORY_PATTERN = re.compile(
    r"^(?P<locale>[a-z]{2}(?:-[a-z]{2})?)/categories/"
    r"(?P<category>[A-Za-z0-9_-]+)(?:/.*)?$",
    re.IGNORECASE,
)
JW_LANGUAGE_CODES = {
    "pt": "T",
    "pt-br": "T",
}
API_URLS = (
    "https://data.jw-api.org/mediator/v1/categories/{language}/{category}"
    "?detailed=1&clientType=www",
    "https://b.jw-cdn.org/apis/mediator/v1/categories/{language}/{category}"
    "?detailed=1&clientType=www",
)
MAX_JSON_BYTES = 32 * 1024 * 1024
USER_AGENT = "C2-Video-Downloader/1.2 (+https://c2sistemas.com)"
VIDEO_EXTENSIONS = {".mp4", ".m4v", ".mov", ".webm"}
AUDIO_EXTENSIONS = {".m4a", ".mp3", ".aac", ".opus", ".ogg"}


@dataclass(frozen=True)
class JWCategoryReference:
    language: str
    category: str


@dataclass(frozen=True)
class JWDownloadItem:
    title: str
    media_id: str
    download_url: str
    filesize: int | None
    height: int | None
    extension: str
    source_kind: str


class JWOrgError(RuntimeError):
    pass


def parse_jw_category_url(url: str) -> JWCategoryReference | None:
    parsed = urlparse(url.strip())
    hostname = (parsed.hostname or "").lower()
    if hostname not in {"jw.org", "www.jw.org"}:
        return None

    fragment = unquote(parsed.fragment).lstrip("/")
    match = JW_CATEGORY_PATTERN.match(fragment)
    if not match:
        return None

    locale = match.group("locale").lower()
    language = JW_LANGUAGE_CODES.get(locale)
    if not language:
        raise JWOrgError(
            f"O idioma '{locale}' ainda não está configurado para listas do JW.ORG."
        )

    return JWCategoryReference(
        language=language,
        category=match.group("category"),
    )


def is_jw_category_url(url: str) -> bool:
    try:
        return parse_jw_category_url(url) is not None
    except JWOrgError:
        return True


def _fetch_json(url: str, timeout: int = 45) -> dict[str, object]:
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    with urlopen(request, timeout=timeout) as response:
        body = response.read(MAX_JSON_BYTES + 1)
        if len(body) > MAX_JSON_BYTES:
            raise JWOrgError("O catálogo retornado pelo JW.ORG é maior que o limite permitido.")
    parsed = json.loads(body.decode("utf-8"))
    if not isinstance(parsed, dict):
        raise JWOrgError("O catálogo do JW.ORG retornou um formato inesperado.")
    return parsed


def _fetch_category(
    language: str,
    category: str,
    logger: Callable[[str], None] | None = None,
) -> dict[str, object]:
    errors: list[str] = []
    for template in API_URLS:
        url = template.format(language=language, category=category)
        try:
            return _fetch_json(url)
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, JWOrgError) as exc:
            errors.append(str(exc))
            if logger:
                logger(f"Aviso: tentativa de catálogo indisponível para '{category}' ({exc}).")

    details = errors[-1] if errors else "erro desconhecido"
    raise JWOrgError(f"Não foi possível consultar a categoria '{category}': {details}")


def _positive_int(value: object) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _file_extension(file_data: dict[str, object], url: str) -> str:
    suffix = Path(urlparse(url).path).suffix.lower()
    if suffix and 1 < len(suffix) <= 6:
        return suffix

    mimetype = str(file_data.get("mimetype") or "").lower()
    subtype = mimetype.partition("/")[2].partition(";")[0]
    if subtype == "mpeg":
        return ".mp3"
    if subtype in {"mp4", "x-m4a"}:
        return ".m4a" if mimetype.startswith("audio/") else ".mp4"
    if subtype:
        return f".{subtype}"
    return ".mp4"


def _file_height(file_data: dict[str, object]) -> int | None:
    direct = _positive_int(file_data.get("height"))
    if direct:
        return direct

    for field in ("label", "resolution", "quality"):
        text = str(file_data.get(field) or "")
        match = re.search(r"(?<!\d)(\d{3,4})p?(?!\d)", text, re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None


def _source_kind(file_data: dict[str, object], url: str) -> str:
    mimetype = str(file_data.get("mimetype") or "").lower()
    extension = _file_extension(file_data, url)
    if mimetype.startswith("audio/") or extension in AUDIO_EXTENSIONS:
        return "audio"
    if mimetype.startswith("video/") or extension in VIDEO_EXTENSIONS:
        return "video"
    return "unknown"


def _progressive_files(media: dict[str, object]) -> list[dict[str, object]]:
    raw_files = media.get("files")
    if not isinstance(raw_files, list):
        return []

    result: list[dict[str, object]] = []
    for candidate in raw_files:
        if not isinstance(candidate, dict):
            continue
        url = str(candidate.get("progressiveDownloadURL") or "").strip()
        if not url.startswith("https://"):
            continue
        normalized = dict(candidate)
        normalized["_url"] = url
        normalized["_height"] = _file_height(candidate)
        normalized["_extension"] = _file_extension(candidate, url)
        normalized["_kind"] = _source_kind(candidate, url)
        result.append(normalized)
    return result


def _requested_height(format_choice: str) -> int | None:
    match = re.search(r"(\d{3,4})p", format_choice, re.IGNORECASE)
    return int(match.group(1)) if match else None


def _select_file(
    media: dict[str, object],
    format_choice: str,
) -> dict[str, object] | None:
    files = _progressive_files(media)
    if not files:
        return None

    audio_only = format_choice == "Apenas áudio (M4A)"
    if audio_only:
        audio_files = [item for item in files if item["_kind"] == "audio"]
        if audio_files:
            return max(
                audio_files,
                key=lambda item: (
                    _positive_int(item.get("bitRate")) or 0,
                    _positive_int(item.get("filesize")) or 0,
                ),
            )

    video_files = [item for item in files if item["_kind"] == "video"]
    if not video_files:
        return None

    target = _requested_height(format_choice)
    mp4_preferred = format_choice != "Melhor qualidade"

    def score(item: dict[str, object]) -> tuple[int, int, int, int]:
        height = item["_height"] or 0
        extension = item["_extension"]
        compatibility = 1 if extension in {".mp4", ".m4v"} else 0
        if not mp4_preferred:
            compatibility = 0

        if target is None:
            target_group = 1
            target_score = height
        elif 0 < height <= target:
            target_group = 2
            target_score = height
        elif height > target:
            target_group = 1
            target_score = -height
        else:
            target_group = 0
            target_score = 0

        if target is None:
            return (
                compatibility,
                target_group,
                target_score,
                _positive_int(item.get("filesize")) or 0,
            )
        return (
            target_group,
            compatibility,
            target_score,
            _positive_int(item.get("filesize")) or 0,
        )

    return max(video_files, key=score)


def _media_identifier(media: dict[str, object], fallback: int) -> str:
    for field in ("naturalKey", "lank", "key", "guid", "id"):
        value = str(media.get(field) or "").strip()
        if value:
            return value
    return f"jw-{fallback}"


def resolve_category_items(
    url: str,
    format_choice: str,
    include_subcategories: bool = True,
    logger: Callable[[str], None] | None = None,
) -> list[JWDownloadItem]:
    reference = parse_jw_category_url(url)
    if reference is None:
        raise JWOrgError("O endereço informado não é uma categoria de vídeos do JW.ORG.")

    queue = [reference.category]
    visited_categories: set[str] = set()
    seen_media: set[str] = set()
    items: list[JWDownloadItem] = []

    while queue:
        category_key = queue.pop(0)
        if category_key in visited_categories:
            continue
        if len(visited_categories) >= 250:
            raise JWOrgError("A categoria possui subcategorias demais para processamento seguro.")
        visited_categories.add(category_key)

        if logger:
            logger(f"JW.ORG: consultando categoria {category_key}...")
        response = _fetch_category(reference.language, category_key, logger)
        category = response.get("category")
        if not isinstance(category, dict):
            raise JWOrgError(f"A categoria '{category_key}' retornou dados inválidos.")

        raw_media = category.get("media")
        if isinstance(raw_media, list):
            for position, media in enumerate(raw_media, start=1):
                if not isinstance(media, dict):
                    continue
                selected = _select_file(media, format_choice)
                if not selected:
                    title = str(media.get("title") or "mídia sem título")
                    if logger:
                        logger(f"JW.ORG: nenhuma versão compatível encontrada para '{title}'.")
                    continue

                selected_url = str(selected["_url"])
                media_id = _media_identifier(media, position)
                dedupe_key = media_id or selected_url
                if dedupe_key in seen_media:
                    continue
                seen_media.add(dedupe_key)

                items.append(
                    JWDownloadItem(
                        title=str(media.get("title") or media_id).strip(),
                        media_id=media_id,
                        download_url=selected_url,
                        filesize=_positive_int(selected.get("filesize")),
                        height=selected["_height"],
                        extension=str(selected["_extension"]),
                        source_kind=str(selected["_kind"]),
                    )
                )

        if include_subcategories:
            subcategories = category.get("subcategories")
            if isinstance(subcategories, list):
                for subcategory in subcategories:
                    if not isinstance(subcategory, dict):
                        continue
                    key = str(subcategory.get("key") or "").strip()
                    if key and key not in visited_categories:
                        queue.append(key)

    if not items:
        raise JWOrgError("Nenhum vídeo disponível foi encontrado nessa categoria do JW.ORG.")
    return items


def _safe_filename(value: str, maximum: int = 150) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    if not cleaned:
        cleaned = "video-jw"
    return cleaned[:maximum].rstrip(" .")


def _human_size(size: int | None) -> str:
    if not size:
        return "tamanho desconhecido"
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def download_item(
    item: JWDownloadItem,
    folder: Path,
    index: int,
    total: int,
    logger: Callable[[str], None] | None = None,
    retries: int = 3,
) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    width = max(3, len(str(total)))
    extension = item.extension if item.extension.startswith(".") else f".{item.extension}"
    filename = (
        f"{index:0{width}d} - {_safe_filename(item.title)} "
        f"[{_safe_filename(item.media_id, 60)}]{extension}"
    )
    destination = folder / filename
    temporary = destination.with_name(f".{destination.name}.part")

    if destination.exists() and destination.stat().st_size > 0:
        if not item.filesize or destination.stat().st_size == item.filesize:
            if logger:
                logger(f"JW.ORG [{index}/{total}]: já existe, ignorando {destination.name}")
            return destination

    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        temporary.unlink(missing_ok=True)
        try:
            if logger:
                quality = f"{item.height}p" if item.height else item.source_kind
                logger(
                    f"JW.ORG [{index}/{total}]: baixando {item.title} "
                    f"({quality}, {_human_size(item.filesize)})"
                )

            request = Request(
                item.download_url,
                headers={
                    "Accept": "*/*",
                    "User-Agent": USER_AGENT,
                },
            )
            with urlopen(request, timeout=120) as response, temporary.open("wb") as output:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)

            if not temporary.exists() or temporary.stat().st_size == 0:
                raise JWOrgError("o servidor retornou um arquivo vazio")
            if item.filesize and temporary.stat().st_size != item.filesize:
                raise JWOrgError(
                    f"arquivo incompleto: esperado {item.filesize}, recebido "
                    f"{temporary.stat().st_size}"
                )

            os.replace(temporary, destination)
            if logger:
                logger(f"JW.ORG [{index}/{total}]: concluído {destination.name}")
            return destination
        except (HTTPError, URLError, TimeoutError, OSError, JWOrgError) as exc:
            last_error = exc
            temporary.unlink(missing_ok=True)
            if logger and attempt < retries:
                logger(
                    f"JW.ORG [{index}/{total}]: tentativa {attempt} falhou; "
                    "tentando novamente..."
                )
            time.sleep(min(attempt * 2, 5))

    raise JWOrgError(f"Falha ao baixar '{item.title}': {last_error}")


def convert_to_m4a(
    media_path: Path,
    ffmpeg_path: str | Path | None,
    logger: Callable[[str], None] | None = None,
) -> Path:
    if media_path.suffix.lower() == ".m4a":
        return media_path
    if not ffmpeg_path:
        raise JWOrgError("FFmpeg não está disponível para gerar o arquivo M4A.")

    destination = media_path.with_suffix(".m4a")
    temporary = destination.with_name(f".{destination.stem}.c2-audio.m4a")
    temporary.unlink(missing_ok=True)
    if logger:
        logger(f"Extraindo áudio M4A: {media_path.name}")

    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    completed = subprocess.run(
        [
            str(ffmpeg_path),
            "-y",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-i",
            str(media_path),
            "-vn",
            "-c:a",
            "aac",
            "-profile:a",
            "aac_low",
            "-b:a",
            "160k",
            "-movflags",
            "+faststart",
            str(temporary),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=creationflags,
        timeout=7200,
    )
    if completed.returncode != 0 or not temporary.exists() or temporary.stat().st_size == 0:
        temporary.unlink(missing_ok=True)
        details = completed.stdout.strip().splitlines()
        last_line = details[-1] if details else f"código {completed.returncode}"
        raise JWOrgError(f"Não foi possível gerar o M4A: {last_line}")

    os.replace(temporary, destination)
    if media_path != destination:
        media_path.unlink(missing_ok=True)
    if logger:
        logger(f"Áudio M4A gerado: {destination.name}")
    return destination
