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
    "Apenas áudio (FLAC)": "flac",
    "Apenas áudio (WAV)": "wav",
}
AUDIO_EXTENSIONS = {".m4a", ".mp3", ".opus", ".ogg", ".flac", ".wav"}
MUSIC_FOLDER_STRUCTURES = ("Pasta raiz", "Artista", "Artista\\Álbum")
DEFAULT_MUSIC_FILENAME = "%artist% - %title%"
LEGACY_MUSIC_FILENAME = "{faixa:02} - {titulo}"

OVERWRITE_ALWAYS = "always"
OVERWRITE_SKIP = "skip"
OVERWRITE_HIGHER_QUALITY = "higher_quality"
OVERWRITE_KEEP_BOTH = "keep_both"

OVERWRITE_CHOICES = (
    "Pular se já existir",
    "Sobrescrever sempre",
    "Sobrescrever se qualidade for superior",
    "Manter ambos (renomear)",
)
OVERWRITE_LABEL_TO_CODE = {
    "Pular se já existir": OVERWRITE_SKIP,
    "pular": OVERWRITE_SKIP,
    "skip": OVERWRITE_SKIP,
    "Sobrescrever sempre": OVERWRITE_ALWAYS,
    "Sempre sobrescrever": OVERWRITE_ALWAYS,
    "sempre": OVERWRITE_ALWAYS,
    "always": OVERWRITE_ALWAYS,
    "Sobrescrever se qualidade for superior": OVERWRITE_HIGHER_QUALITY,
    "Sobrescrever se qualidade maior": OVERWRITE_HIGHER_QUALITY,
    "qualidade": OVERWRITE_HIGHER_QUALITY,
    "higher_quality": OVERWRITE_HIGHER_QUALITY,
    "Manter ambos (1)": OVERWRITE_KEEP_BOTH,
    "Manter ambos (renomear)": OVERWRITE_KEEP_BOTH,
    "manter": OVERWRITE_KEEP_BOTH,
    "keep_both": OVERWRITE_KEEP_BOTH,
}
OVERWRITE_CODE_TO_LABEL = {
    OVERWRITE_SKIP: "Pular se já existir",
    OVERWRITE_ALWAYS: "Sobrescrever sempre",
    OVERWRITE_HIGHER_QUALITY: "Sobrescrever se qualidade for superior",
    OVERWRITE_KEEP_BOTH: "Manter ambos (renomear)",
}

DEEMIX_TEMPLATE_VARIABLES = {
    "%artist%": "Nome do artista principal",
    "%title%": "Título da música",
    "%album%": "Nome do álbum ou coleção",
    "%tracknumber%": "Número da faixa (01, 02)",
    "%discnumber%": "Número do disco / CD",
    "%year%": "Ano de lançamento",
    "%date%": "Data ou ano da gravação",
    "%genre%": "Gênero musical",
    "%isrc%": "Código ISRC internacional",
    "%bpm%": "Batidas por minuto (BPM)",
    "%position%": "Posição na playlist ou fila",
    "%playlist%": "Nome da playlist",
}


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
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(value or "")).strip(" .")
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


def music_output_template(
    item: dict,
    index: int = 1,
    template: str = DEFAULT_MUSIC_FILENAME,
    *,
    pad_tracks: bool = True,
) -> str:
    """Render a dynamic filename template supporting Deemix %tags% and legacy {tags}."""
    raw_track = item.get("track_number") or index or 1
    try:
        track_num = int(raw_track)
    except (ValueError, TypeError):
        track_num = 1
    track_str = f"{track_num:02d}" if pad_tracks else str(track_num)
    pos_str = f"{index:02d}" if pad_tracks else str(index)

    artist = str(item.get("artist") or "Artista desconhecido")
    title = str(item.get("track_title") or item.get("title") or "Música")
    album = str(item.get("album") or item.get("collection_title") or "Sem álbum")
    year = str(item.get("release_year") or "")
    genre = str(item.get("genre") or "")
    isrc = str(item.get("isrc") or "")
    bpm = str(item.get("bpm") or "")
    disc = str(item.get("disc_number") or "1")
    playlist = str(item.get("collection_title") or item.get("playlist_title") or "Playlist")

    result = str(template or DEFAULT_MUSIC_FILENAME).strip()

    # Legacy python format string support ({faixa:02}, {titulo}, etc.)
    if "{" in result and "}" in result:
        values = {
            "faixa": track_num,
            "titulo": title,
            "artista": artist,
            "album": album,
            "ano": year,
            "id": str(item.get("media_id") or item.get("id") or ""),
        }
        try:
            result = result.format_map(values)
        except Exception:
            result = f"{track_str} - {title}"

    # Deemix %tags% support
    var_map = {
        "%artist%": artist,
        "%title%": title,
        "%album%": album,
        "%tracknumber%": track_str,
        "%discnumber%": disc,
        "%year%": year,
        "%date%": year,
        "%genre%": genre,
        "%isrc%": isrc,
        "%bpm%": bpm,
        "%playlist%": playlist,
        "%position%": pos_str,
    }
    for var_key, var_val in var_map.items():
        result = re.sub(re.escape(var_key), var_val, result, flags=re.IGNORECASE)

    # Remove any unhandled percent tags
    result = re.sub(r'%[a-zA-Z0-9_]+%', '', result)

    # Sanitize each path segment to allow subfolders like %artist%/%album%/%title%
    segments = re.split(r'[\\/]', result)
    clean_segments = [_safe_name(s, "Música") for s in segments if s.strip()]
    if not clean_segments:
        clean_segments = ["Música"]
    clean_path = "/".join(clean_segments)
    return f"{clean_path}.%(ext)s"


def get_audio_quality_score(path_or_ext: str, bitrate_kbps: int | None = None) -> int:
    """Return a relative quality score: FLAC/lossless=1411, MP3 320=320, etc."""
    ext = Path(path_or_ext).suffix.lower() if "." in str(path_or_ext) else f".{str(path_or_ext).lower()}"
    if ext in {".flac", ".wav", ".alac"}:
        return 1411
    if bitrate_kbps:
        return int(bitrate_kbps)
    if ext in {".m4a", ".aac"}:
        return 256
    if ext == ".mp3":
        return 320
    return 128


def should_overwrite_file(
    existing_path: Path,
    new_ext: str = ".mp3",
    new_bitrate_kbps: int | None = None,
    policy: str = OVERWRITE_SKIP,
) -> bool:
    """Determine whether to overwrite an existing file based on the configured policy."""
    if not existing_path.is_file() or existing_path.stat().st_size == 0:
        return True
    policy_code = OVERWRITE_LABEL_TO_CODE.get(policy, str(policy).lower())
    if policy_code == OVERWRITE_ALWAYS:
        return True
    if policy_code in {OVERWRITE_SKIP, OVERWRITE_KEEP_BOTH}:
        return False
    if policy_code == OVERWRITE_HIGHER_QUALITY:
        existing_score = get_audio_quality_score(existing_path)
        new_score = get_audio_quality_score(new_ext, new_bitrate_kbps)
        return new_score > existing_score
    return False


def resolve_unique_path(path: Path) -> Path:
    """Return an incremented filename path if the file already exists (e.g. 'Song (1).mp3')."""
    if not path.exists():
        return path
    parent = path.parent
    stem = path.stem
    suffix = path.suffix
    counter = 1
    while True:
        candidate = parent / f"{stem} ({counter}){suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def _cover_bytes(url: str | None, high_res: bool = True) -> bytes | None:
    if not url:
        return None
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host.endswith(("dzcdn.net", "scdn.co", "spotifycdn.com")):
        return None
    target_url = url
    if high_res and "dzcdn.net" in host:
        # Request high-definition 1200x1200 image if available
        target_url = re.sub(r'/\d+x\d+-', '/1200x1200-', target_url)
    request = Request(target_url, headers={"User-Agent": f"C2VideoDownloader/{APP_VERSION}"})
    try:
        with urlopen(request, timeout=20) as response:
            content = response.read(8 * 1024 * 1024 + 1)
    except OSError:
        if target_url != url:
            try:
                with urlopen(Request(url, headers={"User-Agent": f"C2VideoDownloader/{APP_VERSION}"}), timeout=15) as resp:
                    content = resp.read(8 * 1024 * 1024 + 1)
            except OSError:
                return None
        else:
            return None
    return content if 0 < len(content) <= 8 * 1024 * 1024 else None


def save_album_artwork(folder: Path, item: dict, filename: str = "cover.jpg", logger=None) -> Path | None:
    """Download and save high-resolution album artwork into the target album folder."""
    try:
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        art_path = folder / filename
        if art_path.is_file() and art_path.stat().st_size > 0:
            return art_path
        cover_url = item.get("cover_url")
        if not cover_url:
            return None
        data = _cover_bytes(cover_url, high_res=True)
        if data:
            art_path.write_bytes(data)
            if logger:
                logger(f"Capa do álbum salva: {art_path.name}")
            return art_path
    except Exception as exc:
        if logger:
            logger(f"Aviso: não foi possível salvar a capa avulsa ({exc}).")
    return None


def _syncsafe(value: int) -> bytes:
    return bytes(((value >> 21) & 0x7f, (value >> 14) & 0x7f, (value >> 7) & 0x7f, value & 0x7f))


def _text_frame(frame_id: str, value: object) -> bytes:
    text = str(value or "").strip()
    if not text:
        return b""
    # ID3v2.3 uses UTF-16 (encoding marker 1); Python includes the required BOM.
    payload = b"\x01" + text.encode("utf-16")
    return frame_id.encode("ascii") + len(payload).to_bytes(4, "big") + b"\x00\x00" + payload


def _image_mime(content: bytes | None) -> str:
    if content and content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    return "image/jpeg"


def _picture_frame(content: bytes | None) -> bytes:
    if not content:
        return b""
    mime = _image_mime(content).encode("ascii")
    payload = b"\x00" + mime + b"\x00\x03Cover\x00" + content
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


def save_track_lyrics(path: Path, item: dict, logger=None) -> Path | None:
    """Fetch and save synchronized lyrics (.lrc) alongside the audio file."""
    try:
        from deezer_catalog import fetch_track_lyrics
        media_id = item.get("media_id") or ""
        artist = str(item.get("artist") or "")
        title = str(item.get("track_title") or item.get("title") or "")
        album = str(item.get("album") or item.get("collection_title") or "")

        lyrics_data = fetch_track_lyrics(media_id, artist=artist, title=title, album=album)
        synced = lyrics_data.get("synced")
        if synced:
            lrc_path = path.with_suffix(".lrc")
            lrc_path.write_text(synced, encoding="utf-8")
            if logger:
                source_label = "Deezer" if lyrics_data.get("source") == "deezer" else "LRCLIB"
                logger(f"Letras sincronizadas ({source_label}) salvas: {lrc_path.name}")
            return lrc_path
    except Exception as exc:
        if logger:
            logger(f"Aviso: não foi possível obter letras sincronizadas ({exc}).")
    return None


def apply_deezer_metadata(path: Path, item: dict, logger=None) -> None:
    """Attach metadata and cover to an audio file (MP3 or FLAC) and save lyrics."""
    if not path.is_file():
        return
    suffix = path.suffix.lower()
    if suffix not in {".mp3", ".flac", ".m4a", ".opus"}:
        return

    # Use Mutagen for FLAC or other non-mp3 containers
    if suffix == ".flac":
        try:
            write_audio_metadata(path, item)
            if logger:
                logger(f"Metadados FLAC e capa aplicados: {path.name}")
        except Exception as exc:
            if logger:
                logger(f"Aviso: falha ao gravar tags FLAC ({exc}).")
        save_track_lyrics(path, item, logger)
        return

    # For MP3: attempt EasyMutagen / ID3 first
    try:
        write_audio_metadata(path, item)
        if logger:
            logger(f"Metadados e capa aplicados: {path.name}")
        save_track_lyrics(path, item, logger)
        return
    except Exception:
        pass

    title = str(item.get("track_title") or item.get("title") or "")
    artist = str(item.get("artist") or "")
    album = str(item.get("album") or item.get("collection_title") or "")
    track = item.get("track_number")
    disc = item.get("disc_number")
    year = str(item.get("release_year") or "")
    album_artist = str(item.get("album_artist") or artist)
    cover = cover_bytes_for_item(item)
    txxx_frame = b""
    if item.get("explicit"):
        txxx_payload = b"\x00ITUNESADVISORY\x001"
        txxx_frame = b"TXXX" + len(txxx_payload).to_bytes(4, "big") + b"\x00\x00" + txxx_payload

    frames = b"".join((
        _text_frame("TIT2", title), _text_frame("TPE1", artist),
        _text_frame("TPE2", album_artist), _text_frame("TALB", album),
        _text_frame("TRCK", track), _text_frame("TPOS", disc),
        _text_frame("TDRC", year), _picture_frame(cover),
        txxx_frame,
    ))
    _write_mp3_metadata(path, frames)
    if logger:
        logger(f"Metadados e capa aplicados: {path.name}")
    save_track_lyrics(path, item, logger)



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


def read_audio_metadata(path: Path) -> dict[str, str | bool]:
    """Read editable tags from a local audio file using Mutagen."""
    from mutagen import File as MutagenFile

    path = Path(path)
    suffix = path.suffix.lower()
    try:
        audio = MutagenFile(path, easy=True)
    except Exception:
        audio = None

    result: dict[str, str | bool] = {}
    if audio is not None:
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
    elif suffix == ".mp3":
        try:
            from mutagen.id3 import ID3
            tags = ID3(path)
            for target, frame_key in (
                ("track_title", "TIT2"),
                ("artist", "TPE1"),
                ("album", "TALB"),
                ("album_artist", "TPE2"),
                ("track_number", "TRCK"),
                ("disc_number", "TPOS"),
                ("release_year", "TDRC"),
            ):
                frame = tags.get(frame_key)
                if frame and getattr(frame, "text", None):
                    result[target] = str(frame.text[0])
        except Exception:
            pass
    else:
        raise ValueError("Formato de áudio não reconhecido para leitura de metadados.")

    # Read explicit advisory tag
    if suffix == ".mp3":
        try:
            from mutagen.id3 import ID3
            tags = ID3(path)
            for key in ("TXXX:ITUNESADVISORY", "TXXX:iTunes Advisory"):
                frame = tags.get(key)
                if frame and getattr(frame, "text", None) and str(frame.text[0]).strip() == "1":
                    result["explicit"] = True
                    break
        except Exception:
            pass
    elif suffix in {".m4a", ".mp4"}:
        try:
            from mutagen.mp4 import MP4
            media = MP4(path)
            if media.tags and media.tags.get("rtng") == [1]:
                result["explicit"] = True
        except Exception:
            pass
    elif suffix == ".flac":
        try:
            from mutagen.flac import FLAC
            flac = FLAC(path)
            adv = flac.get("ITUNESADVISORY")
            if adv and str(adv[0]).strip() == "1":
                result["explicit"] = True
        except Exception:
            pass

    return result


def write_audio_metadata(path: Path, item: dict, *, cover_bytes: bytes | None = None) -> None:
    """Write common editable tags to MP3/M4A/FLAC/Ogg/Opus files."""
    from mutagen import File as MutagenFile
    from mutagen.flac import FLAC, Picture
    from mutagen.id3 import APIC, ID3, ID3NoHeaderError, PictureType, TXXX
    from mutagen.mp4 import MP4, MP4Cover

    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)

    suffix = path.suffix.lower()
    try:
        audio = MutagenFile(path, easy=True)
    except Exception:
        audio = None

    fields = {
        "title": str(item.get("track_title") or item.get("title") or "").strip(),
        "artist": str(item.get("artist") or "").strip(),
        "album": str(item.get("album") or "").strip(),
        "albumartist": str(item.get("album_artist") or item.get("artist") or "").strip(),
        "tracknumber": str(item.get("track_number") or "").strip(),
        "discnumber": str(item.get("disc_number") or "").strip(),
        "date": str(item.get("release_year") or "").strip(),
    }

    if audio is not None:
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
    elif suffix == ".mp3":
        try:
            tags = ID3(path)
        except ID3NoHeaderError:
            tags = ID3()
        from mutagen.id3 import TALB, TDRC, TIT2, TPE1, TPE2, TPOS, TRCK
        if fields["title"]:
            tags.delall("TIT2")
            tags.add(TIT2(encoding=3, text=[fields["title"]]))
        if fields["artist"]:
            tags.delall("TPE1")
            tags.add(TPE1(encoding=3, text=[fields["artist"]]))
        if fields["album"]:
            tags.delall("TALB")
            tags.add(TALB(encoding=3, text=[fields["album"]]))
        if fields["albumartist"]:
            tags.delall("TPE2")
            tags.add(TPE2(encoding=3, text=[fields["albumartist"]]))
        if fields["tracknumber"]:
            tags.delall("TRCK")
            tags.add(TRCK(encoding=3, text=[fields["tracknumber"]]))
        if fields["discnumber"]:
            tags.delall("TPOS")
            tags.add(TPOS(encoding=3, text=[fields["discnumber"]]))
        if fields["date"]:
            tags.delall("TDRC")
            tags.add(TDRC(encoding=3, text=[fields["date"]]))
        tags.save(path, v2_version=3)
    else:
        raise ValueError("Formato de áudio não reconhecido para edição de metadados.")

    picture = cover_bytes if cover_bytes is not None else cover_bytes_for_item(item)
    suffix = path.suffix.lower()
    explicit = bool(item.get("explicit"))

    if suffix == ".mp3":
        try:
            tags = ID3(path)
        except ID3NoHeaderError:
            tags = ID3()
        if picture:
            tags.delall("APIC")
            tags.add(APIC(
                encoding=3, mime=_image_mime(picture), type=PictureType.COVER_FRONT,
                desc="Cover", data=picture,
            ))
        tags.delall("TXXX:ITUNESADVISORY")
        tags.add(TXXX(encoding=3, desc="ITUNESADVISORY", text=["1" if explicit else "0"]))
        tags.save(path, v2_version=3)
    elif suffix == ".m4a":
        media = MP4(path)
        if media.tags is None:
            media.add_tags()
        if picture:
            image_format = MP4Cover.FORMAT_PNG if _image_mime(picture) == "image/png" else MP4Cover.FORMAT_JPEG
            media.tags["covr"] = [MP4Cover(picture, imageformat=image_format)]
        media.tags["rtng"] = [1 if explicit else 0]
        media.save()
    elif suffix == ".flac":
        media = FLAC(path)
        if picture:
            media.clear_pictures()
            pic = Picture()
            pic.mime = _image_mime(picture)
            pic.type = 3
            pic.desc = "Cover"
            pic.data = picture
            media.add_picture(pic)
        media["ITUNESADVISORY"] = ["1" if explicit else "0"]
        media.save()


def create_deezer_playlists(items: list[dict], folder: Path, logger=None) -> list[Path]:
    groups: dict[str, list[tuple[dict, Path]]] = defaultdict(list)
    for item in items:
        if item.get("kind") not in {"deezer_preview", "deezer_full"} or item.get("status") != "completed":
            continue
        collection = str(item.get("collection_title") or "Coleção Deezer")
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


def create_collection_zip(items: list[dict], folder: Path, playlists: list[Path] | None = None, logger=None) -> list[Path]:
    """Package audio files and matching .m3u8 playlists into .ZIP archives for collections."""
    import zipfile

    groups: dict[str, list[Path]] = defaultdict(list)
    for item in items:
        if item.get("status") != "completed":
            continue
        collection = str(item.get("collection_title") or "").strip()
        if not collection:
            continue
        for filename in item.get("files", []):
            path = Path(filename)
            if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS:
                if path not in groups[collection]:
                    groups[collection].append(path)

    playlist_map = {pl.stem: pl for pl in (playlists or []) if pl.is_file()}
    created_zips = []

    for collection, files in groups.items():
        if len(files) < 2:
            continue
        zip_path = folder / f"{_safe_name(collection)}.zip"
        temp_zip = zip_path.with_suffix(".zip.tmp")
        try:
            with zipfile.ZipFile(temp_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                for file_path in files:
                    try:
                        arcname = file_path.relative_to(folder)
                    except ValueError:
                        arcname = file_path.name
                    zf.write(file_path, arcname=str(arcname))

                # Include corresponding m3u8 playlist if available
                safe_name = _safe_name(collection)
                matching_pl = playlist_map.get(safe_name) or (folder / f"{safe_name}.m3u8")
                if matching_pl.is_file():
                    try:
                        pl_arc = matching_pl.relative_to(folder)
                    except ValueError:
                        pl_arc = matching_pl.name
                    zf.write(matching_pl, arcname=str(pl_arc))

            temp_zip.replace(zip_path)
            created_zips.append(zip_path)
            if logger:
                logger(f"Arquivo ZIP criado: {zip_path.name}")
        except Exception as exc:
            temp_zip.unlink(missing_ok=True)
            if logger:
                logger(f"Aviso: falha ao gerar ZIP da coleção '{collection}' ({exc}).")

    return created_zips
