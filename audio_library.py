"""Audio post-processing helpers shared by regular and catalog downloads."""
from __future__ import annotations

import re
import shutil
import uuid
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from app_config import APP_VERSION


AUDIO_ORIGINAL_FORMAT = "Áudio original (sem conversão)"
AUDIO_AUTO_BITRATE = "Original / automática"
AUDIO_CUSTOM_BITRATE = "Personalizada"
AUDIO_BITRATE_CHOICES = (
    AUDIO_AUTO_BITRATE,
    "64 kbps",
    "96 kbps",
    "128 kbps",
    "160 kbps",
    "192 kbps",
    "256 kbps",
    "320 kbps",
    AUDIO_CUSTOM_BITRATE,
)
AUDIO_FORMATS = {
    AUDIO_ORIGINAL_FORMAT: None,
    "Apenas áudio (M4A)": "m4a",
    "Apenas áudio (MP3)": "mp3",
    "Apenas áudio (Opus)": "opus",
}
AUDIO_EXTENSIONS = {".m4a", ".mp3", ".opus", ".ogg", ".flac", ".wav"}
MUSIC_FOLDER_STRUCTURES = ("Pasta raiz", "Artista", "Artista\\Álbum")
DEFAULT_MUSIC_FILENAME = "{faixa:02} - {titulo}"


def is_audio_format(format_choice: str) -> bool:
    return format_choice in AUDIO_FORMATS


def audio_codec(format_choice: str) -> str | None:
    return AUDIO_FORMATS.get(format_choice)


def audio_bitrate_kbps(mode: object = AUDIO_AUTO_BITRATE, custom: object = "192") -> int | None:
    """Return a validated target bitrate, or None for source/default quality."""
    selected = str(mode or AUDIO_AUTO_BITRATE).strip()
    if selected == AUDIO_AUTO_BITRATE:
        return None
    value = str(custom if selected == AUDIO_CUSTOM_BITRATE else selected).lower()
    value = value.removesuffix("kbps").strip()
    try:
        bitrate = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Informe uma taxa de bits inteira entre 32 e 320 kbps.") from exc
    if not 32 <= bitrate <= 320:
        raise ValueError("A taxa de bits personalizada deve ficar entre 32 e 320 kbps.")
    return bitrate


def bitrate_from_options(options: dict | None) -> int | None:
    values = options or {}
    return audio_bitrate_kbps(
        values.get("audio_bitrate_mode", AUDIO_AUTO_BITRATE),
        values.get("audio_custom_bitrate", "192"),
    )


def _safe_name(value: str, fallback: str = "Playlist") -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")
    return cleaned[:120] or fallback


def music_target_folder(root: Path, item: dict, structure: str = "Pasta raiz") -> Path:
    root = Path(root)
    artist = _safe_name(str(item.get("artist") or "Artista desconhecido"), "Artista desconhecido")
    album = _safe_name(str(item.get("album") or item.get("collection_title") or "Sem álbum"), "Sem álbum")
    if structure == "Artista":
        return root / artist
    if structure == "Artista\\Álbum":
        return root / artist / album
    return root


def music_output_template(item: dict, index: int, template: str = DEFAULT_MUSIC_FILENAME) -> str:
    values = {
        "faixa": int(item.get("track_number") or index or 1),
        "titulo": str(item.get("track_title") or item.get("title") or "Música"),
        "artista": str(item.get("artist") or "Artista desconhecido"),
        "album": str(item.get("album") or item.get("collection_title") or "Sem álbum"),
        "ano": str(item.get("release_year") or ""),
        "id": str(item.get("media_id") or item.get("id") or ""),
    }
    try:
        name = str(template or DEFAULT_MUSIC_FILENAME).format_map(values)
    except (KeyError, ValueError, TypeError):
        name = DEFAULT_MUSIC_FILENAME.format_map(values)
    return f"{_safe_name(name, 'Música')}.%(ext)s"


def _cover_bytes(url: str | None) -> bytes | None:
    if not url:
        return None
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host.endswith("dzcdn.net"):
        return None
    request = Request(url, headers={"User-Agent": f"C2VideoDownloader/{APP_VERSION}"})
    try:
        with urlopen(request, timeout=20) as response:
            content = response.read(8 * 1024 * 1024 + 1)
    except OSError:
        return None
    return content if 0 < len(content) <= 8 * 1024 * 1024 else None


def _syncsafe(value: int) -> bytes:
    return bytes(((value >> 21) & 0x7f, (value >> 14) & 0x7f, (value >> 7) & 0x7f, value & 0x7f))


def _text_frame(frame_id: str, value: object) -> bytes:
    text = str(value or "").strip()
    if not text:
        return b""
    # ID3v2.3 uses UTF-16 (encoding marker 1); Python includes the required BOM.
    payload = b"\x01" + text.encode("utf-16")
    return frame_id.encode("ascii") + len(payload).to_bytes(4, "big") + b"\x00\x00" + payload


def _picture_frame(content: bytes | None) -> bytes:
    if not content:
        return b""
    payload = b"\x00image/jpeg\x00\x03Cover\x00" + content
    return b"APIC" + len(payload).to_bytes(4, "big") + b"\x00\x00" + payload


def _existing_id3_size(source) -> int:
    header = source.read(10)
    if len(header) != 10 or header[:3] != b"ID3":
        return 0
    # Syncsafe integers reserve the high bit in every size byte. Treat a
    # malformed header as audio data instead of trusting a potentially huge
    # seek offset that could discard the original file.
    if any(value & 0x80 for value in header[6:10]):
        return 0
    size = sum((header[6 + index] & 0x7f) << shift for index, shift in enumerate((21, 14, 7, 0)))
    footer = 10 if header[5] & 0x10 else 0
    return 10 + size + footer


def _write_mp3_metadata(path: Path, frames: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tags")
    try:
        with path.open("rb") as source:
            previous_tag_size = _existing_id3_size(source)
            if previous_tag_size > path.stat().st_size:
                previous_tag_size = 0
            source.seek(previous_tag_size)
            with temporary.open("wb") as destination:
                destination.write(b"ID3\x03\x00\x00" + _syncsafe(len(frames)) + frames)
                shutil.copyfileobj(source, destination, 1024 * 1024)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def apply_deezer_metadata(path: Path, item: dict, logger=None) -> None:
    """Attach public catalog metadata to an official preview file."""
    if not path.is_file() or path.suffix.lower() != ".mp3":
        return
    title = str(item.get("track_title") or item.get("title") or "")
    artist = str(item.get("artist") or "")
    album = str(item.get("album") or item.get("collection_title") or "")
    track = item.get("track_number")
    disc = item.get("disc_number")
    year = str(item.get("release_year") or "")
    album_artist = str(item.get("album_artist") or artist)
    cover = cover_bytes_for_item(item)

    frames = b"".join((
        _text_frame("TIT2", title), _text_frame("TPE1", artist),
        _text_frame("TPE2", album_artist), _text_frame("TALB", album),
        _text_frame("TRCK", track), _text_frame("TPOS", disc),
        _text_frame("TDRC", year), _picture_frame(cover),
    ))
    _write_mp3_metadata(path, frames)
    if logger:
        logger(f"Metadados e capa aplicados: {path.name}")


def cover_bytes_for_item(item: dict) -> bytes | None:
    custom = str(item.get("custom_cover_path") or "").strip()
    if custom:
        path = Path(custom)
        try:
            if path.is_file() and 0 < path.stat().st_size <= 8 * 1024 * 1024:
                return path.read_bytes()
        except OSError:
            pass
    return _cover_bytes(item.get("cover_url"))


def read_audio_metadata(path: Path) -> dict[str, str]:
    """Read editable tags from a local audio file using Mutagen."""
    from mutagen import File as MutagenFile

    path = Path(path)
    audio = MutagenFile(path, easy=True)
    if audio is None:
        raise ValueError("Formato de áudio não reconhecido para leitura de metadados.")

    result: dict[str, str] = {}
    aliases = {
        "track_title": ("title",),
        "artist": ("artist",),
        "album": ("album",),
        "album_artist": ("albumartist",),
        "track_number": ("tracknumber",),
        "disc_number": ("discnumber",),
        "release_year": ("date",),
    }
    for target, keys in aliases.items():
        for key in keys:
            value = audio.get(key)
            if value:
                result[target] = str(value[0])
                break
    return result


def write_audio_metadata(path: Path, item: dict, *, cover_bytes: bytes | None = None) -> None:
    """Write common editable tags to MP3/M4A/FLAC/Ogg/Opus files."""
    from mutagen import File as MutagenFile
    from mutagen.flac import FLAC, Picture
    from mutagen.id3 import APIC, ID3, ID3NoHeaderError, PictureType
    from mutagen.mp4 import MP4, MP4Cover

    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)

    audio = MutagenFile(path, easy=True)
    if audio is None:
        raise ValueError("Formato de áudio não reconhecido para edição de metadados.")

    fields = {
        "title": str(item.get("track_title") or item.get("title") or "").strip(),
        "artist": str(item.get("artist") or "").strip(),
        "album": str(item.get("album") or "").strip(),
        "albumartist": str(item.get("album_artist") or item.get("artist") or "").strip(),
        "tracknumber": str(item.get("track_number") or "").strip(),
        "discnumber": str(item.get("disc_number") or "").strip(),
        "date": str(item.get("release_year") or "").strip(),
    }
    for key, value in fields.items():
        try:
            if value:
                audio[key] = [value]
            elif key in audio:
                del audio[key]
        except (KeyError, TypeError):
            # Some containers do not expose every EasyMutagen key.
            pass
    audio.save()

    picture = cover_bytes if cover_bytes is not None else cover_bytes_for_item(item)
    if not picture:
        return

    suffix = path.suffix.lower()
    if suffix == ".mp3":
        try:
            tags = ID3(path)
        except ID3NoHeaderError:
            tags = ID3()
        tags.delall("APIC")
        tags.add(APIC(
            encoding=3, mime="image/jpeg", type=PictureType.COVER_FRONT,
            desc="Cover", data=picture,
        ))
        tags.save(path, v2_version=3)
    elif suffix == ".m4a":
        media = MP4(path)
        if media.tags is None:
            media.add_tags()
        media.tags["covr"] = [MP4Cover(picture, imageformat=MP4Cover.FORMAT_JPEG)]
        media.save()
    elif suffix == ".flac":
        media = FLAC(path)
        media.clear_pictures()
        pic = Picture()
        pic.mime = "image/jpeg"
        pic.type = 3
        pic.desc = "Cover"
        pic.data = picture
        media.add_picture(pic)
        media.save()


def create_deezer_playlists(items: list[dict], folder: Path, logger=None) -> list[Path]:
    groups: dict[str, list[tuple[dict, Path]]] = defaultdict(list)
    for item in items:
        if item.get("kind") != "deezer_preview" or item.get("status") != "completed":
            continue
        collection = str(item.get("collection_title") or "Prévia Deezer")
        for filename in item.get("files", []):
            path = Path(filename)
            if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS:
                groups[collection].append((item, path))
    created = []
    for collection, entries in groups.items():
        if len(entries) < 2:
            continue
        playlist = folder / f"{_safe_name(collection)}.m3u8"
        lines = ["#EXTM3U"]
        for item, path in entries:
            duration = int(item.get("duration") or -1)
            lines.append(f"#EXTINF:{duration},{item.get('title') or path.stem}")
            try:
                lines.append(str(path.relative_to(folder)))
            except ValueError:
                lines.append(str(path))
        temporary = playlist.with_suffix(".m3u8.tmp")
        temporary.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
        temporary.replace(playlist)
        created.append(playlist)
        if logger:
            logger(f"Playlist local criada: {playlist.name}")
    return created
