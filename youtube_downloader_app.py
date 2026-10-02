from __future__ import annotations

import json
import math
import os
import queue
import re
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from datetime import datetime
from pathlib import Path
from tkinter import BooleanVar, Canvas, DoubleVar, END, Frame, Menu, StringVar, Tk, Toplevel, filedialog, messagebox
from tkinter import ttk
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

from app_config import APP_MUTEX, APP_NAME, APP_VERSION
from audio_library import (
    AUDIO_AUTO_BITRATE,
    AUDIO_BITRATE_CHOICES,
    AUDIO_CUSTOM_BITRATE,
    AUDIO_ORIGINAL_FORMAT,
    DEFAULT_MUSIC_FILENAME,
    MUSIC_FOLDER_STRUCTURES,
    audio_codec,
    cover_bytes_for_item,
    is_audio_format,
    read_audio_metadata,
    write_audio_metadata,
)
from deezer_catalog import (
    DeezerSearchResult,
    resolve_deezer_track,
    search_deezer_catalog,
    get_artist_top_tracks,
    get_artist_albums,
    get_album_tracks,
    get_deezer_top_brasil,
    get_deezer_top_global,
    get_deezer_user_favorites,
    analyze_deezer_metadata,
    fetch_track_lyrics,
    is_deezer_url,
)
from spotify_catalog import is_spotify_url
from download_control import DownloadCancelled, DownloadControl
from ui_layout import (
    APPLE_CARD_BG,
    APPLE_DARK_BG,
    APPLE_RED,
    APPLE_SIDEBAR_BG,
    APPLE_TEXT_MUTED,
    APPLE_TEXT_PRIMARY,
    APPLE_TEXT_SECONDARY,
    PROGRAM_BLUE,
    ScrollablePage,
    add_tooltip,
    build_brand,
    configure_fonts,
    fit_window,
    format_quality_badge,
    wrapping_label,
)
from download_queue import QueueRepository, queue_summary
from queue_ui import QueueUI
from queue_service import cookie_arguments
from media_conversion import codec_arguments, duration_from_probe, run_conversion, stream_compatibility
from music_player import MusicPlayer, MusicPlayerError, is_public_deezer_preview
from video_player import EmbeddedVideoPlayer, VideoPlayerError
from lyrics_ui import LyricsDialog
from mini_player_ui import MiniPlayer
from process_monitor import ProcessInactivityError, close_process, monitored_lines
from kanald_downloader import is_kanald_url, resolve_kanald_video
from c2_update import (
    ApplicationUpdater,
    AppUpdate,
    CREATE_NO_WINDOW,
    DATA_DIR,
    DependencyManager,
    DependencyStatus,
)


def fetch_video_preview_info(url: str, yt_dlp_path: Path | None = None) -> dict:
    """Fetch title, author and thumbnail bytes for a video link (e.g. YouTube oEmbed or generic)."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    title = ""
    author = ""
    thumb_url = ""
    data = None

    if "youtube.com" in host or "youtu.be" in host:
        try:
            oembed_url = f"https://www.youtube.com/oembed?url={quote(url, safe='')}&format=json"
            req = Request(oembed_url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
            with urlopen(req, timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8", errors="replace"))
                title = str(payload.get("title") or "")
                author = str(payload.get("author_name") or "")
                thumb_url = str(payload.get("thumbnail_url") or "")
        except Exception:
            pass

    if not title and is_kanald_url(url):
        try:
            video = resolve_kanald_video(url)
            title = video.title
            author = "Kanal D"
        except Exception:
            pass

    if not title and yt_dlp_path and Path(yt_dlp_path).is_file():
        try:
            cmd = [
                str(yt_dlp_path),
                "--dump-single-json",
                "--flat-playlist",
                "--skip-download",
                "--no-warnings",
                "--socket-timeout", "5",
                url,
            ]
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=CREATE_NO_WINDOW,
                timeout=8,
            )
            if res.returncode == 0 and res.stdout.strip():
                info = json.loads(res.stdout)
                title = str(info.get("title") or "")
                author = str(info.get("uploader") or info.get("channel") or "")
                thumb_url = str(info.get("thumbnail") or "")
        except Exception:
            pass

    if thumb_url:
        try:
            req = Request(thumb_url, headers={"User-Agent": "Mozilla/5.0"})
            with urlopen(req, timeout=5) as response:
                data = response.read(5 * 1024 * 1024)
        except Exception:
            data = None

    return {
        "title": title or "Vídeo detectado",
        "author": author or host,
        "cover_data": data,
    }


def resolve_video_stream(
    url: str,
    yt_dlp_path: Path,
    environment: dict[str, str],
    options: dict | None = None,
) -> str:
    """Resolve one public video page into a stream suitable for libVLC."""
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise VideoPlayerError("Informe um link HTTP ou HTTPS válido para assistir.")
    if is_kanald_url(url):
        return resolve_kanald_video(url).content_url
    engine = Path(yt_dlp_path)
    if not engine.is_file():
        raise VideoPlayerError("O yt-dlp ainda não está pronto. Aguarde a verificação de componentes.")
    command = [
        str(engine),
        "--ignore-config",
        "--no-playlist",
        "--no-warnings",
        "--no-color",
        "--socket-timeout", "20",
        "--extractor-retries", "2",
        "--get-url",
        "-f", "best[ext=mp4]/best",
        *cookie_arguments(options or {}),
    ]
    deno = engine.with_name("deno.exe")
    if deno.is_file():
        command += ["--js-runtimes", f"deno:{deno}"]
    command += ["--", url]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=CREATE_NO_WINDOW,
            env=environment,
            timeout=60,
        )
    except subprocess.TimeoutExpired as exc:
        raise VideoPlayerError("A fonte do vídeo demorou demais para responder.") from exc
    streams = [line.strip() for line in result.stdout.splitlines() if line.strip().startswith(("http://", "https://"))]
    if result.returncode != 0 or not streams:
        detail = next(
            (line.strip() for line in reversed(result.stderr.splitlines()) if line.strip()),
            "O site não forneceu uma fonte compatível para reprodução.",
        )
        raise VideoPlayerError(detail)
    return streams[0]


def format_player_time(milliseconds: int | float) -> str:
    seconds = max(0, int(float(milliseconds) / 1000))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02}:{seconds:02}"
    return f"{minutes:02}:{seconds:02}"

try:
    import imageio_ffmpeg

    FFMPEG_PATH = imageio_ffmpeg.get_ffmpeg_exe()
except (ImportError, RuntimeError):
    FFMPEG_PATH = None

SETTINGS_FILE = DATA_DIR / "settings.json"
QUEUE_FILE = DATA_DIR / "downloads.sqlite3"
ACTIVITY_LOG_FILE = DATA_DIR / "activity.log"
OUTPUT_MARKER = "__C2_OUTPUT__:"
PROGRESS_MARKER = "__C2_PROGRESS__:"
POSTPROCESS_MARKER = "__C2_POSTPROCESS__:"
FRAGMENT_CHOICES = (1, 2, 4, 8)
PROGRESS_TEMPLATE = (
    "download:"
    f"{PROGRESS_MARKER}"
    "%(progress.downloaded_bytes)s|%(progress.total_bytes)s|"
    "%(progress.total_bytes_estimate)s|%(progress.speed)s|"
    "%(progress.eta)s|%(progress._percent_str)s|"
    "%(progress.elapsed)s|%(progress.fragment_index)s|%(progress.fragment_count)s|"
    "%(info.filesize_approx)s|%(info.duration)s|%(info.tbr)s|"
    "%(progress.status)s|%(progress.filename)s"
)
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}
VIDEO_DOWNLOAD_FORMATS = [
    "Melhor MP4 compatível",
    "Melhor qualidade",
    "2160p (4K)",
    "1440p (2K)",
    "1080p",
    "720p",
    "480p",
    "360p",
    AUDIO_ORIGINAL_FORMAT,
    "Apenas áudio (M4A)",
    "Apenas áudio (MP3)",
    "Apenas áudio (Opus)",
    "Apenas áudio (FLAC)",
    "Apenas áudio (WAV)",
]
MUSIC_DOWNLOAD_FORMATS = [
    AUDIO_ORIGINAL_FORMAT,
    "Apenas áudio (M4A)",
    "Apenas áudio (MP3)",
    "Apenas áudio (Opus)",
    "Apenas áudio (FLAC)",
    "Apenas áudio (WAV)",
]
DOWNLOAD_FORMATS = VIDEO_DOWNLOAD_FORMATS
BROWSERS = ["Nenhum", "Chrome", "Edge", "Firefox", "Brave", "Opera", "Vivaldi"]
RATE_LIMIT_CHOICES = ["Ilimitado", "500 KB/s", "1 MB/s", "2 MB/s", "5 MB/s", "10 MB/s", "20 MB/s"]
PRESETS = {
    "Personalizado": {},
    "🌟 Máxima Qualidade (4K / 2K / HD)": {
        "format": "Melhor qualidade",
        "audio_bitrate": "Original / automática",
        "chapters": True,
        "subs": True,
    },
    "📱 Celular & WhatsApp (720p H.264 Leve)": {
        "format": "720p",
        "audio_bitrate": "128 kbps (econômica)",
    },
    "⚡ Econômico / Rápido (480p Leve)": {
        "format": "480p",
        "audio_bitrate": "128 kbps (econômica)",
    },
    "🎧 Áudio Hi-Fi Lossless (FLAC)": {
        "format": "Apenas áudio (FLAC)",
        "audio_bitrate": "Original / automática",
    },
    "🎵 Música Universal (MP3 320 kbps)": {
        "format": "Apenas áudio (MP3)",
        "audio_bitrate": "320 kbps (alta qualidade)",
    },
    "🎙️ Podcast & Audiolivro (MP3 128 kbps)": {
        "format": "Apenas áudio (MP3)",
        "audio_bitrate": "128 kbps (econômica)",
    },
}
PRESET_CHOICES = list(PRESETS.keys())
POST_DOWNLOAD_ACTIONS = [
    "Nenhuma ação",
    "Tocar som de alerta",
    "Abrir pasta de downloads",
    "Suspender computador",
    "Desligar o computador (30s)",
]
DOWNLOAD_START_INACTIVITY_SECONDS = 90

def _progress_number(value: str) -> float | None:
    try:
        number = float(value.strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def fragment_count(value: object) -> int:
    try:
        number = int(str(value))
    except (TypeError, ValueError):
        return 4
    return number if number in FRAGMENT_CHOICES else 4


def parse_ytdlp_progress(line: str, elapsed: float) -> dict[str, object] | None:
    if not line.startswith(PROGRESS_MARKER):
        return None
    fields = line[len(PROGRESS_MARKER):].split("|", 13)
    if len(fields) < 6:
        return None
    fields += ["NA"] * (14 - len(fields))

    downloaded = _progress_number(fields[0]) or 0.0
    exact_total = _progress_number(fields[1])
    estimated_total = _progress_number(fields[2])
    fragment_index = _progress_number(fields[7])
    fragment_total = _progress_number(fields[8])
    if not exact_total and not estimated_total:
        estimated_total = _progress_number(fields[9])
        duration = _progress_number(fields[10])
        bitrate = _progress_number(fields[11])
        if not estimated_total and duration and bitrate:
            estimated_total = duration * bitrate * 125
        if not estimated_total and fragment_index and fragment_total:
            estimated_total = downloaded * fragment_total / fragment_index
    total = exact_total or estimated_total
    if total is not None:
        total = max(downloaded, total)
    speed = _progress_number(fields[3])
    eta = _progress_number(fields[4])
    percent_text = fields[5].strip().rstrip("%").strip()
    percent = _progress_number(percent_text)
    if percent is None and total:
        percent = downloaded * 100 / total
    if percent is None and fragment_total and fragment_index is not None:
        percent = fragment_index * 100 / fragment_total
    percent = max(0.0, min(100.0, percent or 0.0))
    if fields[12] == "downloading":
        percent = min(99.9, percent)

    return {
        "downloaded": downloaded,
        "total": total,
        "total_is_estimate": exact_total is None and estimated_total is not None,
        "speed": speed,
        "average_speed": downloaded / max(elapsed, 0.001),
        "eta": eta,
        "percent": percent,
        "percent_known": total is not None or _progress_number(percent_text) is not None or fragment_total is not None,
        "status": fields[12],
        "filename": fields[13],
    }


class ProgressTracker:
    """Exclude resumed bytes and paused time from the transfer average."""

    def __init__(self) -> None:
        self.started: float | None = None
        self.first_bytes = 0.0
        self.last_bytes = 0.0
        self.last_time = 0.0
        self.current_speed: float | None = None
        self.filename = ""

    def parse(self, line: str, now: float) -> dict[str, object] | None:
        payload = parse_ytdlp_progress(line, elapsed=1)
        if payload is None:
            return None
        downloaded = float(payload["downloaded"])
        filename = str(payload["filename"])
        if self.started is None or downloaded < self.last_bytes or filename != self.filename:
            self.started = now
            self.first_bytes = downloaded
            self.filename = filename
            self.last_bytes = downloaded
            self.last_time = now
            self.current_speed = None
        elapsed = max(0.0, now - self.started)
        average = (downloaded - self.first_bytes) / elapsed if elapsed >= 0.25 else None
        payload["average_speed"] = average
        if now - self.last_time >= 0.25:
            self.current_speed = max(0.0, downloaded - self.last_bytes) / (now - self.last_time)
            self.last_time = now
            self.last_bytes = downloaded
        if self.current_speed is not None:
            payload["speed"] = self.current_speed
        # The engine's wall clock includes pauses. Use our active-time average for ETA.
        total = payload["total"]
        payload["eta"] = (max(0.0, float(total) - downloaded) / average) if total and average else None
        return payload


def format_bytes(value: float | int | None) -> str:
    if value is None:
        return "desconhecido"
    size = max(0.0, float(value))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            decimals = 0 if unit == "B" else 1
            return f"{size:.{decimals}f} {unit}".replace(".", ",")
        size /= 1024
    return f"{size:.1f} TB".replace(".", ",")


def format_duration(value: float | int | None) -> str:
    if value is None or not math.isfinite(float(value)) or float(value) < 0:
        return "calculando"
    seconds = int(round(float(value)))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours:d}h {minutes:02d}min"
    if minutes:
        return f"{minutes:d}min {seconds:02d}s"
    return f"{seconds:d}s"


def resource_path(relative_path: str) -> Path:
    base_path = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base_path / relative_path


def load_user_settings() -> dict[str, object]:
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def save_user_settings(settings: dict[str, object]) -> None:
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = SETTINGS_FILE.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(settings, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    os.replace(temporary, SETTINGS_FILE)


class DownloadApp(QueueUI):
    def __init__(self, root: Tk) -> None:
        self.root = root
        self.text_family, self.display_family = configure_fonts(root)
        self.root.title(f"{APP_NAME} {APP_VERSION}")
        fit_window(self.root)

        icon_path = resource_path("assets/c2.ico")
        if icon_path.exists():
            try:
                self.root.iconbitmap(str(icon_path))
            except Exception:
                pass

        self.user_settings = load_user_settings()
        saved_video_folder = str(
            self.user_settings.get("video_download_folder")
            or self.user_settings.get("download_folder")
            or (Path.home() / "Downloads")
        )
        saved_music_folder = str(
            self.user_settings.get("music_download_folder")
            or (Path.home() / "Music" if (Path.home() / "Music").exists() else Path.home() / "Downloads" / "Músicas")
            or saved_video_folder
        )
        saved_video_format = str(
            self.user_settings.get("video_format")
            or self.user_settings.get("format")
            or "Melhor MP4 compatível"
        )
        if saved_video_format not in VIDEO_DOWNLOAD_FORMATS:
            saved_video_format = "Melhor MP4 compatível"

        saved_music_format = str(
            self.user_settings.get("music_format")
            or "Apenas áudio (MP3)"
        )
        if saved_music_format not in MUSIC_DOWNLOAD_FORMATS:
            saved_music_format = "Apenas áudio (MP3)"

        saved_browser = str(self.user_settings.get("cookies_browser") or "Nenhum")
        if saved_browser not in BROWSERS:
            saved_browser = "Nenhum"
        saved_bitrate_mode = str(self.user_settings.get("audio_bitrate_mode") or AUDIO_AUTO_BITRATE)
        if saved_bitrate_mode not in AUDIO_BITRATE_CHOICES:
            saved_bitrate_mode = AUDIO_AUTO_BITRATE
        saved_work_mode = str(self.user_settings.get("work_mode") or "video").lower()
        if saved_work_mode not in {"music", "video"}:
            saved_work_mode = "video"
        saved_music_structure = str(self.user_settings.get("music_structure") or "Artista\\Álbum")
        if saved_music_structure not in MUSIC_FOLDER_STRUCTURES:
            saved_music_structure = "Artista\\Álbum"
        saved_music_filename = str(
            self.user_settings.get("music_filename_template") or DEFAULT_MUSIC_FILENAME
        )

        self.work_mode = saved_work_mode
        self.video_folder_var = StringVar(value=saved_video_folder)
        self.music_folder_var = StringVar(value=saved_music_folder)
        self.folder_var = StringVar(
            value=saved_music_folder if saved_work_mode == "music" else saved_video_folder
        )
        self.video_format_var = StringVar(value=saved_video_format)
        self.music_format_var = StringVar(value=saved_music_format)
        self.resolution_var = StringVar(
            value=saved_music_format if saved_work_mode == "music" else saved_video_format
        )
        self.playlist_var = BooleanVar(value=bool(self.user_settings.get("playlist", True)))
        self.audio_bitrate_mode_var = StringVar(value=saved_bitrate_mode)
        self.audio_custom_bitrate_var = StringVar(
            value=str(self.user_settings.get("audio_custom_bitrate") or "192"),
        )
        self.cookies_browser_var = StringVar(value=saved_browser)
        self.cookies_file_var = StringVar()
        self.fragments_var = StringVar(value=str(fragment_count(self.user_settings.get("concurrent_fragments", 4))))
        self.music_structure_var = StringVar(value=saved_music_structure)
        saved_deezer_arl = str(self.user_settings.get("deezer_arl") or "").strip()
        saved_deezer_quality = str(self.user_settings.get("deezer_quality") or "Automática (melhor da conta)")
        saved_create_zip = bool(self.user_settings.get("create_collection_zip", False))

        self.deezer_arl_var = StringVar(value=saved_deezer_arl)
        self.deezer_quality_var = StringVar(value=saved_deezer_quality)
        self.deezer_status_var = StringVar(
            value="● Conectando..." if saved_deezer_arl else "● Não autenticado (baixando prévias de 30s)"
        )
        self.create_zip_var = BooleanVar(value=saved_create_zip)
        self.deezer_show_arl_var = BooleanVar(value=False)

        # Configurações de legendas
        self.subtitles_enabled_var = BooleanVar(value=bool(self.user_settings.get("subtitles_enabled", False)))
        self.subtitles_embed_var = BooleanVar(value=bool(self.user_settings.get("subtitles_embed", True)))
        self.subtitles_auto_var = BooleanVar(value=bool(self.user_settings.get("subtitles_auto", False)))
        self.subtitles_langs_var = StringVar(value=str(self.user_settings.get("subtitles_langs") or "pt,pt-BR,en"))

        # Recursos de vídeo (SponsorBlock e Capítulos)
        self.sponsorblock_var = BooleanVar(value=bool(self.user_settings.get("sponsorblock", False)))
        self.embed_chapters_var = BooleanVar(value=bool(self.user_settings.get("embed_chapters", True)))
        self.split_chapters_var = BooleanVar(value=bool(self.user_settings.get("split_chapters", False)))

        # Desempenho e Rede (Rate limit, Proxy, Clipboard)
        saved_rate_limit = str(self.user_settings.get("rate_limit") or "Ilimitado")
        if saved_rate_limit not in RATE_LIMIT_CHOICES:
            saved_rate_limit = "Ilimitado"
        self.rate_limit_var = StringVar(value=saved_rate_limit)
        self.proxy_url_var = StringVar(value=str(self.user_settings.get("proxy_url") or ""))
        self.clipboard_monitor_var = BooleanVar(value=bool(self.user_settings.get("clipboard_monitor", False)))
        self._last_clipboard_url: str | None = None
        self._clipboard_after_id = None

        # Perfis rápidos e Ações de conclusão
        saved_preset = str(self.user_settings.get("preset") or "Personalizado")
        if saved_preset not in PRESET_CHOICES:
            saved_preset = "Personalizado"
        self.preset_var = StringVar(value=saved_preset)

        saved_post_action = str(self.user_settings.get("post_download_action") or "Nenhuma ação")
        if saved_post_action not in POST_DOWNLOAD_ACTIONS:
            saved_post_action = "Nenhuma ação"
        self.post_download_action_var = StringVar(value=saved_post_action)
        self.header_status_var = StringVar(value="Fila: 0  •  Concluídos: 0")
        self.detected_links_var = StringVar(value="Nenhum link inserido")

        self.music_filename_var = StringVar(value=saved_music_filename)
        self.music_search_var = StringVar()
        self.music_search_type_var = StringVar(
            value=str(self.user_settings.get("music_search_type") or "Música"),
        )
        if self.music_search_type_var.get() not in {"Música", "Artista", "Álbum", "Playlist"}:
            self.music_search_type_var.set("Música")
        self.music_search_status_var = StringVar(value="Digite para pesquisar.")
        self._music_search_after = None
        self._music_search_generation = 0
        self._music_search_results = []
        self._music_search_cache: dict[tuple[str, str], tuple[float, tuple]] = {}
        self._music_search_tasks = queue.Queue()
        self._music_search_shutdown = threading.Event()
        self._music_search_workers = []
        for index in range(2):
            worker = threading.Thread(
                target=self._music_search_worker_loop,
                name=f"c2-deezer-search-{index + 1}",
                daemon=True,
            )
            worker.start()
            self._music_search_workers.append(worker)
        self._catalog_cover_image = None
        self._catalog_cover_key = None
        self._playing_item_id = None
        self._catalog_playing_active = False
        self._video_preview_after = None
        self._last_video_preview_url = None
        self._video_cover_image = None
        self.video_player = EmbeddedVideoPlayer()
        self.video_player_status_var = StringVar(value="Pronto para reproduzir.")
        self.video_player_time_var = StringVar(value="00:00 / 00:00")
        self.video_player_seek_var = DoubleVar(value=0.0)
        self.video_volume_var = DoubleVar(value=80.0)
        self._video_seek_dragging = False
        self._video_source_key = None
        self._video_resolve_request = None
        self.download_fragments = fragment_count(self.fragments_var.get())
        self.download_control = DownloadControl()
        self.update_status_var = StringVar(value="Componentes ainda não verificados")
        self.download_item_var = StringVar(value="Nenhum download em andamento")
        self.download_metrics_var = StringVar(
            value="Aguardando início do download."
        )
        self.progress_value_var = DoubleVar(value=0.0)
        self.context_queue_trees = {}
        self.context_queue_counts = {}
        self.context_download_buttons = []
        self.context_pause_buttons = []
        self.context_stop_buttons = []

        self.busy = False
        self.maintenance_busy = False
        self.log_queue: queue.Queue[str] = queue.Queue()
        self._activity_log_lock = threading.Lock()
        self.event_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.available_update: AppUpdate | None = None
        self.dependency_status: DependencyStatus | None = None
        self.download_job_started_at = 0.0
        self.download_item_started_at = 0.0
        self.download_item_index = 1
        self.download_item_total = 1
        self.queue_items = []
        self.active_queue_id = None
        self.queue_running = False
        self.download_options = None
        self.ffmpeg_path = FFMPEG_PATH
        self.music_player = MusicPlayer(DATA_DIR / "preview_cache")
        self.music_player_track_var = StringVar(value="Nenhuma faixa em reprodução.")
        self.music_player_status_var = StringVar(value="Pronto para reproduzir.")
        self.music_player_time_var = StringVar(value="00:00 / 00:00")
        self.music_player_seek_var = DoubleVar(value=0.0)
        self.music_volume_var = DoubleVar(value=80.0)
        self.music_volume_label_var = StringVar(value="80%")
        self._music_seek_dragging = False
        self._music_is_muted = False
        self.current_music_item_id = None
        self._music_cover_image = None

        # Apple Music Bottom Bar & Sidebar Variables
        self.bottom_track_var = StringVar(value="Nenhuma faixa em reprodução")
        self.bottom_artist_var = StringVar(value="C² Downloader")
        self.bottom_time_var = StringVar(value="00:00 / 00:00")
        self.bottom_badge_var = StringVar(value="")
        self.bottom_seek_var = DoubleVar(value=0.0)
        self.bottom_volume_var = DoubleVar(value=80.0)
        self._bottom_seek_dragging = False
        self._lyrics_dialog = None
        self._mini_player = None
        self._sidebar_buttons = {}

        self.dependencies = DependencyManager()
        self.app_updater = ApplicationUpdater(self.dependencies)
        queue_error = None
        try:
            self.queue_repository = QueueRepository(QUEUE_FILE)
        except Exception as exc:
            self.queue_repository = None
            queue_error = str(exc)

        self._build_ui()
        self.queue_log(f"Registro de diagnóstico: {ACTIVITY_LOG_FILE}")
        try:
            if self.queue_repository is not None:
                self._restore_queue()
        except Exception as exc:
            self.queue_repository = None
            queue_error = str(exc)
        if queue_error:
            self.download_button.configure(state="disabled")
            self.analyze_button.configure(state="disabled")
            self.queue_count.configure(text="Não foi possível abrir a fila salva. Consulte a aba Atividade.")
            self.queue_log(f"Fila preservada em {QUEUE_FILE}. Erro ao ler/gravar: {queue_error}")
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.bind("<Destroy>", self._on_root_destroy, add="+")
        self.root.bind("<FocusIn>", lambda _e: self._check_clipboard_for_media(), add="+")
        self._poll_queues()
        self.root.after(700, self.start_maintenance)
        self.root.after(1500, self._schedule_clipboard_tick)

    def _build_ui(self) -> None:
        shell = ttk.Frame(self.root, padding=6)
        shell.pack(fill="both", expand=True)

        header = ttk.Frame(shell)
        header.pack(fill="x", pady=(0, 6))
        try:
            self._build_site_logo(header).pack(side="left", padx=(0, 10))
        except Exception as exc:
            self.queue_log(f"Aviso: não foi possível carregar a logo ({exc}).")
            ttk.Label(
                header,
                text="C² - Downloader",
                font=(self.display_family, 15, "bold"),
                foreground=PROGRAM_BLUE,
            ).pack(side="left")

        self.sidebar_toggle_btn = ttk.Button(
            header,
            text="☰",
            width=3,
            command=self._toggle_sidebar,
        )
        self.sidebar_toggle_btn.pack(side="left", padx=(2, 8))
        add_tooltip(self.sidebar_toggle_btn, "Mostrar/Ocultar barra lateral")

        self.settings_button = ttk.Button(
            header,
            text="⚙",
            width=3,
            command=self._open_settings,
        )
        self.settings_button.pack(side="right")
        add_tooltip(self.settings_button, "Configurações")

        # Mantidos como atributos em memória para compatibilidade sem poluir o cabeçalho superior
        self.open_folder_header_btn = ttk.Button(
            header,
            text="📁 Pasta",
            command=self.open_current_download_folder,
        )
        self.lyrics_header_btn = ttk.Button(
            header,
            text="💬 Letras",
            command=self._open_lyrics_window,
        )
        self.mini_player_header_btn = ttk.Button(
            header,
            text="🔲 Mini",
            command=self._open_mini_player,
        )

        self.header_status_label = ttk.Label(
            header,
            textvariable=self.header_status_var,
            font=(self.text_family, 9, "bold"),
            foreground=PROGRAM_BLUE,
        )
        self.header_status_label.pack(side="right", padx=(0, 10))

        # Barra de reprodução estilo Apple Music fixada no rodapé
        self._build_apple_now_playing_bar(shell)

        # Divisão central com Sidebar e Área de Conteúdo
        self.main_split = ttk.Frame(shell)
        self.main_split.pack(fill="both", expand=True)

        self.content_container = ttk.Frame(self.main_split)
        self.content_container.pack(side="right", fill="both", expand=True)

        self.tabs = ttk.Notebook(self.content_container)
        self.tabs.pack(fill="both", expand=True)

        self.music_page = ScrollablePage(self.tabs)
        self.video_page = ScrollablePage(self.tabs)
        self.queue_page = ttk.Frame(self.tabs, padding=10)
        self.completed_page = ttk.Frame(self.tabs, padding=10)
        self.activity_page = ttk.Frame(self.tabs, padding=10)
        self.about_page = ttk.Frame(self.tabs, padding=18)

        self.settings_dialog = Toplevel(self.root)
        self.settings_dialog.withdraw()
        self.settings_dialog.title("Configurações")
        self.settings_dialog.transient(self.root)
        self.settings_dialog.protocol("WM_DELETE_WINDOW", lambda: self._close_settings(True))
        fit_window(self.settings_dialog, max_width=820, max_height=720)
        self.settings_page = ScrollablePage(self.settings_dialog)
        self.settings_page.pack(fill="both", expand=True)

        self.tabs.add(self.music_page, text="  Música  ")
        self.tabs.add(self.video_page, text="  Vídeo  ")
        self.tabs.add(self.queue_page, text="  Fila geral  ")
        self.tabs.add(self.completed_page, text="  Concluídos  ")
        self.tabs.add(self.activity_page, text="  Atividade  ")
        self.tabs.add(self.about_page, text="  Sobre  ")

        self.sidebar_visible = True
        self._build_apple_sidebar(self.main_split)

        about_brand = ttk.Frame(self.about_page)
        about_brand.pack(anchor="center", pady=(24, 12))
        try:
            self._build_site_logo(about_brand).pack()
        except Exception:
            ttk.Label(
                about_brand,
                text="C² - Downloader",
                font=(self.display_family, 18, "bold"),
                foreground="#1876d2",
            ).pack()
        ttk.Label(
            self.about_page,
            text="C2 Sistemas",
            font=(self.display_family, 14, "bold"),
            foreground="#172b4d",
        ).pack(pady=(0, 4))
        ttk.Label(
            self.about_page,
            text=f"Versão {APP_VERSION}",
            foreground="#596579",
        ).pack()
        about_text = ttk.Label(
            self.about_page,
            text=(
                "Aplicativo para organizar, baixar e reproduzir mídias compatíveis.\n"
                "Use somente conteúdo próprio, livre ou autorizado pelo titular."
            ),
            justify="center",
            wraplength=620,
        )
        about_text.pack(fill="x", pady=(18, 12))
        ttk.Button(
            self.about_page,
            text="Abrir projeto no GitHub",
            command=lambda: webbrowser.open("https://github.com/Jjunior-san/C2-VIDEODOWNLOADER"),
        ).pack()

        music = self.music_page.body
        video = self.video_page.body

        # Música: pesquisa instantânea e resultados sem precisar de botão Buscar.
        search_frame = ttk.LabelFrame(music, text="Pesquisar na Deezer & Spotify", padding=10)
        search_frame.pack(fill="x", pady=(0, 8))
        search_row = ttk.Frame(search_frame)
        search_row.pack(fill="x")
        self.catalog_back_button = ttk.Button(
            search_row,
            text="⬅ Voltar",
            command=self._restore_previous_music_search,
            state="disabled",
            width=9,
        )
        self.catalog_back_button.pack(side="left", padx=(0, 6))
        add_tooltip(self.catalog_back_button, "Voltar para o resultado da busca anterior")
        ttk.Combobox(
            search_row,
            textvariable=self.music_search_type_var,
            values=("Música", "Artista", "Álbum", "Playlist"),
            state="readonly",
            width=12,
        ).pack(side="left", padx=(0, 8))
        self.music_search_entry = ttk.Entry(
            search_row,
            textvariable=self.music_search_var,
            font=(self.text_family, 11),
        )
        self.music_search_entry.pack(side="left", fill="x", expand=True)

        discovery_row = ttk.Frame(search_frame)
        discovery_row.pack(fill="x", pady=(6, 2))
        ttk.Label(
            discovery_row,
            text="🔥 Descoberta:",
            font=(self.text_family, 9, "bold"),
            foreground="#2563eb",
        ).pack(side="left", padx=(0, 6))

        self.btn_top_brasil = ttk.Button(
            discovery_row,
            text="🇧🇷 Top Brasil",
            command=self._load_top_brasil,
        )
        self.btn_top_brasil.pack(side="left", padx=(0, 4))
        add_tooltip(self.btn_top_brasil, "Carregar as 50 músicas mais tocadas no Brasil na Deezer")

        self.btn_top_global = ttk.Button(
            discovery_row,
            text="🌍 Top Global",
            command=self._load_top_global,
        )
        self.btn_top_global.pack(side="left", padx=(0, 4))
        add_tooltip(self.btn_top_global, "Carregar a parada mundial Top 50 da Deezer")

        self.btn_user_favs = ttk.Button(
            discovery_row,
            text="⭐ Meus Favoritos",
            command=self._load_user_favorites,
        )
        self.btn_user_favs.pack(side="left", padx=(0, 4))
        add_tooltip(self.btn_user_favs, "Carregar as músicas curtidas da sua conta Deezer conectada")

        self.btn_link_analyzer = ttk.Button(
            discovery_row,
            text="🔍 Raio-X Metadados",
            command=self._open_link_analyzer_dialog,
        )
        self.btn_link_analyzer.pack(side="left", padx=(0, 4))
        add_tooltip(self.btn_link_analyzer, "Inspecionar metadados de qualquer link Deezer (ISRC, BPM, gravadora)")

        ttk.Label(
            search_frame,
            textvariable=self.music_search_status_var,
            foreground="#596579",
        ).pack(anchor="w", pady=(6, 0))

        result_area = ttk.Panedwindow(music, orient="horizontal")
        result_area.pack(fill="both", expand=True, pady=(0, 8))

        result_list = ttk.Frame(result_area)
        result_detail = ttk.LabelFrame(result_detail_parent := result_area, text="Detalhes", padding=10)
        result_area.add(result_list, weight=3)
        result_area.add(result_detail, weight=2)

        result_table = ttk.Frame(result_list)
        result_table.pack(fill="both", expand=True)
        self.music_results_tree = ttk.Treeview(
            result_table,
            columns=("type", "title", "detail"),
            show="headings",
            height=11,
            selectmode="browse",
        )
        for name, label, width in (
            ("type", "Tipo", 85),
            ("title", "Resultado", 280),
            ("detail", "Artista / Álbum / Autor", 250),
        ):
            self.music_results_tree.heading(name, text=label)
            self.music_results_tree.column(
                name,
                width=width,
                minwidth=70,
                stretch=name != "type",
                anchor="w",
            )
        self.music_results_tree.grid(row=0, column=0, sticky="nsew")
        self.music_results_tree.bind("<Button-3>", self._on_music_tree_context_menu)
        result_table.rowconfigure(0, weight=1)
        result_table.columnconfigure(0, weight=1)
        result_scroll = ttk.Scrollbar(
            result_table,
            orient="vertical",
            command=self.music_results_tree.yview,
        )
        result_scroll.grid(row=0, column=1, sticky="ns")
        self.music_results_tree.configure(yscrollcommand=result_scroll.set)

        cover_box = ttk.Frame(result_detail)
        cover_box.pack(side="left", anchor="n", padx=(0, 12))
        self.catalog_cover_label = ttk.Label(
            cover_box,
            text="Sem capa",
            width=18,
            anchor="center",
        )
        self.catalog_cover_label.pack()
        detail_info = ttk.Frame(result_detail)
        detail_info.pack(side="left", fill="both", expand=True)
        self.catalog_title_var = StringVar(value="Selecione um resultado")
        self.catalog_subtitle_var = StringVar()
        self.catalog_type_var = StringVar()
        wrapping_label(
            detail_info,
            textvariable=self.catalog_title_var,
            font=(self.text_family, 11, "bold"),
        )
        wrapping_label(detail_info, textvariable=self.catalog_subtitle_var)
        wrapping_label(
            detail_info,
            textvariable=self.catalog_type_var,
            foreground="#596579",
        )
        result_buttons = ttk.Frame(detail_info)
        result_buttons.pack(fill="x", pady=(6, 0))

        # Linha 1 de Ações Principais
        r1 = ttk.Frame(result_buttons)
        r1.pack(fill="x", pady=(0, 4))

        self.catalog_play_button = ttk.Button(
            r1,
            text="▶",
            width=3,
            command=self._toggle_catalog_playback,
            state="normal",
            style="ApplePlay.TButton",
        )
        self.catalog_play_button.pack(side="left")
        add_tooltip(self.catalog_play_button, "Reproduzir (faixa completa se ARL configurada, ou prévia de 30s)")

        self.catalog_stop_button = ttk.Button(
            r1,
            text="■",
            width=3,
            command=self.stop_music,
            state="normal",
        )
        self.catalog_stop_button.pack(side="left", padx=(4, 0))
        add_tooltip(self.catalog_stop_button, "Parar a reprodução")

        self.catalog_download_button = ttk.Button(
            r1,
            text="⬇️ Baixar",
            command=self._download_selected_catalog_result,
            state="normal",
            style="Accent.TButton",
        )
        self.catalog_download_button.pack(side="left", padx=(6, 0))
        add_tooltip(self.catalog_download_button, "Baixar item selecionado para a biblioteca")

        self.catalog_load_button = ttk.Button(
            r1,
            text="➕ Fila",
            command=self._load_selected_catalog_result,
            state="normal",
        )
        self.catalog_load_button.pack(side="left", padx=(4, 0))
        add_tooltip(self.catalog_load_button, "Adicionar à fila geral de downloads")

        self.catalog_drill_button = ttk.Button(
            r1,
            text="💿 Ver Álbum",
            command=self._view_album_for_selected,
            state="normal",
        )
        self.catalog_drill_button.pack(side="left", padx=(6, 0))
        add_tooltip(self.catalog_drill_button, "Ver todas as faixas do álbum correspondente")

        self.catalog_album_download_button = ttk.Button(
            r1,
            text="⚡ Baixar Álbum Completo",
            command=self._download_album_for_selected,
            state="normal",
            style="Accent.TButton",
        )
        self.catalog_album_download_button.pack(side="left", padx=(4, 0))
        add_tooltip(self.catalog_album_download_button, "Adicionar todas as faixas deste álbum à fila e iniciar o download")

        # Linha 2 de Ferramentas e Opções Adicionais
        r2 = ttk.Frame(result_buttons)
        r2.pack(fill="x", pady=(2, 0))

        self.catalog_tracklist_button = ttk.Button(
            r2,
            text="☑️ Escolher Faixas do Álbum...",
            command=self._tracklist_for_selected,
            state="normal",
        )
        self.catalog_tracklist_button.pack(side="left")
        add_tooltip(self.catalog_tracklist_button, "Abrir lista interativa [✓] para escolher faixas específicas do álbum")

        self.catalog_artist_albums_button = ttk.Button(
            r2,
            text="💿 Discografia",
            command=self._drill_artist_albums,
            state="normal",
        )
        self.catalog_artist_albums_button.pack(side="left", padx=(6, 0))
        add_tooltip(self.catalog_artist_albums_button, "Ver todos os álbuns deste artista")

        self.catalog_analyzer_button = ttk.Button(
            r2,
            text="🔍 Raio-X",
            command=lambda: self._open_link_analyzer_dialog(),
            state="normal",
        )
        self.catalog_analyzer_button.pack(side="left", padx=(6, 0))
        add_tooltip(self.catalog_analyzer_button, "Inspecionar metadados detalhados (ISRC, BPM, gravadora)")

        self.catalog_open_button = ttk.Button(
            r2,
            text="🌐 Deezer",
            command=self._open_selected_catalog_result,
            state="normal",
        )
        self.catalog_open_button.pack(side="left", padx=(6, 0))
        add_tooltip(self.catalog_open_button, "Abrir página oficial no Deezer")

        open_local_btn = ttk.Button(
            r2,
            text="📂 Abrir local...",
            command=self.open_local_audio_file,
        )
        open_local_btn.pack(side="left", padx=(6, 0))
        add_tooltip(open_local_btn, "Reproduzir arquivo de áudio do computador")

        # In-memory audio controls frame to keep backward compatibility and test invariants without cluttering UI
        music_player_card = ttk.Frame(music)
        player_info_row = ttk.Frame(music_player_card)
        wrapping_label(player_info_row, textvariable=self.music_player_track_var, font=(self.text_family, 10, "bold"))
        ttk.Label(player_info_row, textvariable=self.music_player_status_var, foreground="#596579", font=(self.text_family, 9))
        player_ctrls_row = ttk.Frame(music_player_card)
        self.audio_play_button = ttk.Button(player_ctrls_row, text="▶", width=3, command=self._toggle_audio_player_playback)
        self.audio_stop_button = ttk.Button(player_ctrls_row, text="■", width=3, command=self.stop_music, state="disabled")
        self.audio_seek_scale = ttk.Scale(player_ctrls_row, from_=0, to=100, variable=self.music_player_seek_var)
        self.audio_seek_scale.bind("<ButtonPress-1>", self._begin_music_seek)
        self.audio_seek_scale.bind("<ButtonRelease-1>", self._finish_music_seek)
        ttk.Label(player_ctrls_row, textvariable=self.music_player_time_var, width=15, anchor="center", font=(self.text_family, 9))
        self.audio_mute_button = ttk.Button(player_ctrls_row, text="🔊", width=3, command=self._toggle_music_mute)
        self.audio_volume_scale = ttk.Scale(player_ctrls_row, from_=0, to=100, variable=self.music_volume_var, command=self._set_music_volume, length=80)
        ttk.Label(player_ctrls_row, textvariable=self.music_volume_label_var, width=5, anchor="w", font=(self.text_family, 9))

        options = ttk.LabelFrame(music, text="Download e organização", padding=8)
        options.pack(fill="x")
        options.columnconfigure(1, weight=1)
        options.columnconfigure(5, weight=1)

        ttk.Label(options, text="Pasta:").grid(row=0, column=0, sticky="w", padx=(0, 6), pady=3)
        ttk.Entry(options, textvariable=self.music_folder_var).grid(row=0, column=1, columnspan=3, sticky="ew", pady=3)
        ttk.Button(options, text="Escolher", command=self.choose_music_folder).grid(row=0, column=4, padx=(6, 12), pady=3)

        ttk.Label(options, text="Formato:").grid(row=0, column=5, sticky="e", padx=(0, 6), pady=3)
        self.music_format_combo = ttk.Combobox(
            options,
            textvariable=self.music_format_var,
            values=MUSIC_DOWNLOAD_FORMATS,
            state="readonly",
            width=21,
        )
        self.music_format_combo.grid(row=0, column=6, sticky="ew", pady=3)
        self.music_format_combo.bind("<<ComboboxSelected>>", self._on_music_format_selected)

        ttk.Label(options, text="Pastas:").grid(row=1, column=0, sticky="w", padx=(0, 6), pady=3)
        ttk.Combobox(
            options,
            textvariable=self.music_structure_var,
            values=MUSIC_FOLDER_STRUCTURES,
            state="readonly",
            width=18,
        ).grid(row=1, column=1, sticky="w", pady=3)
        ttk.Label(options, text="Nome:").grid(row=1, column=2, sticky="e", padx=(8, 6), pady=3)
        ttk.Entry(options, textvariable=self.music_filename_var).grid(row=1, column=3, columnspan=2, sticky="ew", pady=3)
        ttk.Label(options, text="Taxa:").grid(row=1, column=5, sticky="e", padx=(8, 6), pady=3)
        self._build_audio_controls(options, row=1, column=6)
        ttk.Checkbutton(
            options,
            text="Carregar todas as faixas ao abrir artista, álbum ou playlist",
            variable=self.playlist_var,
        ).grid(row=2, column=0, columnspan=7, sticky="w", pady=(4, 0))
        self._build_context_queue(music, "music")

        # Vídeo: entrada e opções em uma tela própria.
        video_input = ttk.LabelFrame(video, text="Links de vídeo / playlists", padding=10)
        video_input.pack(fill="x", pady=(0, 8))

        url_toolbar = ttk.Frame(video_input)
        url_toolbar.pack(fill="x", pady=(0, 6))
        ttk.Button(url_toolbar, text="📋 Colar", command=self._paste_clipboard_to_url_text).pack(side="left")
        ttk.Button(url_toolbar, text="📂 Importar .txt", command=self._import_urls_from_file).pack(side="left", padx=4)
        ttk.Button(url_toolbar, text="💾 Exportar", command=self._export_urls_to_file).pack(side="left")
        ttk.Button(url_toolbar, text="🧹 Limpar", command=self._clear_url_text).pack(side="left", padx=4)
        self.detected_links_label = ttk.Label(
            url_toolbar,
            textvariable=self.detected_links_var,
            font=(self.text_family, 9),
            foreground="#2563eb",
        )
        self.detected_links_label.pack(side="right")

        self.video_url_text = self._make_text(video_input, height=3)
        self.video_url_text.pack(fill="both", expand=True)
        wrapping_label(
            video_input,
            text="Cole um link por linha. YouTube, Kanal D, JW.ORG e outras fontes compatíveis com yt-dlp.",
            foreground="#596579",
        )
        self.video_url_text.bind("<KeyRelease>", lambda e: (self._update_detected_links_count(), self._schedule_video_preview_check()))
        self.video_url_text.bind("<<Paste>>", lambda e: self.root.after(100, lambda: (self._update_detected_links_count(), self._schedule_video_preview_check())))

        # Card de prévia automática de vídeo
        self.video_preview_frame = ttk.LabelFrame(video, text="Prévia do vídeo", padding=8)
        self.video_preview_frame.pack(fill="x", pady=(0, 8))
        video_cover_box = ttk.Frame(self.video_preview_frame)
        video_cover_box.pack(side="left", anchor="n", padx=(0, 12))
        self.video_cover_label = ttk.Label(
            video_cover_box,
            text="Sem capa",
            width=20,
            anchor="center",
        )
        self.video_cover_label.pack()
        video_detail_info = ttk.Frame(self.video_preview_frame)
        video_detail_info.pack(side="left", fill="both", expand=True)
        self.video_preview_title_var = StringVar(value="Cole um link de vídeo acima para ver os detalhes")
        self.video_preview_author_var = StringVar(value="")
        wrapping_label(
            video_detail_info,
            textvariable=self.video_preview_title_var,
            font=(self.text_family, 11, "bold"),
        )
        wrapping_label(
            video_detail_info,
            textvariable=self.video_preview_author_var,
            foreground="#596579",
        )

        player = ttk.LabelFrame(video, text="Player", padding=8)
        player.pack(fill="x", pady=(0, 8))
        self.video_surface = Frame(
            player,
            background="#05070a",
            height=300,
            highlightthickness=1,
            highlightbackground="#26364a",
        )
        self.video_surface.pack(fill="x")
        self.video_surface.pack_propagate(False)
        controls = ttk.Frame(player)
        controls.pack(fill="x", pady=(8, 0))
        self.video_play_button = ttk.Button(
            controls,
            text="▶",
            width=3,
            command=self.play_selected_video,
        )
        self.video_play_button.pack(side="left")
        self.video_stop_button = ttk.Button(
            controls,
            text="■",
            width=3,
            command=self.stop_video,
            state="disabled",
        )
        self.video_stop_button.pack(side="left", padx=(6, 8))
        add_tooltip(self.video_play_button, "Reproduzir ou pausar o vídeo")
        add_tooltip(self.video_stop_button, "Parar o vídeo")
        self.video_seek_scale = ttk.Scale(
            controls,
            from_=0,
            to=100,
            variable=self.video_player_seek_var,
        )
        self.video_seek_scale.pack(side="left", fill="x", expand=True)
        self.video_seek_scale.bind("<ButtonPress-1>", self._begin_video_seek)
        self.video_seek_scale.bind("<ButtonRelease-1>", self._finish_video_seek)
        ttk.Label(
            controls,
            textvariable=self.video_player_time_var,
            width=15,
            anchor="center",
        ).pack(side="left", padx=(8, 8))
        ttk.Label(controls, text="🔊").pack(side="left")
        self.video_volume_scale = ttk.Scale(
            controls,
            from_=0,
            to=100,
            variable=self.video_volume_var,
            command=self._set_video_volume,
            length=100,
        )
        self.video_volume_scale.pack(side="left", padx=(4, 0))
        wrapping_label(
            player,
            textvariable=self.video_player_status_var,
            foreground="#596579",
        )

        video_options = ttk.LabelFrame(video, text="Opções", padding=8)
        video_options.pack(fill="x")
        video_options.columnconfigure(1, weight=1)
        ttk.Label(video_options, text="Pasta:").grid(row=0, column=0, sticky="w", padx=(0, 6), pady=3)
        ttk.Entry(video_options, textvariable=self.video_folder_var).grid(row=0, column=1, sticky="ew", pady=3)
        ttk.Button(video_options, text="Escolher", command=self.choose_video_folder).grid(row=0, column=2, padx=(6, 12), pady=3)
        ttk.Label(video_options, text="Formato:").grid(row=0, column=3, sticky="e", padx=(0, 6), pady=3)
        self.video_format_combo = ttk.Combobox(
            video_options,
            textvariable=self.video_format_var,
            values=VIDEO_DOWNLOAD_FORMATS,
            state="readonly",
            width=24,
        )
        self.video_format_combo.grid(row=0, column=4, sticky="w", pady=3)
        self.video_format_combo.bind("<<ComboboxSelected>>", self._on_video_format_selected)
        ttk.Checkbutton(
            video_options,
            text="Playlist inteira",
            variable=self.playlist_var,
        ).grid(row=1, column=0, sticky="w", pady=3)
        ttk.Label(video_options, text="Taxa do áudio:").grid(row=1, column=3, sticky="e", padx=(0, 6), pady=3)
        self._build_audio_controls(video_options, row=1, column=4)
        video_actions = ttk.Frame(video_options)
        video_actions.grid(row=1, column=2, sticky="e", padx=(6, 12), pady=3)
        ttk.Button(
            video_actions,
            text="Adicionar à fila",
            command=self.analyze_links,
        ).pack(side="left")
        ttk.Button(
            video_actions,
            text="Baixar",
            command=self.start_download,
            style="Accent.TButton",
        ).pack(side="left", padx=(6, 0))

        preset_row = ttk.Frame(video_options)
        preset_row.grid(row=2, column=0, columnspan=5, sticky="ew", pady=(4, 0))
        ttk.Label(preset_row, text="⚡ Perfil Rápido:", font=(self.text_family, 9, "bold")).pack(side="left", padx=(0, 6))
        self.preset_combo = ttk.Combobox(
            preset_row,
            textvariable=self.preset_var,
            values=PRESET_CHOICES,
            state="readonly",
            width=36,
        )
        self.preset_combo.pack(side="left")
        self.preset_combo.bind("<<ComboboxSelected>>", self._on_preset_selected)
        add_tooltip(self.preset_combo, "Aplica formatos e opções recomendadas para o seu objetivo")

        self._build_context_queue(video, "video")

        # Fila em aba própria: elimina a maior parte do scroll da tela de trabalho.
        self._build_episode_list(self.queue_page)
        actions = self.episode_actions
        actions.pack(fill="x", pady=(0, 8))
        self.download_button = ttk.Button(actions, text="Baixar", command=self.start_download, style="Accent.TButton")
        self.download_button.pack(side="left")
        self.pause_button = ttk.Button(actions, text="Pausar", command=self.toggle_pause, state="disabled")
        self.pause_button.pack(side="left", padx=(8, 0))
        self.stop_button = ttk.Button(actions, text="Parar fila", command=self.stop_queue, state="disabled")
        self.stop_button.pack(side="left", padx=(8, 0))

        progress_frame = ttk.LabelFrame(self.queue_page, text="Progresso", padding=8)
        progress_frame.pack(fill="x")
        wrapping_label(
            progress_frame,
            textvariable=self.download_item_var,
            font=(self.text_family, 10, "bold"),
        )
        self.progress = ttk.Progressbar(
            progress_frame,
            mode="determinate",
            maximum=100,
            variable=self.progress_value_var,
        )
        self.progress.pack(fill="x", pady=(0, 6))
        wrapping_label(progress_frame, textvariable=self.download_metrics_var, foreground="#3f4f5f")

        self._build_completed_list(self.completed_page)

        settings = self.settings_page.body
        speed_frame = ttk.LabelFrame(settings, text="Desempenho e Rede", padding=10)
        speed_frame.pack(fill="x", pady=(0, 12))

        speed_row = ttk.Frame(speed_frame)
        speed_row.pack(fill="x", pady=(0, 8))
        ttk.Label(speed_row, text="Fragmentos simultâneos:").pack(side="left", padx=(0, 8))
        ttk.Combobox(
            speed_row,
            textvariable=self.fragments_var,
            values=FRAGMENT_CHOICES,
            state="readonly",
            width=3,
        ).pack(side="left", padx=(0, 16))

        ttk.Label(speed_row, text="Limite de velocidade:").pack(side="left", padx=(0, 8))
        ttk.Combobox(
            speed_row,
            textvariable=self.rate_limit_var,
            values=RATE_LIMIT_CHOICES,
            state="readonly",
            width=12,
        ).pack(side="left")

        proxy_row = ttk.Frame(speed_frame)
        proxy_row.pack(fill="x", pady=(0, 6))
        ttk.Label(proxy_row, text="Proxy (HTTP/SOCKS5):").pack(side="left", padx=(0, 8))
        ttk.Entry(proxy_row, textvariable=self.proxy_url_var, width=28).pack(side="left", fill="x", expand=True)

        clip_check = ttk.Checkbutton(
            speed_frame,
            text="Monitorar área de transferência (detectar links copiados automaticamente)",
            variable=self.clipboard_monitor_var,
        )
        clip_check.pack(anchor="w", pady=(2, 6))

        wrapping_label(
            speed_frame,
            text="Dica: use 4 fragmentos; limite a velocidade para não sobrecarregar sua conexão.",
            foreground="#596579",
        )

        automation_frame = ttk.LabelFrame(settings, text="Automação & Ação ao Concluir Fila", padding=10)
        automation_frame.pack(fill="x", pady=(0, 12))
        post_row = ttk.Frame(automation_frame)
        post_row.pack(fill="x", pady=(0, 6))
        ttk.Label(post_row, text="Ao concluir todos os downloads:").pack(side="left", padx=(0, 8))
        ttk.Combobox(
            post_row,
            textvariable=self.post_download_action_var,
            values=POST_DOWNLOAD_ACTIONS,
            state="readonly",
            width=28,
        ).pack(side="left")
        wrapping_label(
            automation_frame,
            text="Permite tocar aviso sonoro, abrir a pasta ou suspender/desligar o PC após concluir a fila.",
            foreground="#596579",
        )

        subtitles_frame = ttk.LabelFrame(settings, text="Legendas (Subtitles & CC)", padding=10)
        subtitles_frame.pack(fill="x", pady=(0, 12))
        ttk.Checkbutton(
            subtitles_frame,
            text="Baixar legendas automaticamente quando disponíveis",
            variable=self.subtitles_enabled_var,
        ).pack(anchor="w", pady=(0, 4))
        sub_opts_row = ttk.Frame(subtitles_frame)
        sub_opts_row.pack(fill="x", pady=(0, 4))
        ttk.Checkbutton(
            sub_opts_row,
            text="Embutir legendas no arquivo de vídeo (soft subs)",
            variable=self.subtitles_embed_var,
        ).pack(side="left", padx=(0, 16))
        ttk.Checkbutton(
            sub_opts_row,
            text="Incluir legendas geradas automaticamente (auto-captions)",
            variable=self.subtitles_auto_var,
        ).pack(side="left")
        langs_row = ttk.Frame(subtitles_frame)
        langs_row.pack(fill="x", pady=(4, 4))
        ttk.Label(langs_row, text="Idiomas das legendas:").pack(side="left", padx=(0, 8))
        ttk.Entry(langs_row, textvariable=self.subtitles_langs_var, width=22).pack(side="left")
        wrapping_label(
            subtitles_frame,
            text="Separe idiomas por vírgula (ex: pt,pt-BR,en,es). Desmarque 'Embutir' para salvar arquivos .srt separados.",
            foreground="#596579",
        )

        video_features_frame = ttk.LabelFrame(settings, text="Otimizações de Vídeo & Capítulos", padding=10)
        video_features_frame.pack(fill="x", pady=(0, 12))
        ttk.Checkbutton(
            video_features_frame,
            text="Remover patrocínios internos e vinhetas (SponsorBlock)",
            variable=self.sponsorblock_var,
        ).pack(anchor="w", pady=(0, 4))
        ttk.Checkbutton(
            video_features_frame,
            text="Embutir marcadores de capítulos no arquivo",
            variable=self.embed_chapters_var,
        ).pack(anchor="w", pady=(0, 4))
        ttk.Checkbutton(
            video_features_frame,
            text="Dividir vídeo em arquivos separados por capítulo (--split-chapters)",
            variable=self.split_chapters_var,
        ).pack(anchor="w", pady=(0, 4))
        wrapping_label(
            video_features_frame,
            text="A divisão por capítulos é recomendada para coletâneas de música ou podcasts em vídeo único.",
            foreground="#596579",
        )

        cookies_frame = ttk.LabelFrame(settings, text="Acesso a sites com login", padding=10)
        cookies_frame.pack(fill="x", pady=(0, 12))
        browser_row = ttk.Frame(cookies_frame)
        browser_row.pack(fill="x", pady=(0, 8))
        ttk.Label(browser_row, text="Cookies do navegador:").pack(side="left", padx=(0, 8))
        ttk.Combobox(
            browser_row,
            textvariable=self.cookies_browser_var,
            values=BROWSERS,
            state="readonly",
            width=14,
        ).pack(side="left")
        wrapping_label(
            cookies_frame,
            text="Feche o Chrome/Edge antes de usar seus cookies. Fontes públicas do Kanal D não precisam deles.",
            foreground="#596579",
        )
        ttk.Label(cookies_frame, text="Ou arquivo cookies.txt:").pack(anchor="w")
        file_row = ttk.Frame(cookies_frame)
        file_row.pack(fill="x", pady=(4, 0))
        ttk.Entry(file_row, textvariable=self.cookies_file_var, width=12).pack(side="left", fill="x", expand=True)
        ttk.Button(file_row, text="Selecionar", command=self.choose_cookies_file).pack(side="left", padx=(8, 0))
        ttk.Button(file_row, text="Limpar", command=lambda: self.cookies_file_var.set("")).pack(side="left", padx=(8, 0))

        deezer_frame = ttk.LabelFrame(settings, text="Autenticação Deezer (Músicas completas)", padding=10)
        deezer_frame.pack(fill="x", pady=(0, 12))

        arl_row = ttk.Frame(deezer_frame)
        arl_row.pack(fill="x", pady=(0, 6))
        ttk.Label(arl_row, text="Cookie ARL:").pack(side="left", padx=(0, 8))
        self.deezer_arl_entry = ttk.Entry(
            arl_row,
            textvariable=self.deezer_arl_var,
            show="*" if not self.deezer_show_arl_var.get() else "",
            width=28,
        )
        self.deezer_arl_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))
        ttk.Button(arl_row, text="Colar", command=self._paste_deezer_arl).pack(side="left", padx=(0, 4))
        self.deezer_toggle_btn = ttk.Button(arl_row, text="Mostrar", command=self._toggle_show_arl, width=8)
        self.deezer_toggle_btn.pack(side="left", padx=(0, 4))
        ttk.Button(arl_row, text="Verificar / Salvar", command=lambda: self._check_deezer_arl(show_dialog=True)).pack(side="left", padx=(0, 4))
        ttk.Button(arl_row, text="Limpar", command=self._clear_deezer_arl).pack(side="left")

        status_row = ttk.Frame(deezer_frame)
        status_row.pack(fill="x", pady=(2, 6))
        ttk.Label(status_row, text="Status da conta:").pack(side="left", padx=(0, 8))
        wrapping_label(status_row, textvariable=self.deezer_status_var, font=(self.text_family, 9, "bold"))

        options_row = ttk.Frame(deezer_frame)
        options_row.pack(fill="x", pady=(4, 6))
        ttk.Label(options_row, text="Qualidade Deezer:").pack(side="left", padx=(0, 8))
        ttk.Combobox(
            options_row,
            textvariable=self.deezer_quality_var,
            values=["Automática (melhor da conta)", "FLAC Lossless", "MP3 320 kbps", "MP3 128 kbps"],
            state="readonly",
            width=26,
        ).pack(side="left", padx=(0, 16))

        zip_check = ttk.Checkbutton(
            deezer_frame,
            text="Compactar álbum / playlist em arquivo .ZIP ao concluir",
            variable=self.create_zip_var,
        )
        zip_check.pack(anchor="w", pady=(2, 6))

        wrapping_label(
            deezer_frame,
            text="Dica: Faça login no deezer.com pelo navegador > F12 > Armazenamento/Cookies > copie o valor de 'arl'. Sem ARL, o aplicativo continuará baixando as prévias públicas de 30s.",
            foreground="#596579",
        )

        about_frame = ttk.LabelFrame(settings, text="Aplicativo", padding=10)
        about_frame.pack(fill="x")
        wrapping_label(
            about_frame,
            text=f"{APP_NAME} • Versão {APP_VERSION}",
            font=(self.text_family, 10, "bold"),
        )
        wrapping_label(about_frame, textvariable=self.update_status_var, foreground="#596579")
        self.update_button = ttk.Button(
            about_frame,
            text="Verificar atualizações",
            command=lambda: self.start_maintenance(True),
        )
        self.update_button.pack(anchor="w", pady=(0, 10))

        settings_actions = ttk.Frame(settings)
        settings_actions.pack(fill="x", pady=(0, 8))
        ttk.Button(
            settings_actions,
            text="Cancelar",
            command=lambda: self._close_settings(False),
        ).pack(side="right")
        ttk.Button(
            settings_actions,
            text="Salvar configurações",
            command=lambda: self._close_settings(True),
        ).pack(side="right", padx=(0, 8))

        activity = self.activity_page
        ttk.Button(activity, text="Limpar atividade", command=self.clear_log).pack(anchor="e", pady=(0, 8))
        log_frame = ttk.Frame(activity)
        log_frame.pack(fill="both", expand=True)
        self.log = self._make_text(log_frame, height=20)
        self.log.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(log_frame, command=self.log.yview)
        scrollbar.pack(side="right", fill="y")
        self.log.configure(yscrollcommand=scrollbar.set, state="disabled")

        self._build_music_details(music)
        self._apply_work_mode(self.work_mode, initial=True)
        self.tabs.select(self.music_page if self.work_mode == "music" else self.video_page)
        self.tabs.bind("<<NotebookTabChanged>>", self._on_main_tab_changed)

        self.music_search_var.trace_add("write", self._schedule_music_search)
        self.music_search_type_var.trace_add("write", self._schedule_music_search)
        self.resolution_var.trace_add("write", self._on_resolution_var_changed)
        self.folder_var.trace_add("write", self._on_folder_var_changed)
        self.audio_bitrate_mode_var.trace_add("write", self._update_audio_controls)
        self.music_results_tree.bind("<<TreeviewSelect>>", self._show_selected_catalog_result)
        self.music_results_tree.bind("<Double-1>", lambda _event: self._on_music_result_double_click())
        self.music_search_entry.bind("<Return>", lambda _event: self._load_selected_catalog_result())
        self.music_search_entry.focus_set()

        # Atalhos Globais de Teclado
        self.root.bind("<Control-m>", lambda _e: self._focus_music_search())
        self.root.bind("<Control-M>", lambda _e: self._focus_music_search())
        self.root.bind("<Control-Key-1>", lambda _e: self.tabs.select(self.music_page))
        self.root.bind("<Control-Key-2>", lambda _e: self.tabs.select(self.video_page))
        self.root.bind("<Control-Key-3>", lambda _e: self.tabs.select(self.queue_page))
        self.root.bind("<Control-Key-4>", lambda _e: self.tabs.select(self.completed_page))
        self.root.bind("<Control-Key-5>", lambda _e: self.tabs.select(self.activity_page))
        self.root.bind("<Control-Key-6>", lambda _e: self.tabs.select(self.about_page))
        self._update_audio_controls()
        if self.deezer_arl_var.get().strip():
            self.root.after(400, lambda: self._check_deezer_arl(show_dialog=False))

    def _toggle_sidebar(self) -> None:
        if getattr(self, "sidebar_visible", True):
            self.sidebar_frame.pack_forget()
            self.sidebar_visible = False
        else:
            self.sidebar_frame.pack(side="left", fill="y", padx=(0, 6), before=self.content_container)
            self.sidebar_visible = True

    def _build_apple_sidebar(self, parent) -> None:
        self.sidebar_frame = ttk.Frame(parent, width=200, padding=(2, 4))
        self.sidebar_frame.pack_propagate(False)
        self.sidebar_frame.pack(side="left", fill="y", padx=(0, 6), before=self.content_container)

        # Cabeçalho da Sidebar
        brand_lbl = ttk.Label(
            self.sidebar_frame,
            text="⚡ C² Downloader",
            font=(self.display_family, 11, "bold"),
            foreground=PROGRAM_BLUE,
            padding=(10, 4, 4, 6),
        )
        brand_lbl.pack(fill="x")

        # Container interno com rolagem suave
        canvas = Canvas(self.sidebar_frame, borderwidth=0, highlightthickness=0)
        inner = ttk.Frame(canvas)
        canvas_win = canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(canvas_win, width=e.width))
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))

        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind("<MouseWheel>", _on_mousewheel)
        inner.bind("<MouseWheel>", _on_mousewheel)
        canvas.pack(fill="both", expand=True)

        def _sec(title: str):
            ttk.Label(
                inner,
                text=title,
                font=(self.text_family, 8, "bold"),
                foreground="#8e8e93",
                padding=(10, 8, 2, 2),
            ).pack(fill="x")

        def _item(text: str, page_obj, key: str, tooltip: str = ""):
            btn = ttk.Button(
                inner,
                text=text,
                style="Sidebar.TButton",
                command=lambda p=page_obj: self.tabs.select(p),
            )
            btn.pack(fill="x", padx=4, pady=1)
            if tooltip:
                add_tooltip(btn, tooltip)
            self._sidebar_buttons[key] = (btn, page_obj)
            return btn

        def _action(text: str, command, tooltip: str = ""):
            btn = ttk.Button(
                inner,
                text=text,
                style="Sidebar.TButton",
                command=command,
            )
            btn.pack(fill="x", padx=4, pady=1)
            if tooltip:
                add_tooltip(btn, tooltip)
            return btn

        _sec("NAVEGAÇÃO")
        _item("🎵 Música & Catálogo", self.music_page, "music", "Buscar e ouvir faixas, álbuns e discografias")
        _item("🎬 Vídeos & Links", self.video_page, "video", "Baixar vídeos do YouTube e links diretos")

        _sec("BIBLIOTECA")
        _item("⏳ Fila Geral", self.queue_page, "queue", "Ver downloads ativos e em fila")
        _item("✅ Concluídos", self.completed_page, "completed", "Histórico de downloads finalizados")
        self.sidebar_folder_btn = _action(
            "📁 Pasta de Downloads",
            self.open_current_download_folder,
            "Abrir pasta de downloads no Windows Explorer",
        )

        _sec("EXPLORAR")
        self.sidebar_favs_btn = _action(
            "⭐ Meus Favoritos",
            lambda: (self.tabs.select(self.music_page), self._load_user_favorites()),
            "Carregar faixas favoritas da conta Deezer",
        )
        self.sidebar_top_br_btn = _action(
            "🔥 Top 50 Brasil",
            lambda: (self.tabs.select(self.music_page), self._load_top_brasil()),
            "Carregar Top 50 mais ouvidas no Brasil",
        )
        self.sidebar_top_gl_btn = _action(
            "🌐 Top 50 Global",
            lambda: (self.tabs.select(self.music_page), self._load_top_global()),
            "Carregar Top 50 mais ouvidas no Mundo",
        )

        _sec("FERRAMENTAS")
        self.sidebar_lyrics_btn = _action(
            "💬 Letras Karaokê",
            self._open_lyrics_window,
            "Abrir letras sincronizadas em tempo real",
        )
        self.sidebar_mini_btn = _action(
            "🔲 MiniPlayer",
            self._open_mini_player,
            "Abrir MiniPlayer flutuante Always-on-Top",
        )
        self.sidebar_analyzer_btn = _action(
            "🔍 Raio-X Metadados",
            lambda: (self.tabs.select(self.music_page), self._open_link_analyzer_dialog()),
            "Inspecionar metadados ISRC, BPM e gravadora",
        )

        _sec("SISTEMA")
        _item("📜 Atividade / Logs", self.activity_page, "activity", "Registro detalhado de atividade e diagnósticos")
        _item("ℹ️ Sobre", self.about_page, "about", "Informações e créditos do aplicativo")
        self.sidebar_settings_btn = _action(
            "⚙ Configurações",
            self._open_settings,
            "Painel de preferências, qualidade e contas",
        )

    def _sync_sidebar_selection(self) -> None:
        if not hasattr(self, "_sidebar_buttons"):
            return
        selected = self.tabs.select()
        for key, (btn, page) in self._sidebar_buttons.items():
            if str(page) == selected:
                btn.configure(style="ActiveSidebar.TButton")
            else:
                btn.configure(style="Sidebar.TButton")

    def _build_apple_now_playing_bar(self, parent) -> None:
        self.bottom_player_bar = ttk.Frame(parent, padding=(10, 6))
        self.bottom_player_bar.pack(side="bottom", fill="x", pady=(4, 0))

        div = ttk.Separator(parent, orient="horizontal")
        div.pack(side="bottom", fill="x", before=self.bottom_player_bar)

        left_col = ttk.Frame(self.bottom_player_bar)
        left_col.pack(side="left", padx=(0, 12))

        art_box = ttk.Frame(left_col, width=40, height=40)
        art_box.pack(side="left", padx=(0, 8))
        art_box.pack_propagate(False)
        self.bottom_art_label = ttk.Label(
            art_box,
            text="🎵",
            font=(self.display_family, 14),
            foreground=PROGRAM_BLUE,
            anchor="center",
        )
        self.bottom_art_label.pack(expand=True)

        meta_box = ttk.Frame(left_col)
        meta_box.pack(side="left")

        title_row = ttk.Frame(meta_box)
        title_row.pack(fill="x", anchor="w")

        ttk.Label(
            title_row,
            textvariable=self.bottom_track_var,
            font=(self.display_family, 9, "bold"),
            width=22,
            anchor="w",
        ).pack(side="left")

        self.bottom_badge_label = ttk.Label(
            title_row,
            textvariable=self.bottom_badge_var,
            font=(self.text_family, 8, "bold"),
            foreground=PROGRAM_BLUE,
        )
        self.bottom_badge_label.pack(side="left", padx=(4, 0))

        ttk.Label(
            meta_box,
            textvariable=self.bottom_artist_var,
            font=(self.text_family, 8),
            foreground="#8e8e93",
            width=26,
            anchor="w",
        ).pack(fill="x", anchor="w")

        center_col = ttk.Frame(self.bottom_player_bar)
        center_col.pack(side="left", fill="x", expand=True, padx=8)

        ctrls_row = ttk.Frame(center_col)
        ctrls_row.pack(anchor="center")

        ttk.Button(
            ctrls_row,
            text="⏮",
            width=3,
            command=self._bottom_seek_backward,
        ).pack(side="left", padx=2)

        self.bottom_play_button = ttk.Button(
            ctrls_row,
            text="▶",
            width=4,
            style="ApplePlay.TButton",
            command=self._toggle_audio_player_playback,
        )
        self.bottom_play_button.pack(side="left", padx=4)

        self.bottom_stop_button = ttk.Button(
            ctrls_row,
            text="⏹",
            width=3,
            command=self.stop_music,
        )
        self.bottom_stop_button.pack(side="left", padx=2)

        ttk.Button(
            ctrls_row,
            text="⏭",
            width=3,
            command=self._bottom_seek_forward,
        ).pack(side="left", padx=2)

        time_row = ttk.Frame(center_col)
        time_row.pack(fill="x", expand=True, pady=(2, 0))

        self.bottom_seek_scale = ttk.Scale(
            time_row,
            from_=0.0,
            to=100.0,
            variable=self.bottom_seek_var,
            command=lambda _v: None,
        )
        self.bottom_seek_scale.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.bottom_seek_scale.bind("<ButtonPress-1>", lambda _e: setattr(self, "_bottom_seek_dragging", True))
        self.bottom_seek_scale.bind("<ButtonRelease-1>", self._on_bottom_seek_release)

        ttk.Label(
            time_row,
            textvariable=self.bottom_time_var,
            font=(self.text_family, 8),
            foreground="#8e8e93",
        ).pack(side="right")

        right_col = ttk.Frame(self.bottom_player_bar)
        right_col.pack(side="right")

        ttk.Label(
            right_col,
            text="🔊",
            font=(self.text_family, 9),
        ).pack(side="left", padx=(0, 3))

        vol_scale = ttk.Scale(
            right_col,
            from_=0,
            to=100,
            variable=self.bottom_volume_var,
            command=self._on_bottom_volume_change,
            length=75,
        )
        vol_scale.pack(side="left", padx=(0, 8))

        lyrics_btn = ttk.Button(
            right_col,
            text="💬",
            width=3,
            command=self._open_lyrics_window,
        )
        lyrics_btn.pack(side="left", padx=2)
        add_tooltip(lyrics_btn, "Letras sincronizadas (Karaokê)")

        mini_btn = ttk.Button(
            right_col,
            text="🔲",
            width=3,
            command=self._open_mini_player,
        )
        mini_btn.pack(side="left", padx=2)
        add_tooltip(mini_btn, "MiniPlayer flutuante")

    def _bottom_seek_backward(self) -> None:
        cur = self.music_player.get_position()
        self.music_player.set_position(max(0.0, cur - 10.0))
        self._update_music_player()

    def _bottom_seek_forward(self) -> None:
        cur = self.music_player.get_position()
        dur = self.music_player.get_duration()
        self.music_player.set_position(min(dur, cur + 10.0))
        self._update_music_player()

    def _on_bottom_seek_release(self, _event) -> None:
        self._bottom_seek_dragging = False
        if self.music_player.is_active():
            dur_sec = self.music_player.get_duration()
            percent = self.bottom_seek_var.get() / 100.0
            target_sec = max(0.0, dur_sec * percent)
            self.music_player.set_position(target_sec)
        self._update_music_player()

    def _on_bottom_volume_change(self, val) -> None:
        try:
            vol = float(val)
            self.music_player.set_volume(vol / 100.0)
            self.music_volume_var.set(vol)
            self.music_volume_label_var.set(f"{int(vol)}%")
        except Exception:
            pass

    def _open_lyrics_window(self) -> None:
        if self._lyrics_dialog is not None and self._lyrics_dialog.winfo_exists():
            self._lyrics_dialog.lift()
            self._lyrics_dialog.focus_force()
            self._lyrics_dialog.load_lyrics()
            return
        self._lyrics_dialog = LyricsDialog(
            self.root,
            self.music_player,
            self.text_family,
            self.display_family,
        )

    def _open_mini_player(self) -> None:
        if self._mini_player is not None and self._mini_player.winfo_exists():
            self._mini_player.lift()
            self._mini_player.focus_force()
            return
        self._mini_player = MiniPlayer(
            self.root,
            self.music_player,
            self.text_family,
            self.display_family,
            on_restore=lambda: self.root.deiconify(),
        )

    def _build_context_queue(self, parent, mode: str) -> None:
        title = "Fila de músicas" if mode == "music" else "Fila de vídeos"
        frame = ttk.LabelFrame(parent, text=title, padding=8)
        frame.pack(fill="x", pady=(8, 0))

        count = ttk.Label(frame, text="Nenhum item nesta fila.", foreground="#596579")
        count.pack(anchor="w", pady=(0, 4))
        self.context_queue_counts[mode] = count

        table = ttk.Frame(frame)
        table.pack(fill="x")
        tree = ttk.Treeview(
            table,
            columns=("title", "status"),
            show="headings",
            height=3,
            selectmode="browse",
        )
        tree.heading("title", text="Mídia")
        tree.heading("status", text="Situação")
        tree.column("title", width=430, minwidth=150, stretch=True, anchor="w")
        tree.column("status", width=130, minwidth=95, stretch=False, anchor="center")
        tree.grid(row=0, column=0, sticky="ew")
        table.columnconfigure(0, weight=1)
        ybar = ttk.Scrollbar(table, orient="vertical", command=tree.yview)
        ybar.grid(row=0, column=1, sticky="ns")
        tree.configure(yscrollcommand=ybar.set)
        tree.bind(
            "<<TreeviewSelect>>",
            lambda _event, selected_mode=mode: self._select_context_queue_item(selected_mode),
        )
        self.context_queue_trees[mode] = tree

        progress = ttk.Progressbar(
            frame,
            mode="determinate",
            maximum=100,
            variable=self.progress_value_var,
        )
        progress.pack(fill="x", pady=(6, 3))
        item_label = wrapping_label(
            frame,
            textvariable=self.download_item_var,
            font=(self.text_family, 9, "bold"),
        )
        metrics_label = wrapping_label(
            frame,
            textvariable=self.download_metrics_var,
            foreground="#596579",
        )
        progress.pack_configure(before=table)
        item_label.pack_configure(before=table)
        metrics_label.pack_configure(before=table)

        actions = ttk.Frame(frame)
        actions.pack(fill="x")
        download = ttk.Button(actions, text="Baixar / Continuar", command=self.start_download, style="Accent.TButton")
        download.pack(side="left")
        pause = ttk.Button(actions, text="⏸", width=3, command=self.toggle_pause, state="disabled")
        pause.pack(side="left", padx=(6, 0))
        stop = ttk.Button(actions, text="■", width=3, command=self.stop_queue, state="disabled")
        stop.pack(side="left", padx=(4, 0))
        ttk.Button(
            actions,
            text="Ver fila geral",
            command=lambda: self.tabs.select(self.queue_page),
        ).pack(side="right")
        add_tooltip(pause, "Pausar ou continuar o download")
        add_tooltip(stop, "Parar a fila mantendo os arquivos parciais")
        self.context_download_buttons.append(download)
        self.context_pause_buttons.append(pause)
        self.context_stop_buttons.append(stop)

    def _select_context_queue_item(self, mode: str) -> None:
        tree = self.context_queue_trees.get(mode)
        if tree is None or not tree.selection():
            return
        item_id = tree.selection()[0]
        if self.episode_tree.exists(item_id):
            self.episode_tree.selection_set(item_id)
            self.episode_tree.focus(item_id)
            self._show_episode_details()

    def _settings_variables(self) -> dict[str, object]:
        names = (
            "fragments_var", "cookies_browser_var", "cookies_file_var",
            "deezer_arl_var", "deezer_quality_var", "create_zip_var",
            "audio_bitrate_mode_var", "audio_custom_bitrate_var",
            "playlist_var", "music_structure_var", "music_filename_var",
            "subtitles_enabled_var", "subtitles_embed_var", "subtitles_auto_var", "subtitles_langs_var",
            "sponsorblock_var", "embed_chapters_var", "split_chapters_var",
            "rate_limit_var", "proxy_url_var", "clipboard_monitor_var",
            "preset_var", "post_download_action_var",
        )
        return {name: getattr(self, name).get() for name in names if hasattr(self, name)}

    def _open_settings(self) -> None:
        self._settings_snapshot = self._settings_variables()
        self.settings_dialog.deiconify()
        self.settings_dialog.lift()
        self.settings_dialog.focus_force()

    def _close_settings(self, save: bool) -> None:
        if save:
            self._save_preferences()
            self.queue_log("Configurações salvas.")
        else:
            for name, value in getattr(self, "_settings_snapshot", {}).items():
                getattr(self, name).set(value)
            self._update_audio_controls()
        self.settings_dialog.withdraw()

    def _build_audio_controls(self, parent, *, row: int, column: int) -> None:
        holder = ttk.Frame(parent)
        holder.grid(row=row, column=column, sticky="w", pady=3)
        combo = ttk.Combobox(
            holder,
            textvariable=self.audio_bitrate_mode_var,
            values=AUDIO_BITRATE_CHOICES,
            state="readonly",
            width=17,
        )
        combo.pack(side="left")
        custom = ttk.Spinbox(
            holder,
            textvariable=self.audio_custom_bitrate_var,
            from_=32,
            to=320,
            increment=1,
            width=5,
        )
        custom.pack(side="left", padx=(5, 2))
        unit = ttk.Label(holder, text="kbps")
        unit.pack(side="left")
        hint = ttk.Label(holder, text="", foreground="#596579")
        hint.pack(side="left", padx=(6, 0))
        if not hasattr(self, "audio_control_sets"):
            self.audio_control_sets = []
        self.audio_control_sets.append((combo, custom, unit, hint))
        if len(self.audio_control_sets) == 1:
            self.audio_bitrate_combo = combo
            self.audio_custom_bitrate = custom
            self.audio_custom_bitrate_unit = unit
            self.audio_bitrate_hint = hint

    def _build_site_logo(self, parent) -> ttk.Frame:
        return build_brand(parent, resource_path("assets/c2_logo_horizontal.png"), self.display_family)

    def _make_text(self, parent, height: int):
        from tkinter import Text

        return Text(parent, height=height, width=1, wrap="word", font=(self.text_family, 10),
                    relief="solid", borderwidth=1, padx=8, pady=6)

    def _update_audio_controls(self, *_args) -> None:
        format_choice = self.resolution_var.get()
        audio_selected = is_audio_format(format_choice)
        original_selected = format_choice == AUDIO_ORIGINAL_FORMAT
        if original_selected and self.audio_bitrate_mode_var.get() != AUDIO_AUTO_BITRATE:
            self.audio_bitrate_mode_var.set(AUDIO_AUTO_BITRATE)

        custom_enabled = (
            audio_selected
            and not original_selected
            and self.audio_bitrate_mode_var.get() == AUDIO_CUSTOM_BITRATE
        )
        if original_selected:
            hint_text = "Preserva a fonte."
        elif audio_selected:
            hint_text = "Automática = melhor qualidade."
        else:
            hint_text = "Somente para áudio."

        for combo, custom, unit, hint in getattr(self, "audio_control_sets", []):
            combo.configure(state="readonly" if audio_selected and not original_selected else "disabled")
            custom.configure(state="normal" if custom_enabled else "disabled")
            unit.configure(state="normal" if custom_enabled else "disabled")
            hint.configure(text=hint_text)

    def _on_resolution_var_changed(self, *_args) -> None:
        val = self.resolution_var.get()
        if getattr(self, "work_mode", "video") == "music":
            if hasattr(self, "music_format_var") and self.music_format_var.get() != val and val in MUSIC_DOWNLOAD_FORMATS:
                self.music_format_var.set(val)
        else:
            if hasattr(self, "video_format_var") and self.video_format_var.get() != val and val in VIDEO_DOWNLOAD_FORMATS:
                self.video_format_var.set(val)
        self._update_audio_controls()

    def _on_folder_var_changed(self, *_args) -> None:
        val = self.folder_var.get()
        if getattr(self, "work_mode", "video") == "music":
            if hasattr(self, "music_folder_var") and self.music_folder_var.get() != val:
                self.music_folder_var.set(val)
        else:
            if hasattr(self, "video_folder_var") and self.video_folder_var.get() != val:
                self.video_folder_var.set(val)

    def _on_music_format_selected(self, _event=None) -> None:
        self.resolution_var.set(self.music_format_var.get())
        self._update_audio_controls()
        self._save_preferences()

    def _on_video_format_selected(self, _event=None) -> None:
        self.resolution_var.set(self.video_format_var.get())
        self._update_audio_controls()
        self._save_preferences()

    def _apply_work_mode(self, mode: str, *, initial: bool = False) -> None:
        mode = "music" if mode == "music" else "video"
        self.work_mode = mode
        if mode == "music":
            self.resolution_var.set(
                self.music_format_var.get()
                if hasattr(self, "music_format_var")
                else "Apenas áudio (MP3)"
            )
            if hasattr(self, "music_folder_var"):
                self.folder_var.set(self.music_folder_var.get())
            if hasattr(self, "analyze_button"):
                self.analyze_button.configure(text="Atualizar fila")
            if not initial:
                self.download_item_var.set("Modo Música")
                self.download_metrics_var.set("Selecione um resultado da pesquisa para carregar na fila.")
        else:
            self.resolution_var.set(
                self.video_format_var.get()
                if hasattr(self, "video_format_var")
                else "Melhor MP4 compatível"
            )
            if hasattr(self, "video_folder_var"):
                self.folder_var.set(self.video_folder_var.get())
            if hasattr(self, "analyze_button"):
                self.analyze_button.configure(text="Listar links")
            if not initial:
                self.download_item_var.set("Modo Vídeo")
                self.download_metrics_var.set("Cole links de vídeos ou playlists para começar.")
        self._update_audio_controls()

    def _schedule_clipboard_tick(self) -> None:
        try:
            if hasattr(self, "clipboard_monitor_var") and self.clipboard_monitor_var.get():
                self._check_clipboard_for_media()
        except Exception:
            pass
        finally:
            if not getattr(self, "_music_search_shutdown", None) or not self._music_search_shutdown.is_set():
                try:
                    self._clipboard_after_id = self.root.after(1500, self._schedule_clipboard_tick)
                except Exception:
                    pass

    def _check_clipboard_for_media(self) -> None:
        if not hasattr(self, "clipboard_monitor_var") or not self.clipboard_monitor_var.get():
            return
        try:
            text = self.root.clipboard_get().strip()
        except Exception:
            return
        if not text or text == getattr(self, "_last_clipboard_url", None):
            return

        is_media_url = text.startswith(("http://", "https://", "deezer:", "spotify:"))
        if not is_media_url:
            return

        known_patterns = (
            "youtube.com", "youtu.be", "instagram.com", "tiktok.com",
            "facebook.com", "fb.watch", "deezer.com", "deezer:",
            "link.deezer.com", "deezer.page.link", "spotify.com", "spotify:",
            "kanald.com.tr", "jw.org", "twitter.com", "x.com",
            "vimeo.com", "twitch.tv", "soundcloud.com", "reddit.com",
            "dailymotion.com", "bilibili.com"
        )
        lower = text.lower()
        if not any(pattern in lower for pattern in known_patterns) and not lower.endswith(tuple(VIDEO_EXTENSIONS)):
            return

        self._last_clipboard_url = text
        if hasattr(self, "video_url_text"):
            current = self.video_url_text.get("1.0", "end").strip()
            if text not in current:
                if not current:
                    self.video_url_text.insert("1.0", text)
                else:
                    self.video_url_text.insert("end", f"\n{text}")
                self.queue_log(f"Área de transferência: link detectado e adicionado ({text[:60]}...)")
                self._schedule_video_preview_check()

    def _schedule_video_preview_check(self, _event=None) -> None:
        if self._video_preview_after is not None:
            try:
                self.root.after_cancel(self._video_preview_after)
            except Exception:
                pass
        self._video_preview_after = self.root.after(350, self._check_video_preview)

    def _check_video_preview(self) -> None:
        self._video_preview_after = None
        raw_text = self.video_url_text.get("1.0", "end").strip()
        urls = [line.strip() for line in raw_text.splitlines() if line.strip() and "://" in line.strip()]
        if not urls:
            self._last_video_preview_url = None
            self.video_preview_title_var.set("Cole um link de vídeo acima para ver os detalhes")
            self.video_preview_author_var.set("")
            self.video_cover_label.configure(image="", text="Sem capa")
            self._video_cover_image = None
            return

        target_url = urls[0]
        if self._last_video_preview_url == target_url:
            return
        self._last_video_preview_url = target_url
        self.video_preview_title_var.set("Carregando informações do vídeo...")
        self.video_preview_author_var.set(target_url)
        self.video_cover_label.configure(image="", text="Carregando capa...")
        self._video_cover_image = None

        yt_dlp = None
        if hasattr(self, "dependencies") and hasattr(self.dependencies, "yt_dlp_path"):
            yt_dlp = self.dependencies.yt_dlp_path

        def worker(url: str, engine_path):
            info = fetch_video_preview_info(url, engine_path)
            self.event_queue.put(("video_preview_ready", (url, info)))

        threading.Thread(target=worker, args=(target_url, yt_dlp), daemon=True).start()

    def _apply_video_preview(self, payload: object) -> None:
        if not isinstance(payload, tuple) or len(payload) != 2:
            return
        url, info = payload
        if url != self._last_video_preview_url or not isinstance(info, dict):
            return
        title = str(info.get("title") or "Vídeo detectado")
        author = str(info.get("author") or "")
        self.video_preview_title_var.set(title)
        self.video_preview_author_var.set(author)

        cover_data = info.get("cover_data")
        if not cover_data:
            self.video_cover_label.configure(image="", text="Sem capa")
            self._video_cover_image = None
            return
        try:
            from io import BytesIO
            from PIL import Image, ImageTk

            image = Image.open(BytesIO(cover_data))
            image.thumbnail((160, 90))
            self._video_cover_image = ImageTk.PhotoImage(image)
            self.video_cover_label.configure(image=self._video_cover_image, text="")
        except Exception:
            self._video_cover_image = None
            self.video_cover_label.configure(image="", text="Capa indisponível")

    def _video_url_from_input(self) -> str | None:
        raw_text = self.video_url_text.get("1.0", "end").strip()
        return next(
            (line.strip() for line in raw_text.splitlines() if line.strip().startswith(("http://", "https://"))),
            None,
        )

    def _selected_video_item(self, *, completed: bool = False):
        tree = self.completed_tree if completed else self.episode_tree
        selection = tree.selection()
        return next(
            (item for item in self.queue_items if selection and item.get("id") == selection[0]),
            None,
        )

    @staticmethod
    def _local_video_for_item(item) -> Path | None:
        if not item:
            return None
        return next(
            (
                path for path in (Path(value) for value in item.get("files", []))
                if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS
            ),
            None,
        )

    def _is_completed_playable(self, item) -> bool:
        if not item:
            return False
        if item.get("kind") in {"deezer_preview", "deezer_full"}:
            return True
        return self._local_video_for_item(item) is not None

    def play_completed_media(self) -> None:
        item = self._selected_video_item(completed=True)
        if item and item.get("kind") in {"deezer_preview", "deezer_full"}:
            self.play_selected_music(completed=True)
        else:
            self.play_selected_video(completed=True)

    def stop_completed_media(self) -> None:
        if self.video_player.is_active() or self.video_player.source:
            self.stop_video()
        else:
            self.stop_music()

    def play_selected_video(self, *, completed: bool = False) -> None:
        item = self._selected_video_item(completed=completed)
        local = self._local_video_for_item(item)
        page_url = None if completed else str((item or {}).get("source") or "").strip()
        if not page_url.startswith(("http://", "https://")):
            page_url = None if completed else self._video_url_from_input()
        source_key = str(local) if local is not None else page_url
        if not source_key:
            messagebox.showinfo(
                APP_NAME,
                "Selecione um vídeo concluído ou cole um link na aba Vídeo.",
            )
            return

        if self._video_source_key == source_key and self.video_player.is_playing():
            self.video_player.pause()
            self.video_player_status_var.set("Reprodução pausada.")
            self._update_video_player()
            return
        if self._video_source_key == source_key and self.video_player.is_paused():
            self.video_player.resume()
            self.video_player_status_var.set("Reproduzindo.")
            self._update_video_player()
            return

        request_id = uuid.uuid4().hex
        self._video_resolve_request = request_id
        self._video_source_key = source_key
        self.video_play_button.configure(state="disabled")
        self.video_player_status_var.set(
            "Abrindo arquivo local..." if local is not None else "Localizando a transmissão do vídeo..."
        )

        if local is not None:
            self.event_queue.put(("video_source_ready", (request_id, str(local), source_key)))
            return

        def worker(url: str, token: str, key: str) -> None:
            try:
                stream = resolve_video_stream(
                    url,
                    self.dependencies.yt_dlp_path,
                    self.dependencies.runtime_environment(),
                    self._capture_options(),
                )
                self.event_queue.put(("video_source_ready", (token, stream, key)))
            except Exception as exc:
                self.event_queue.put(("video_player_error", (token, str(exc))))

        threading.Thread(target=worker, args=(page_url, request_id, source_key), daemon=True).start()

    def _start_video_source(self, payload: object) -> None:
        if not isinstance(payload, tuple) or len(payload) != 3:
            return
        request_id, source, source_key = payload
        if request_id != self._video_resolve_request:
            return
        try:
            self.music_player.stop()
            self._playing_item_id = None
            self._catalog_playing_active = False
            self.video_surface.update_idletasks()
            self.video_player.play(str(source), self.video_surface.winfo_id())
            self.video_player.set_volume(self.video_volume_var.get())
            self._video_source_key = source_key
            self.video_player_status_var.set("Reproduzindo.")
        except Exception as exc:
            self._video_source_key = None
            self.video_player_status_var.set("Não foi possível reproduzir o vídeo.")
            messagebox.showerror(APP_NAME, str(exc))
        finally:
            self.video_play_button.configure(state="normal")
            self._update_video_player()

    def _handle_video_player_error(self, payload: object) -> None:
        request_id, message = payload if isinstance(payload, tuple) and len(payload) == 2 else (None, payload)
        if request_id is not None and request_id != self._video_resolve_request:
            return
        self._video_source_key = None
        self.video_play_button.configure(state="normal")
        self.video_player_status_var.set("Não foi possível abrir este vídeo.")
        messagebox.showerror(APP_NAME, str(message))

    def stop_video(self) -> None:
        try:
            self._video_resolve_request = None
            self.video_player.stop()
            self._video_source_key = None
            self.video_player_seek_var.set(0)
            self.video_player_time_var.set("00:00 / 00:00")
            self.video_player_status_var.set("Reprodução interrompida.")
            self._update_video_player()
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Não foi possível parar o vídeo:\n{exc}")

    def _begin_video_seek(self, _event=None) -> None:
        self._video_seek_dragging = True

    def _finish_video_seek(self, _event=None) -> None:
        self._video_seek_dragging = False
        if self.video_player.is_active():
            self.video_player.set_position(self.video_player_seek_var.get() / 100)

    def _set_video_volume(self, value) -> None:
        try:
            self.video_player.set_volume(float(value))
        except (TypeError, ValueError):
            pass

    def _update_video_player(self) -> None:
        active = self.video_player.is_active()
        playing = self.video_player.is_playing()
        if hasattr(self, "video_play_button"):
            self.video_play_button.configure(text="⏸" if playing else "▶")
        if hasattr(self, "video_stop_button"):
            self.video_stop_button.configure(state="normal" if active else "disabled")
        if hasattr(self, "play_completed_button") and active:
            self.play_completed_button.configure(text="⏸" if playing else "▶")
        if hasattr(self, "stop_completed_button"):
            self.stop_completed_button.configure(
                state="normal" if active or self.music_player.is_active() else "disabled"
            )
        if active:
            elapsed = self.video_player.get_time()
            total = self.video_player.get_length()
            if not self._video_seek_dragging:
                position = self.video_player.get_position()
                self.video_player_seek_var.set(position * 100)
            self.video_player_time_var.set(
                f"{format_player_time(elapsed)} / {format_player_time(total)}"
            )

    def _begin_music_seek(self, _event=None) -> None:
        self._music_seek_dragging = True

    def _finish_music_seek(self, _event=None) -> None:
        self._music_seek_dragging = False
        if self.music_player.is_active():
            dur_sec = self.music_player.get_duration()
            percent = self.music_player_seek_var.get() / 100.0
            target_sec = dur_sec * percent if dur_sec > 0 else 0.0
            self.music_player.set_position(target_sec)

    def _set_music_volume(self, value) -> None:
        try:
            val = float(value)
            self.music_volume_label_var.set(f"{int(round(val))}%")
            self.music_player.set_volume(val / 100.0)
            if val > 0:
                self._music_is_muted = False
                if hasattr(self, "audio_mute_button"):
                    self.audio_mute_button.configure(text="🔊")
            else:
                self._music_is_muted = True
                if hasattr(self, "audio_mute_button"):
                    self.audio_mute_button.configure(text="🔇")
        except (TypeError, ValueError):
            pass

    def _toggle_music_mute(self) -> None:
        if self._music_is_muted:
            self._music_is_muted = False
            restored = self._music_prev_volume if self._music_prev_volume > 0 else 80.0
            self.music_volume_var.set(restored)
            self._set_music_volume(restored)
        else:
            self._music_prev_volume = self.music_volume_var.get()
            self._music_is_muted = True
            self.music_volume_var.set(0)
            self._set_music_volume(0)

    def _toggle_audio_player_playback(self) -> None:
        if self.music_player.is_playing():
            self.music_player.pause()
            self.music_player_status_var.set("Pausado")
            self._update_player_buttons()
            self._update_music_player()
            return
        if self.music_player.is_paused():
            self.music_player.resume()
            self.music_player_status_var.set("Reproduzindo...")
            self._update_player_buttons()
            self._update_music_player()
            return
        result = self._selected_catalog_result() if hasattr(self, "_selected_catalog_result") else None
        if result is not None and result.kind == "track":
            self._toggle_catalog_playback()
            return
        item = self._selected_music_item() or self._selected_music_item(completed=True)
        if item:
            self.play_selected_music()
            return
        self.open_local_audio_file()

    def open_local_audio_file(self) -> None:
        from tkinter import filedialog

        file_path = filedialog.askopenfilename(
            title="Escolha um arquivo de áudio para reproduzir",
            filetypes=[
                ("Arquivos de Áudio", "*.mp3 *.flac *.wav *.ogg"),
                ("MP3", "*.mp3"),
                ("FLAC", "*.flac"),
                ("WAV", "*.wav"),
                ("OGG", "*.ogg"),
                ("Todos os arquivos", "*.*"),
            ],
        )
        if not file_path:
            return
        path = Path(file_path)
        if not path.is_file():
            return
        if self.video_player.is_active() or self.video_player.source:
            self.stop_video()
        try:
            mode = self.music_player.play_file(path, title=path.stem)
            self._playing_item_id = f"local_{path.name}"
            self._catalog_playing_active = False
            self.music_player_track_var.set(f"🎵 {path.name}")
            self.music_player_status_var.set("Arquivo local" if mode == "internal" else "Player externo")
            self._update_player_buttons()
            self._update_music_player()
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Não foi possível abrir o arquivo:\n{exc}")

    def _update_music_player(self) -> None:
        active = self.music_player.is_active()
        playing = self.music_player.is_playing()
        paused = self.music_player.is_paused()

        if hasattr(self, "audio_play_button"):
            self.audio_play_button.configure(text="⏸" if playing else "▶")
        if hasattr(self, "audio_stop_button"):
            self.audio_stop_button.configure(state="normal" if active else "disabled")
        if hasattr(self, "audio_seek_scale"):
            self.audio_seek_scale.configure(state="normal" if active else "disabled")

        if hasattr(self, "bottom_play_button"):
            self.bottom_play_button.configure(text="⏸" if playing else "▶")
        if hasattr(self, "bottom_stop_button"):
            self.bottom_stop_button.configure(state="normal" if active else "disabled")
        if hasattr(self, "bottom_seek_scale"):
            self.bottom_seek_scale.configure(state="normal" if active else "disabled")

        if active:
            pos_ms = self.music_player.get_position_ms()
            dur_ms = self.music_player.get_duration_ms()
            if not self._music_seek_dragging:
                if dur_ms > 0:
                    pct = (pos_ms / dur_ms) * 100.0
                    self.music_player_seek_var.set(min(100.0, max(0.0, pct)))
                else:
                    self.music_player_seek_var.set(0.0)
            if not getattr(self, "_bottom_seek_dragging", False):
                if dur_ms > 0:
                    pct = (pos_ms / dur_ms) * 100.0
                    self.bottom_seek_var.set(min(100.0, max(0.0, pct)))
                else:
                    self.bottom_seek_var.set(0.0)

            self.music_player_time_var.set(
                f"{format_player_time(pos_ms)} / {format_player_time(dur_ms)}"
            )
            rem_ms = max(0, dur_ms - pos_ms)
            self.bottom_time_var.set(
                f"{format_player_time(pos_ms)} / -{format_player_time(rem_ms)}"
            )

            title = self.music_player.track_title
            artist = self.music_player.track_artist
            album = self.music_player.track_album
            if title and artist:
                display_track = f"{title} • {artist}"
            elif title:
                display_track = title
            else:
                display_track = "Reproduzindo áudio"
            self.music_player_track_var.set(f"🎵 {display_track}")

            self.bottom_track_var.set(title or "Reproduzindo áudio")
            self.bottom_artist_var.set(f"{artist} — {album}" if album and artist else (artist or album or "C² Downloader"))

            # Quality & Format Badge
            current_path = getattr(self.music_player, "_current_path", None)
            current_src = str(self.music_player.current_source or "")
            if current_src.startswith("deezer_full_"):
                ext = Path(current_path).suffix.lower() if current_path else ""
                if ext == ".flac":
                    self.bottom_badge_var.set("💎 LOSSLESS (FLAC)")
                else:
                    self.bottom_badge_var.set("⚡ FAIXA COMPLETA (320k)")
            elif current_path:
                ext = Path(current_path).suffix.lower()
                if ext == ".flac":
                    self.bottom_badge_var.set("💎 LOSSLESS")
                elif ext == ".mp3":
                    self.bottom_badge_var.set("⚡ 320k")
                elif ext in {".m4a", ".aac"}:
                    self.bottom_badge_var.set("🎵 AAC")
                else:
                    self.bottom_badge_var.set(ext.upper().replace(".", ""))
            elif is_public_deezer_preview(current_src):
                self.bottom_badge_var.set("📱 PRÉVIA (30s)")
            else:
                self.bottom_badge_var.set("")

            if paused:
                self.music_player_status_var.set("Pausado")
            elif playing:
                self.music_player_status_var.set("Reproduzindo...")
        else:
            if not self._music_seek_dragging:
                self.music_player_seek_var.set(0.0)
            if not getattr(self, "_bottom_seek_dragging", False):
                self.bottom_seek_var.set(0.0)
            self.music_player_time_var.set("00:00 / 00:00")
            self.bottom_time_var.set("00:00 / 00:00")
            if not self.music_player.current_source:
                self.music_player_track_var.set("Nenhuma faixa em reprodução.")
                self.music_player_status_var.set("Pronto para reproduzir.")
                self.bottom_track_var.set("Nenhuma faixa em reprodução")
                self.bottom_artist_var.set("C² Downloader")
                self.bottom_badge_var.set("")

    def _on_main_tab_changed(self, _event=None) -> None:
        selected = self.tabs.select()
        if selected == str(self.music_page):
            if self.work_mode != "music":
                self._apply_work_mode("music")
        elif selected == str(self.video_page):
            if self.work_mode != "video":
                self._apply_work_mode("video")
        self._sync_sidebar_selection()
        self._save_preferences()

    def _set_source_text(self, sources: list[str]) -> None:
        values = [str(source).strip() for source in sources if str(source).strip()]
        if not values:
            return
        music = all(
            value.lower().startswith("deezer:")
            or "deezer.com/" in value.lower()
            or "://" not in value
            for value in values
        )
        if music:
            self._apply_work_mode("music", initial=True)
            self.tabs.select(self.music_page)
            self.music_search_var.set(values[0])
        else:
            self._apply_work_mode("video", initial=True)
            self.tabs.select(self.video_page)
            self.video_url_text.delete("1.0", END)
            self.video_url_text.insert("1.0", "\n".join(values))

    def _clear_music_search_results(self) -> None:
        if not hasattr(self, "music_results_tree"):
            return
        for item_id in self.music_results_tree.get_children():
            self.music_results_tree.delete(item_id)
        self._music_search_results = []
        self.catalog_title_var.set("Selecione um resultado")
        self.catalog_subtitle_var.set("")
        self.catalog_type_var.set("")
        self.catalog_load_button.configure(state="normal")
        if hasattr(self, "catalog_download_button"):
            self.catalog_download_button.configure(state="normal")
        self.catalog_play_button.configure(state="normal", text="▶")
        if hasattr(self, "catalog_stop_button"):
            self.catalog_stop_button.configure(state="normal" if self.music_player.is_active() else "disabled")
        self.catalog_open_button.configure(state="normal")
        self.catalog_cover_label.configure(image="", text="Sem capa")
        self._catalog_cover_image = None
        self._catalog_cover_key = None

    def _schedule_music_search(self, *_args) -> None:
        if self._music_search_after is not None:
            try:
                self.root.after_cancel(self._music_search_after)
            except Exception:
                pass
            self._music_search_after = None

        query = self.music_search_var.get().strip()
        if not query:
            self._clear_music_search_results()
            self.music_search_status_var.set("Digite para pesquisar.")
            return
        if query.lower().startswith(("http://", "https://", "spotify:", "deezer:")):
            self._clear_music_search_results()
            if is_deezer_url(query):
                self.music_search_status_var.set("Link Deezer detectado. Pressione Enter para carregar na fila.")
            elif is_spotify_url(query):
                self.music_search_status_var.set("Link Spotify detectado. Pressione Enter para carregar na fila.")
            else:
                self.music_search_status_var.set("Na aba Música, use nome de música, artista, álbum, playlist ou link Deezer / Spotify.")
            return
        if len(query) < 2:
            self._clear_music_search_results()
            self.music_search_status_var.set("Digite pelo menos 2 caracteres.")
            return

        self.music_search_status_var.set("Pesquisando...")
        self._music_search_after = self.root.after(250, self._start_music_search)

    def _start_music_search(self) -> None:
        self._music_search_after = None
        query = self.music_search_var.get().strip()
        if len(query) < 2 or query.lower().startswith(("http://", "https://", "spotify:", "deezer:")):
            return

        kind_map = {
            "Música": "track",
            "Artista": "artist",
            "Álbum": "album",
            "Playlist": "playlist",
        }
        kind = kind_map.get(self.music_search_type_var.get(), "track")
        self._music_search_generation += 1
        generation = self._music_search_generation
        cache_key = (kind, query.casefold())
        cached = self._music_search_cache.get(cache_key)
        if cached and time.monotonic() - cached[0] < 600:
            self.event_queue.put(("music_search_results", {
                "generation": generation,
                "query": query,
                "results": cached[1],
                "error": "",
                "cached": True,
                "elapsed": 0.0,
            }))
            return

        started = time.monotonic()

        def worker():
            if generation != self._music_search_generation:
                return
            try:
                results = search_deezer_catalog(query, kind=kind, limit=15)
                self._music_search_cache[cache_key] = (time.monotonic(), results)
                self.event_queue.put(("music_search_results", {
                    "generation": generation,
                    "query": query,
                    "results": results,
                    "error": "",
                    "cached": False,
                    "elapsed": time.monotonic() - started,
                }))
            except Exception as exc:
                self.event_queue.put(("music_search_results", {
                    "generation": generation,
                    "query": query,
                    "results": (),
                    "error": str(exc),
                    "cached": False,
                    "elapsed": time.monotonic() - started,
                }))

        self._music_search_tasks.put(worker)

    def _music_search_worker_loop(self) -> None:
        while not self._music_search_shutdown.is_set():
            try:
                task = self._music_search_tasks.get(timeout=0.25)
            except queue.Empty:
                continue
            if task is None:
                self._music_search_tasks.task_done()
                return
            try:
                task()
            finally:
                self._music_search_tasks.task_done()

    def _handle_music_search_results(self, payload: object) -> None:
        if not isinstance(payload, dict):
            return
        if int(payload.get("generation") or 0) != self._music_search_generation:
            return

        self._clear_music_search_results()
        error = str(payload.get("error") or "")
        if error:
            self.music_search_status_var.set(f"Falha na pesquisa: {error}")
            return

        results = list(payload.get("results") or [])
        self._music_search_results = results
        for index, result in enumerate(results):
            if not isinstance(result, DeezerSearchResult):
                continue
            self.music_results_tree.insert(
                "",
                END,
                iid=str(index),
                values=(result.kind_label, result.title, result.subtitle),
            )
        count = len(results)
        elapsed = float(payload.get("elapsed") or 0.0)
        source = "cache" if payload.get("cached") else f"{elapsed:.1f}s".replace(".", ",")
        self.music_search_status_var.set(
            f"{count} resultado(s) • {source}. Selecione ou dê duplo clique para carregar."
            if count
            else "Nenhum resultado encontrado."
        )
        if count:
            self.music_results_tree.selection_set("0")
            self.music_results_tree.focus("0")
            self._show_selected_catalog_result()

    def _selected_catalog_result(self) -> DeezerSearchResult | None:
        selected = self.music_results_tree.selection()
        if not selected:
            return None
        try:
            index = int(selected[0])
        except (TypeError, ValueError):
            return None
        if 0 <= index < len(self._music_search_results):
            result = self._music_search_results[index]
            return result if isinstance(result, DeezerSearchResult) else None
        return None

    def _show_selected_catalog_result(self, _event=None) -> None:
        result = self._selected_catalog_result()
        if result is None:
            self.catalog_load_button.configure(state="normal")
            if hasattr(self, "catalog_download_button"):
                self.catalog_download_button.configure(state="normal")
            if hasattr(self, "catalog_drill_button"):
                self.catalog_drill_button.configure(state="normal", text="💿 Ver Álbum")
            if hasattr(self, "catalog_album_download_button"):
                self.catalog_album_download_button.configure(state="normal")
            if hasattr(self, "catalog_tracklist_button"):
                self.catalog_tracklist_button.configure(state="normal", text="☑️ Escolher Faixas...")
            if hasattr(self, "catalog_artist_albums_button"):
                self.catalog_artist_albums_button.configure(state="normal")
            if hasattr(self, "catalog_analyzer_button"):
                self.catalog_analyzer_button.configure(state="normal")
            self.catalog_play_button.configure(state="normal", text="▶")
            if hasattr(self, "catalog_stop_button"):
                self.catalog_stop_button.configure(state="normal" if self.music_player.is_active() else "disabled")
            self.catalog_open_button.configure(state="normal")
            return

        self.catalog_title_var.set(result.title)
        self.catalog_subtitle_var.set(result.subtitle)
        self.catalog_type_var.set(result.kind_label)
        self.catalog_load_button.configure(state="normal")
        self.catalog_download_button.configure(state="normal")
        self.catalog_open_button.configure(state="normal")

        if hasattr(self, "catalog_drill_button"):
            if result.kind == "album":
                self.catalog_drill_button.configure(state="normal", text="💿 Ver Faixas do Álbum")
            elif result.kind == "artist":
                self.catalog_drill_button.configure(state="normal", text="🎵 Top Músicas")
            elif result.kind == "track":
                self.catalog_drill_button.configure(state="normal", text="💿 Ver Álbum")
            else:
                self.catalog_drill_button.configure(state="normal", text="💿 Ver Álbum")

        if hasattr(self, "catalog_album_download_button"):
            self.catalog_album_download_button.configure(state="normal")

        if hasattr(self, "catalog_tracklist_button"):
            if result.kind == "album":
                self.catalog_tracklist_button.configure(state="normal", text="☑️ Escolher Faixas...")
            elif result.kind == "track":
                self.catalog_tracklist_button.configure(state="normal", text="☑️ Escolher Faixas do Álbum...")
            else:
                self.catalog_tracklist_button.configure(state="normal", text="☑️ Escolher Faixas...")

        if hasattr(self, "catalog_analyzer_button"):
            self.catalog_analyzer_button.configure(state="normal")

        if hasattr(self, "catalog_artist_albums_button"):
            self.catalog_artist_albums_button.configure(state="normal")

        catalog_id = f"catalog_{result.item_id}"
        is_current = getattr(self, "_playing_item_id", None) == catalog_id
        is_playing = is_current and self.music_player.is_playing()
        self.catalog_play_button.configure(
            state="normal",
            text="⏸" if is_playing else "▶",
        )
        if hasattr(self, "catalog_stop_button"):
            can_stop = self.music_player.is_active() or result.kind == "track"
            self.catalog_stop_button.configure(state="normal" if can_stop else "disabled")
        self.catalog_cover_label.configure(image="", text="Carregando capa...")
        self._catalog_cover_image = None
        self._catalog_cover_key = result.page_url

        def worker():
            try:
                data = cover_bytes_for_item({"cover_url": result.cover_url})
            except Exception:
                data = None
            self.event_queue.put(("catalog_cover_ready", (result.page_url, data)))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_catalog_cover(self, payload: object) -> None:
        if not isinstance(payload, tuple) or len(payload) != 2:
            return
        key, data = payload
        if key != self._catalog_cover_key:
            return
        if not data:
            self.catalog_cover_label.configure(image="", text="Sem capa")
            return
        try:
            from io import BytesIO
            from PIL import Image, ImageTk

            image = Image.open(BytesIO(data))
            image.thumbnail((150, 150))
            self._catalog_cover_image = ImageTk.PhotoImage(image)
            self.catalog_cover_label.configure(image=self._catalog_cover_image, text="")
        except Exception:
            self._catalog_cover_image = None
            self.catalog_cover_label.configure(image="", text="Capa indisponível")

    def _load_selected_catalog_result(self) -> None:
        result = self._selected_catalog_result()
        if result is None:
            query = self.music_search_var.get().strip()
            if query and (is_deezer_url(query) or is_spotify_url(query)):
                self._apply_work_mode("music", initial=True)
                self._prepare_sources([query], False)
                return
            if self._music_search_results:
                result = self._music_search_results[0]
            else:
                self.music_search_status_var.set("Selecione um item da lista ou digite um link para adicionar à fila.")
                return
        self._apply_work_mode("music", initial=True)
        self._prepare_sources([result.page_url], False)

    def _download_selected_catalog_result(self) -> None:
        result = self._selected_catalog_result()
        if result is None:
            query = self.music_search_var.get().strip()
            if query and (is_deezer_url(query) or is_spotify_url(query)):
                self._apply_work_mode("music", initial=True)
                self._prepare_sources([query], True)
                return
            if self._music_search_results:
                result = self._music_search_results[0]
            else:
                self.music_search_status_var.set("Selecione uma faixa da lista ou digite um link para baixar.")
                return
        self._apply_work_mode("music", initial=True)
        self._prepare_sources([result.page_url], True)

    def _save_search_state_before_drill(self) -> None:
        if not hasattr(self, "_previous_music_search_stack"):
            self._previous_music_search_stack = []
        if self._music_search_results:
            title = self.music_search_var.get().strip() or "Busca"
            self._previous_music_search_stack.append((title, list(self._music_search_results)))
        if hasattr(self, "catalog_back_button"):
            self.catalog_back_button.configure(state="normal")

    def _restore_previous_music_search(self) -> None:
        if not getattr(self, "_previous_music_search_stack", None):
            return
        prev_title, prev_results = self._previous_music_search_stack.pop()
        self._clear_music_search_results()
        self._music_search_results = list(prev_results)
        for index, result in enumerate(prev_results):
            self.music_results_tree.insert(
                "",
                END,
                iid=str(index),
                values=(result.kind_label, result.title, result.subtitle),
            )
        count = len(prev_results)
        self.music_search_status_var.set(f"Retornado a: {prev_title} ({count} resultados).")
        if count:
            self.music_results_tree.selection_set("0")
            self.music_results_tree.focus("0")
            self._show_selected_catalog_result()
        if hasattr(self, "catalog_back_button"):
            self.catalog_back_button.configure(
                state="normal" if getattr(self, "_previous_music_search_stack", None) else "disabled"
            )

    def _on_music_result_double_click(self, _event=None) -> None:
        result = self._selected_catalog_result()
        if result is None:
            return
        if result.kind == "album":
            self._drill_down_album(result.item_id, result.title)
        elif result.kind == "artist":
            self._drill_down_artist_top(result.item_id, result.title)
        else:
            self._load_selected_catalog_result()

    def _drill_down_selected(self) -> None:
        self._view_album_for_selected()

    def _view_album_for_selected(self) -> None:
        result = self._selected_catalog_result()
        if not result:
            if self._music_search_results:
                result = self._music_search_results[0]
            else:
                self.music_search_status_var.set("Selecione uma faixa ou álbum para ver suas músicas.")
                return
        if result.kind == "album":
            self._drill_down_album(result.item_id, result.title)
        elif result.kind == "artist":
            self._drill_down_artist_top(result.item_id, result.title)
        elif result.kind == "track":
            album_id = getattr(result, "album_id", None)
            album_title = getattr(result, "album_title", None) or "Álbum"
            if album_id:
                self._drill_down_album(album_id, album_title)
            else:
                self.music_search_status_var.set("Buscando informações do álbum desta faixa...")
                def worker():
                    from deezer_catalog import get_track_album_info
                    info = get_track_album_info(result.item_id)
                    if info:
                        alb_id, alb_name = info
                        self.root.after(0, lambda: self._drill_down_album(alb_id, alb_name))
                    else:
                        self.event_queue.put(("music_player_error", "Álbum não encontrado para esta faixa."))
                threading.Thread(target=worker, daemon=True).start()

    def _download_album_for_selected(self) -> None:
        result = self._selected_catalog_result()
        if not result:
            if self._music_search_results:
                result = self._music_search_results[0]
            else:
                self.music_search_status_var.set("Selecione uma faixa ou álbum para baixar o álbum completo.")
                return
        if result.kind == "album":
            self._apply_work_mode("music", initial=True)
            self._prepare_sources([result.page_url], True)
        elif result.kind == "track":
            album_id = getattr(result, "album_id", None)
            if album_id:
                self._apply_work_mode("music", initial=True)
                self._prepare_sources([f"https://www.deezer.com/album/{album_id}"], True)
            else:
                self.music_search_status_var.set("Identificando álbum completo para download...")
                def worker():
                    from deezer_catalog import get_track_album_info
                    info = get_track_album_info(result.item_id)
                    if info:
                        alb_id, _ = info
                        self.root.after(0, lambda: (
                            self._apply_work_mode("music", initial=True),
                            self._prepare_sources([f"https://www.deezer.com/album/{alb_id}"], True),
                        ))
                    else:
                        self.event_queue.put(("music_player_error", "Álbum não encontrado para esta faixa."))
                threading.Thread(target=worker, daemon=True).start()

    def _tracklist_for_selected(self) -> None:
        result = self._selected_catalog_result()
        if not result:
            if self._music_search_results:
                result = self._music_search_results[0]
            else:
                self.music_search_status_var.set("Selecione um álbum ou faixa para escolher suas faixas.")
                return
        if result.kind == "album":
            self._open_album_tracklist_dialog_for_id(result.item_id, result.title)
        elif result.kind == "track":
            album_id = getattr(result, "album_id", None)
            album_title = getattr(result, "album_title", None) or "Álbum"
            if album_id:
                self._open_album_tracklist_dialog_for_id(album_id, album_title)
            else:
                self.music_search_status_var.set("Carregando lista de faixas do álbum...")
                def worker():
                    from deezer_catalog import get_track_album_info
                    info = get_track_album_info(result.item_id)
                    if info:
                        alb_id, alb_name = info
                        self.root.after(0, lambda: self._open_album_tracklist_dialog_for_id(alb_id, alb_name))
                    else:
                        self.event_queue.put(("music_player_error", "Álbum não encontrado para esta faixa."))
                threading.Thread(target=worker, daemon=True).start()

    def _drill_down_album(self, album_id: str, album_title: str) -> None:
        self._save_search_state_before_drill()
        self.music_search_status_var.set(f"Carregando faixas do álbum '{album_title}'...")

        def worker():
            try:
                tracks = get_album_tracks(album_id)
                results = tuple(
                    DeezerSearchResult(
                        kind="track",
                        item_id=t.track_id,
                        title=t.title,
                        subtitle=f"{t.artist} • {album_title}",
                        cover_url=t.cover_url,
                        page_url=t.page_url,
                        album_id=album_id,
                        album_title=album_title,
                    ) for t in tracks
                )
                self.event_queue.put(("music_drill_results", (f"Álbum: {album_title}", results, "")))
            except Exception as exc:
                self.event_queue.put(("music_drill_results", (album_title, (), str(exc))))

        threading.Thread(target=worker, daemon=True).start()

    def _drill_down_artist_top(self, artist_id: str, artist_name: str) -> None:
        self._save_search_state_before_drill()
        self.music_search_status_var.set(f"Carregando top músicas de '{artist_name}'...")

        def worker():
            try:
                tracks = get_artist_top_tracks(artist_id, limit=30)
                results = tuple(
                    DeezerSearchResult(
                        kind="track",
                        item_id=t.track_id,
                        title=t.title,
                        subtitle=f"{artist_name} • {t.album}",
                        cover_url=t.cover_url,
                        page_url=t.page_url,
                    ) for t in tracks
                )
                self.event_queue.put(("music_drill_results", (f"Top: {artist_name}", results, "")))
            except Exception as exc:
                self.event_queue.put(("music_drill_results", (artist_name, (), str(exc))))

        threading.Thread(target=worker, daemon=True).start()

    def _drill_artist_albums(self) -> None:
        result = self._selected_catalog_result()
        if not result:
            if self._music_search_results:
                result = self._music_search_results[0]
            else:
                self.music_search_status_var.set("Selecione um artista ou faixa para ver a discografia.")
                return
        if result.kind != "artist":
            artist_name = result.subtitle.split(" • ")[0].strip() if " • " in result.subtitle else result.subtitle.strip()
            if not artist_name:
                self.music_search_status_var.set("Artista não identificado para esta faixa.")
                return
            self._save_search_state_before_drill()
            self.music_search_status_var.set(f"Buscando discografia de '{artist_name}'...")
            def worker():
                try:
                    res = search_deezer_catalog(artist_name, kind="artist", limit=5)
                    if res:
                        first_art = res[0]
                        albums = get_artist_albums(first_art.item_id, limit=50)
                        self.event_queue.put(("music_drill_results", (f"Álbuns: {first_art.title}", albums, "")))
                    else:
                        self.event_queue.put(("music_drill_results", (artist_name, (), "Artista não encontrado.")))
                except Exception as exc:
                    self.event_queue.put(("music_drill_results", (artist_name, (), str(exc))))
            threading.Thread(target=worker, daemon=True).start()
            return

        self._save_search_state_before_drill()
        artist_id = result.item_id
        artist_name = result.title
        self.music_search_status_var.set(f"Carregando discografia de '{artist_name}'...")

        def worker():
            try:
                albums = get_artist_albums(artist_id, limit=50)
                self.event_queue.put(("music_drill_results", (f"Álbuns: {artist_name}", albums, "")))
            except Exception as exc:
                self.event_queue.put(("music_drill_results", (artist_name, (), str(exc))))

        threading.Thread(target=worker, daemon=True).start()

    def _handle_music_drill_results(self, payload: tuple) -> None:
        title, results, error = payload
        self._clear_music_search_results()
        if error:
            self.music_search_status_var.set(f"Falha ao carregar {title}: {error}")
            return
        self._music_search_results = list(results)
        for index, result in enumerate(results):
            self.music_results_tree.insert(
                "",
                END,
                iid=str(index),
                values=(result.kind_label, result.title, result.subtitle),
            )
        count = len(results)
        self.music_search_status_var.set(f"{title} • {count} item(ns). Duplo clique ou Baixar.")
        if count:
            self.music_results_tree.selection_set("0")
            self.music_results_tree.focus("0")
            self._show_selected_catalog_result()
        if hasattr(self, "catalog_back_button"):
            self.catalog_back_button.configure(state="normal")

    def _focus_music_search(self) -> None:
        if hasattr(self, "music_page"):
            self.tabs.select(self.music_page)
        if hasattr(self, "music_search_entry"):
            self.music_search_entry.focus_set()
            self.music_search_entry.select_range(0, 'end')

    def _play_selected_catalog_result(self) -> None:
        self._toggle_catalog_playback()

    def _open_selected_catalog_result(self) -> None:
        result = self._selected_catalog_result()
        if result is not None:
            webbrowser.open(result.page_url)
        elif self._music_search_results:
            webbrowser.open(self._music_search_results[0].page_url)
        else:
            webbrowser.open("https://www.deezer.com")

    def _load_top_brasil(self) -> None:
        self._save_search_state_before_drill()
        self.music_search_status_var.set("Carregando Top 50 Brasil da Deezer...")

        def worker():
            try:
                tracks = get_deezer_top_brasil(50)
                results = tuple(
                    DeezerSearchResult(
                        kind="track",
                        item_id=t.track_id,
                        title=t.title,
                        subtitle=f"{t.artist} • {t.album}",
                        cover_url=t.cover_url,
                        page_url=t.page_url,
                    ) for t in tracks
                )
                self.event_queue.put(("music_drill_results", ("🔥 Top Brasil", results, "")))
            except Exception as exc:
                self.event_queue.put(("music_drill_results", ("Top Brasil", (), str(exc))))

        threading.Thread(target=worker, daemon=True).start()

    def _load_top_global(self) -> None:
        self._save_search_state_before_drill()
        self.music_search_status_var.set("Carregando Top 50 Global da Deezer...")

        def worker():
            try:
                tracks = get_deezer_top_global(50)
                results = tuple(
                    DeezerSearchResult(
                        kind="track",
                        item_id=t.track_id,
                        title=t.title,
                        subtitle=f"{t.artist} • {t.album}",
                        cover_url=t.cover_url,
                        page_url=t.page_url,
                    ) for t in tracks
                )
                self.event_queue.put(("music_drill_results", ("🌍 Top Global", results, "")))
            except Exception as exc:
                self.event_queue.put(("music_drill_results", ("Top Global", (), str(exc))))

        threading.Thread(target=worker, daemon=True).start()

    def _load_user_favorites(self) -> None:
        arl = str(self.deezer_arl_var.get() if hasattr(self, "deezer_arl_var") else "").strip()
        if not arl:
            messagebox.showwarning(
                "Meus Favoritos Deezer",
                "Nenhum cookie ARL configurado.\n\nPara acessar seus favoritos, informe seu cookie ARL da Deezer em Configurações (ícone ⚙️).",
            )
            return
        self._save_search_state_before_drill()
        self.music_search_status_var.set("Carregando músicas favoritas da sua conta Deezer...")

        def worker():
            try:
                tracks = get_deezer_user_favorites(arl, 100)
                results = tuple(
                    DeezerSearchResult(
                        kind="track",
                        item_id=t.track_id,
                        title=t.title,
                        subtitle=f"{t.artist} • {t.album}",
                        cover_url=t.cover_url,
                        page_url=t.page_url,
                    ) for t in tracks
                )
                self.event_queue.put(("music_drill_results", ("⭐ Meus Favoritos", results, "")))
            except Exception as exc:
                self.event_queue.put(("music_drill_results", ("Meus Favoritos", (), str(exc))))

        threading.Thread(target=worker, daemon=True).start()

    def _on_music_tree_context_menu(self, event):
        item_id = self.music_results_tree.identify_row(event.y)
        if not item_id:
            return
        self.music_results_tree.selection_set(item_id)
        self.music_results_tree.focus(item_id)
        self._show_selected_catalog_result()
        result = self._selected_catalog_result()
        if not result:
            return

        menu = Menu(self.music_results_tree, tearoff=0)
        has_arl = bool(str(self.deezer_arl_var.get() if hasattr(self, "deezer_arl_var") else "").strip())
        if result.kind == "track":
            menu.add_command(
                label="▶ Reproduzir Faixa Completa (ARL)" if has_arl else "▶ Reproduzir Prévia (30s)",
                command=self._play_selected_catalog_result,
            )
            menu.add_command(
                label="💬 Ver Letras Sincronizadas",
                command=self._open_lyrics_window,
            )
            menu.add_separator()
            menu.add_command(
                label="⚡ Baixar em FLAC Lossless (Hi-Fi)",
                command=lambda: self._download_result_with_quality(result, "flac"),
            )
            menu.add_command(
                label="🎵 Baixar em MP3 320 kbps",
                command=lambda: self._download_result_with_quality(result, "mp3_320"),
            )
            menu.add_command(
                label="📱 Baixar em MP3 128 kbps",
                command=lambda: self._download_result_with_quality(result, "mp3_128"),
            )
            menu.add_separator()
            menu.add_command(label="💿 Ver Álbum desta Música", command=self._view_album_for_selected)
            menu.add_command(label="⚡ Baixar Álbum Completo", command=self._download_album_for_selected)
            menu.add_command(label="☑️ Escolher Faixas do Álbum...", command=self._tracklist_for_selected)
            menu.add_separator()
        elif result.kind == "album":
            menu.add_command(label="💿 Ver Faixas do Álbum", command=self._view_album_for_selected)
            menu.add_command(label="⚡ Baixar Álbum Completo", command=self._download_album_for_selected)
            menu.add_command(label="☑️ Escolher Faixas do Álbum...", command=self._tracklist_for_selected)
            menu.add_separator()
        elif result.kind == "artist":
            menu.add_command(label="🎵 Top Músicas do Artista", command=self._drill_down_selected)
            menu.add_command(label="💿 Ver Álbuns do Artista", command=self._drill_artist_albums)
            menu.add_separator()

        menu.add_command(label="➕ Adicionar à Fila", command=self._load_selected_catalog_result)
        menu.add_command(
            label="📋 Copiar Link Deezer",
            command=lambda: (self.root.clipboard_clear(), self.root.clipboard_append(result.page_url)),
        )
        if result.kind in {"track", "album"}:
            menu.add_command(
                label="🔍 Raio-X Metadados (ISRC / BPM)",
                command=lambda: self._open_link_analyzer_dialog(result.page_url),
            )
        menu.add_command(label="🌐 Abrir no Deezer", command=self._open_selected_catalog_result)

        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _download_result_with_quality(self, result, quality: str) -> None:
        if not result:
            return
        old_quality = self.deezer_quality_var.get() if hasattr(self, "deezer_quality_var") else None
        if hasattr(self, "deezer_quality_var"):
            self.deezer_quality_var.set(quality)
        self._apply_work_mode("music", initial=True)
        self._prepare_sources([result.page_url], True)
        if old_quality and hasattr(self, "deezer_quality_var"):
            self.root.after(1000, lambda: self.deezer_quality_var.set(old_quality))

    def _open_link_analyzer_dialog(self, url_or_id=None) -> None:
        target = url_or_id
        if not target:
            result = self._selected_catalog_result()
            if result:
                target = result.page_url
            else:
                query = self.music_search_var.get().strip()
                if query and (is_deezer_url(query) or query.isdigit()):
                    target = query
        if not target:
            from tkinter.simpledialog import askstring
            target = askstring("Raio-X de Metadados", "Cole o link ou ID da faixa/álbum da Deezer:", parent=self.root)
            if not target:
                return

        dialog = Toplevel(self.root)
        dialog.title("🔍 Raio-X de Metadados Deezer (Link Analyzer)")
        dialog.transient(self.root)
        fit_window(dialog, max_width=680, max_height=600)

        loading_lbl = ttk.Label(dialog, text="Consultando metadados na Deezer...", font=(self.text_family, 11), padding=20)
        loading_lbl.pack(expand=True)

        def worker():
            try:
                data = analyze_deezer_metadata(target)
                def update_ui():
                    loading_lbl.destroy()
                    container = ttk.Frame(dialog, padding=16)
                    container.pack(fill="both", expand=True)

                    header = ttk.Frame(container)
                    header.pack(fill="x", pady=(0, 12))
                    title_txt = f"{data['title']}"
                    sub_txt = f"{data['artist']} • {data['album']}" if data['album'] else data['artist']
                    ttk.Label(header, text=title_txt, font=(self.display_family, 13, "bold"), foreground="#2563eb").pack(anchor="w")
                    ttk.Label(header, text=sub_txt, font=(self.text_family, 10), foreground="#475569").pack(anchor="w", pady=(2, 0))

                    tbl = ttk.Frame(container)
                    tbl.pack(fill="both", expand=True, pady=(0, 12))

                    info_fields = [
                        ("Tipo de Mídia", data["kind"].upper()),
                        ("Identificador (ID)", str(data["id"])),
                        ("Código ISRC", str(data["isrc"] or "Não disponível")),
                        ("Código UPC / Barcode", str(data["upc"] or "Não disponível")),
                        ("Duração", data["duration_formatted"]),
                        ("BPM (Batidas por Minuto)", str(data["bpm"] or "Não informado")),
                        ("Data de Lançamento", str(data["release_date"] or "Não informado")),
                        ("Gravadora / Selo", str(data["label"] or "Não informado")),
                        ("Gênero(s)", str(data["genres"] or "Não informado")),
                        ("Faixa / Posição", str(data["track_position"] or "—")),
                        ("Número do Disco", str(data["disk_number"] or "—")),
                        ("Letras Explícitas", "Sim (Explicit 🔞)" if data["explicit"] else "Não"),
                        ("Disponibilidade", "Liberada para streaming" if data["readable"] is not False else "Restrita"),
                    ]
                    if data.get("available_countries_count"):
                        info_fields.append(("Países Disponíveis", f"{data['available_countries_count']} países"))

                    for r, (lbl, val) in enumerate(info_fields):
                        bg_color = "#f8fafc" if r % 2 == 0 else "#ffffff"
                        row_frame = Frame(tbl, bg=bg_color, padx=6, pady=3)
                        row_frame.pack(fill="x")
                        ttk.Label(row_frame, text=lbl, font=(self.text_family, 9, "bold"), width=24).pack(side="left")
                        ttk.Label(row_frame, text=val, font=(self.text_family, 9)).pack(side="left", fill="x", expand=True)

                    actions = ttk.Frame(container)
                    actions.pack(fill="x", pady=(10, 0))
                    ttk.Button(
                        actions,
                        text="⚡ Baixar esta mídia",
                        style="Accent.TButton",
                        command=lambda: (dialog.destroy(), self._prepare_sources([data["page_url"]], True)),
                    ).pack(side="left")
                    if data.get("isrc"):
                        isrc_val = data["isrc"]
                        ttk.Button(
                            actions,
                            text="📋 Copiar ISRC",
                            command=lambda: (self.root.clipboard_clear(), self.root.clipboard_append(isrc_val), messagebox.showinfo("ISRC", f"Código ISRC copiado:\n{isrc_val}", parent=dialog)),
                        ).pack(side="left", padx=6)
                    ttk.Button(
                        actions,
                        text="📋 Copiar Link",
                        command=lambda: (self.root.clipboard_clear(), self.root.clipboard_append(data["page_url"])),
                    ).pack(side="left")
                    ttk.Button(actions, text="Fechar", command=dialog.destroy).pack(side="right")

                dialog.after_idle(update_ui)
            except Exception as exc:
                def show_err():
                    loading_lbl.configure(text=f"Erro ao analisar link Deezer:\n{exc}", foreground="#ef4444")
                dialog.after_idle(show_err)

        threading.Thread(target=worker, daemon=True).start()

    def _open_album_tracklist_dialog(self) -> None:
        self._tracklist_for_selected()

    def _open_album_tracklist_dialog_for_id(self, album_id: str, album_title: str) -> None:
        if not album_id:
            messagebox.showinfo("Selecionar Faixas", "Identificador do álbum não disponível.")
            return

        dialog = Toplevel(self.root)
        dialog.title(f"☑️ Faixas do Álbum: {album_title}")
        dialog.transient(self.root)
        fit_window(dialog, max_width=720, max_height=620)

        loading = ttk.Label(dialog, text=f"Carregando faixas do álbum '{album_title}'...", font=(self.text_family, 11), padding=20)
        loading.pack(expand=True)

        def worker():
            try:
                tracks = get_album_tracks(album_id)
                def setup_ui():
                    loading.destroy()
                    container = ttk.Frame(dialog, padding=12)
                    container.pack(fill="both", expand=True)

                    header = ttk.Frame(container)
                    header.pack(fill="x", pady=(0, 8))
                    ttk.Label(header, text=f"Álbum: {album_title}", font=(self.display_family, 12, "bold"), foreground="#2563eb").pack(anchor="w")
                    count_var = StringVar()
                    count_lbl = ttk.Label(header, textvariable=count_var, font=(self.text_family, 9), foreground="#64748b")
                    count_lbl.pack(anchor="w")

                    table_frame = ttk.Frame(container)
                    table_frame.pack(fill="both", expand=True, pady=(0, 8))
                    columns = ("selected", "num", "title", "duration")
                    tree = ttk.Treeview(table_frame, columns=columns, show="headings", height=14, selectmode="browse")
                    tree.heading("selected", text="✓")
                    tree.heading("num", text="#")
                    tree.heading("title", text="Título da Faixa")
                    tree.heading("duration", text="Duração")
                    tree.column("selected", width=36, anchor="center")
                    tree.column("num", width=44, anchor="center")
                    tree.column("title", width=420, anchor="w")
                    tree.column("duration", width=70, anchor="center")
                    tree.grid(row=0, column=0, sticky="nsew")
                    table_frame.rowconfigure(0, weight=1)
                    table_frame.columnconfigure(0, weight=1)

                    sb = ttk.Scrollbar(table_frame, orient="vertical", command=tree.yview)
                    sb.grid(row=0, column=1, sticky="ns")
                    tree.configure(yscrollcommand=sb.set)

                    track_states = {}
                    for idx, t in enumerate(tracks):
                        track_states[str(idx)] = {"enabled": True, "track": t}
                        dur_txt = f"{t.duration // 60}:{t.duration % 60:02d}" if t.duration else "—"
                        tree.insert("", END, iid=str(idx), values=("✓", str(t.track_number or idx + 1), t.title, dur_txt))

                    def update_count():
                        sel = sum(1 for it in track_states.values() if it["enabled"])
                        count_var.set(f"{sel} de {len(tracks)} faixa(s) selecionada(s)")

                    update_count()

                    def toggle_item(item_id):
                        if item_id in track_states:
                            curr = track_states[item_id]["enabled"]
                            track_states[item_id]["enabled"] = not curr
                            t = track_states[item_id]["track"]
                            dur_txt = f"{t.duration // 60}:{t.duration % 60:02d}" if t.duration else "—"
                            tree.item(item_id, values=("✓" if not curr else "", str(t.track_number or int(item_id) + 1), t.title, dur_txt))
                            update_count()

                    def on_click(event):
                        row = tree.identify_row(event.y)
                        if row:
                            toggle_item(row)

                    def on_space(_event):
                        sel = tree.selection()
                        if sel:
                            toggle_item(sel[0])

                    tree.bind("<Button-1>", on_click)
                    tree.bind("<space>", on_space)

                    toolbar = ttk.Frame(container)
                    toolbar.pack(fill="x", pady=(0, 8))

                    def set_all(state):
                        for iid, data in track_states.items():
                            data["enabled"] = state
                            t = data["track"]
                            dur_txt = f"{t.duration // 60}:{t.duration % 60:02d}" if t.duration else "—"
                            tree.item(iid, values=("✓" if state else "", str(t.track_number or int(iid) + 1), t.title, dur_txt))
                        update_count()

                    ttk.Button(toolbar, text="Marcar Todas", command=lambda: set_all(True)).pack(side="left", padx=(0, 4))
                    ttk.Button(toolbar, text="Desmarcar Todas", command=lambda: set_all(False)).pack(side="left", padx=(0, 8))

                    def download_selected():
                        selected_urls = [data["track"].page_url for data in track_states.values() if data["enabled"]]
                        if not selected_urls:
                            messagebox.showwarning("Seleção", "Marque pelo menos uma faixa para baixar.", parent=dialog)
                            return
                        dialog.destroy()
                        self._apply_work_mode("music", initial=True)
                        self._prepare_sources(selected_urls, True)

                    ttk.Button(toolbar, text="⚡ Baixar Selecionadas", style="Accent.TButton", command=download_selected).pack(side="right")
                    ttk.Button(toolbar, text="Fechar", command=dialog.destroy).pack(side="right", padx=(0, 6))

                dialog.after_idle(setup_ui)
            except Exception as exc:
                def show_err():
                    loading.configure(text=f"Erro ao carregar faixas do álbum:\n{exc}", foreground="#ef4444")
                dialog.after_idle(show_err)

        threading.Thread(target=worker, daemon=True).start()


    def _build_music_details(self, parent) -> None:
        self.music_detail_frame = ttk.LabelFrame(parent, text="Detalhes da música", padding=10)
        cover_column = ttk.Frame(self.music_detail_frame)
        cover_column.pack(side="left", anchor="n", padx=(0, 12))
        self.music_cover_label = ttk.Label(cover_column, text="Sem capa", width=18, anchor="center")
        self.music_cover_label.pack()
        info = ttk.Frame(self.music_detail_frame)
        info.pack(side="left", fill="both", expand=True)
        self.music_title_var = StringVar()
        self.music_artist_var = StringVar()
        self.music_album_var = StringVar()
        self.music_extra_var = StringVar()
        wrapping_label(info, textvariable=self.music_title_var, font=(self.text_family, 11, "bold"))
        wrapping_label(info, textvariable=self.music_artist_var)
        wrapping_label(info, textvariable=self.music_album_var)
        wrapping_label(info, textvariable=self.music_extra_var, foreground="#596579")
        buttons = ttk.Frame(info)
        buttons.pack(fill="x", pady=(8, 0))
        self.music_play_button = ttk.Button(
            buttons, text="▶", width=3, command=self.play_selected_music,
        )
        self.music_play_button.pack(side="left")
        self.music_stop_button = ttk.Button(
            buttons, text="■", width=3, command=self.stop_music,
        )
        self.music_stop_button.pack(side="left", padx=(6, 0))
        add_tooltip(self.music_play_button, "Reproduzir ou pausar")
        add_tooltip(self.music_stop_button, "Parar a reprodução")
        ttk.Button(buttons, text="Abrir no Deezer", command=self.open_selected_in_deezer).pack(side="left", padx=(6, 0))
        ttk.Button(buttons, text="Editar metadados", command=self.edit_selected_music_metadata).pack(side="left", padx=(6, 0))
        ttk.Button(buttons, text="Alterar capa", command=self.change_selected_music_cover).pack(side="left", padx=(6, 0))

    def _selected_music_item(self, *, completed: bool = False):
        selection = self.completed_tree.selection() if completed else self.episode_tree.selection()
        item_id = selection[0] if selection else self.current_music_item_id
        return next((item for item in self.queue_items if item.get("id") == item_id), None)

    def _show_music_item(self, item) -> None:
        if not item or item.get("kind") != "deezer_preview":
            self.current_music_item_id = None
            self._music_cover_image = None
            if hasattr(self, "music_detail_frame"):
                self.music_detail_frame.pack_forget()
            return
        self.current_music_item_id = item.get("id")
        if not self.music_detail_frame.winfo_manager():
            self.music_detail_frame.pack(fill="x", pady=(8, 0))
        self.music_title_var.set(str(item.get("track_title") or item.get("title") or "Música"))
        self.music_artist_var.set(f"Artista: {item.get('artist') or 'Não informado'}")
        self.music_album_var.set(f"Álbum: {item.get('album') or 'Não informado'}")
        extras = []
        if item.get("track_number"):
            extras.append(f"Faixa {item.get('track_number')}")
        if item.get("disc_number"):
            extras.append(f"Disco {item.get('disc_number')}")
        if item.get("release_year"):
            extras.append(str(item.get("release_year")))
        self.music_extra_var.set(" • ".join(extras))
        self.music_cover_label.configure(image="", text="Carregando capa...")
        self._music_cover_image = None
        item_copy = dict(item)

        def worker():
            try:
                cover = cover_bytes_for_item(item_copy)
            except Exception:
                cover = None
            self.event_queue.put(("music_cover_ready", (item_copy.get("id"), cover)))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_music_cover(self, payload: object) -> None:
        if not isinstance(payload, tuple) or len(payload) != 2:
            return
        item_id, data = payload
        if item_id != self.current_music_item_id or not data:
            if item_id == self.current_music_item_id:
                self.music_cover_label.configure(image="", text="Sem capa")
            return
        try:
            from io import BytesIO
            from PIL import Image, ImageTk

            image = Image.open(BytesIO(data))
            image.thumbnail((140, 140))
            self._music_cover_image = ImageTk.PhotoImage(image)
            self.music_cover_label.configure(image=self._music_cover_image, text="")
        except Exception:
            self._music_cover_image = None
            self.music_cover_label.configure(image="", text="Capa indisponível")

    def play_selected_music(self, *, completed: bool = False) -> None:
        item = self._selected_music_item(completed=completed)
        if not item or item.get("kind") not in {"deezer_preview", "deezer_full"}:
            messagebox.showinfo(APP_NAME, "Selecione uma música da Deezer.")
            return

        item_id = str(item.get("id") or item.get("media_id"))

        if getattr(self, "_playing_item_id", None) == item_id and self.music_player.is_playing():
            self.music_player.pause()
            self.download_metrics_var.set("Reprodução pausada.")
            self._update_player_buttons()
            return

        if getattr(self, "_playing_item_id", None) == item_id and self.music_player.is_paused():
            self.music_player.resume()
            self.download_metrics_var.set("Reproduzindo...")
            self._update_player_buttons()
            return

        if self.video_player.is_active() or self.video_player.source:
            self.stop_video()

        files = [Path(value) for value in item.get("files", [])]
        local = next((path for path in files if path.is_file()), None)
        item_copy = dict(item)

        def worker():
            try:
                title = str(item_copy.get("track_title") or item_copy.get("title") or "")
                artist = str(item_copy.get("artist") or "")
                album = str(item_copy.get("album") or "")
                if local is not None:
                    mode = self.music_player.play_file(
                        local,
                        title=title or local.stem,
                        artist=artist,
                        album=album,
                    )
                    message = "Reproduzindo arquivo local." if mode == "internal" else "Áudio aberto no player padrão do Windows."
                else:
                    track = resolve_deezer_track(str(item_copy.get("media_id") or ""))
                    if not track.preview_url:
                        raise MusicPlayerError("A Deezer não disponibilizou prévia pública para esta faixa.")
                    self.music_player.play_preview(
                        track.preview_url,
                        title=title or track.title,
                        artist=artist or track.artist,
                        album=album or track.album,
                        duration=track.duration or 30.0,
                    )
                    message = "Reproduzindo a prévia pública da Deezer."
                self._playing_item_id = item_id
                self._catalog_playing_active = False
                self.event_queue.put(("music_player_status", message))
            except Exception as exc:
                self._playing_item_id = None
                self._catalog_playing_active = False
                self.event_queue.put(("music_player_error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _toggle_catalog_playback(self) -> None:
        result = self._selected_catalog_result()
        if result is None or result.kind != "track":
            if self._music_search_results:
                first_track = next((r for r in self._music_search_results if r.kind == "track"), None)
                if first_track:
                    result = first_track
            if result is None or result.kind != "track":
                self.music_search_status_var.set("Selecione uma faixa da lista para reproduzir.")
                return

        catalog_id = f"catalog_{result.item_id}"

        if getattr(self, "_playing_item_id", None) == catalog_id and self.music_player.is_playing():
            self.music_player.pause()
            self.download_metrics_var.set(f"Pausado: {result.title}")
            self._update_player_buttons()
            return

        if getattr(self, "_playing_item_id", None) == catalog_id and self.music_player.is_paused():
            self.music_player.resume()
            self.download_metrics_var.set(f"Reproduzindo: {result.title}")
            self._update_player_buttons()
            return

        if self.video_player.is_active() or self.video_player.source:
            self.stop_video()

        arl = str(self.deezer_arl_var.get() if hasattr(self, "deezer_arl_var") else "").strip()

        def worker():
            try:
                track = resolve_deezer_track(result.item_id)
                played_full = False
                if arl:
                    try:
                        self.event_queue.put(("music_player_status", f"Baixando faixa completa: {result.title}..."))
                        pref_qual = str(getattr(self, "deezer_quality_var", None) and self.deezer_quality_var.get() or "auto")
                        self.music_player.play_deezer_full(
                            result.item_id,
                            arl,
                            title=result.title,
                            artist=result.subtitle,
                            album=track.album,
                            duration=track.duration or 0.0,
                            quality_preference=pref_qual,
                        )
                        played_full = True
                    except Exception:
                        pass

                if not played_full:
                    if not track.preview_url:
                        raise MusicPlayerError("A Deezer não disponibilizou áudio para esta faixa.")
                    self.music_player.play_preview(
                        track.preview_url,
                        title=result.title,
                        artist=result.subtitle,
                        album=track.album,
                        duration=track.duration or 30.0,
                    )

                self._playing_item_id = catalog_id
                self._catalog_playing_active = True
                status_msg = f"Reproduzindo faixa completa: {result.title}" if played_full else f"Reproduzindo prévia (30s): {result.title}"
                self.event_queue.put(("music_player_status", status_msg))
            except Exception as exc:
                self._playing_item_id = None
                self._catalog_playing_active = False
                self.event_queue.put(("music_player_error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def open_selected_in_deezer(self) -> None:
        item = self._selected_music_item()
        if not item or item.get("kind") not in {"deezer_preview", "deezer_full"}:
            messagebox.showinfo(APP_NAME, "Selecione uma música da Deezer.")
            return
        url = str(item.get("source") or "").strip()
        if not url.startswith("https://www.deezer.com/track/"):
            messagebox.showinfo(APP_NAME, "O link oficial desta faixa não está disponível.")
            return
        webbrowser.open(url)

    def stop_music(self) -> None:
        try:
            self.music_player.stop()
            self._playing_item_id = None
            self._catalog_playing_active = False
            self.music_player_seek_var.set(0)
            self.music_player_time_var.set("00:00 / 00:00")
            self.music_player_track_var.set("Nenhuma faixa em reprodução.")
            self.music_player_status_var.set("Reprodução interrompida.")
            self.download_metrics_var.set("Reprodução interrompida.")
            self._update_player_buttons()
            self._update_music_player()
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Não foi possível parar a reprodução:\n{exc}")

    def _update_player_buttons(self) -> None:
        is_playing = self.music_player.is_playing()
        cat_active = getattr(self, "_catalog_playing_active", False)
        if hasattr(self, "music_play_button"):
            self.music_play_button.configure(
                text="⏸" if (is_playing and not cat_active) else "▶"
            )
        if hasattr(self, "play_completed_button"):
            self.play_completed_button.configure(
                text="⏸" if (is_playing and not cat_active) else "▶"
            )
        if hasattr(self, "catalog_play_button"):
            self.catalog_play_button.configure(
                text="⏸" if (is_playing and cat_active) else "▶"
            )
        if hasattr(self, "audio_play_button"):
            self.audio_play_button.configure(
                text="⏸" if is_playing else "▶"
            )
        if hasattr(self, "audio_stop_button"):
            self.audio_stop_button.configure(
                state="normal" if self.music_player.is_active() else "disabled"
            )
        if hasattr(self, "catalog_stop_button"):
            res = self._selected_catalog_result() if hasattr(self, "_selected_catalog_result") else None
            can_stop = self.music_player.is_active() or (res is not None and res.kind == "track")
            self.catalog_stop_button.configure(state="normal" if can_stop else "disabled")

    def _metadata_dialog(self, item: dict, *, completed: bool) -> None:
        dialog = Toplevel(self.root)
        dialog.title("Editar metadados")
        dialog.transient(self.root)
        dialog.grab_set()
        body = ttk.Frame(dialog, padding=12)
        body.pack(fill="both", expand=True)

        fields = [
            ("Título", "track_title"),
            ("Artista", "artist"),
            ("Artista do álbum", "album_artist"),
            ("Álbum", "album"),
            ("Número da faixa", "track_number"),
            ("Número do disco", "disc_number"),
            ("Ano", "release_year"),
        ]
        variables = {}
        for row, (label, key) in enumerate(fields):
            ttk.Label(body, text=label).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=4)
            value = item.get(key)
            if key == "album_artist" and not value:
                value = item.get("artist")
            variable = StringVar(value=str(value or ""))
            variables[key] = variable
            ttk.Entry(body, textvariable=variable, width=42).grid(row=row, column=1, sticky="ew", pady=4)
        body.columnconfigure(1, weight=1)

        def save():
            changes = {key: variable.get().strip() for key, variable in variables.items()}
            for numeric in ("track_number", "disc_number"):
                text = changes[numeric]
                changes[numeric] = int(text) if text.isdigit() else None
            title = changes["track_title"] or str(item.get("track_title") or item.get("title") or "Música")
            artist = changes["artist"]
            changes["title"] = f"{artist} - {title} (prévia Deezer)" if artist else f"{title} (prévia Deezer)"
            try:
                self.queue_repository.update(item["id"], **changes)
                merged = dict(item)
                merged.update(changes)
                if completed:
                    local = next((Path(value) for value in merged.get("files", []) if Path(value).is_file()), None)
                    if local is not None:
                        write_audio_metadata(local, merged, cover_bytes=b"")
                self._refresh_queue()
                self._show_music_item(merged)
                dialog.destroy()
            except Exception as exc:
                messagebox.showerror(APP_NAME, f"Não foi possível salvar os metadados:\n{exc}", parent=dialog)

        buttons = ttk.Frame(body)
        buttons.grid(row=len(fields), column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(buttons, text="Cancelar", command=dialog.destroy).pack(side="right")
        ttk.Button(buttons, text="Salvar", command=save).pack(side="right", padx=(0, 6))

    def edit_selected_music_metadata(self, *, completed: bool = False) -> None:
        item = self._selected_music_item(completed=completed)
        if not item or item.get("kind") != "deezer_preview":
            messagebox.showinfo(APP_NAME, "Selecione uma música da Deezer.")
            return
        if completed:
            local = next((Path(value) for value in item.get("files", []) if Path(value).is_file()), None)
            if local is not None:
                try:
                    existing = read_audio_metadata(local)
                    merged = dict(item)
                    merged.update(existing)
                    item = merged
                except Exception:
                    pass
        self._metadata_dialog(dict(item), completed=completed)

    def change_selected_music_cover(self, *, completed: bool = False) -> None:
        item = self._selected_music_item(completed=completed)
        if not item or item.get("kind") != "deezer_preview":
            messagebox.showinfo(APP_NAME, "Selecione uma música da Deezer.")
            return
        selected = filedialog.askopenfilename(
            title="Selecionar capa",
            filetypes=[("Imagens", "*.jpg *.jpeg *.png"), ("Todos os arquivos", "*.*")],
        )
        if not selected:
            return
        try:
            data = Path(selected).read_bytes()
            if not data or len(data) > 8 * 1024 * 1024:
                raise ValueError("A imagem deve ter no máximo 8 MB.")
            self.queue_repository.update(item["id"], custom_cover_path=selected)
            merged = dict(item)
            merged["custom_cover_path"] = selected
            if completed:
                local = next((Path(value) for value in merged.get("files", []) if Path(value).is_file()), None)
                if local is not None:
                    write_audio_metadata(local, merged, cover_bytes=data)
            self._refresh_queue()
            self._show_music_item(merged)
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Não foi possível alterar a capa:\n{exc}")

    def open_completed_folder(self) -> None:
        item = self._selected_music_item(completed=True)
        if not item:
            return
        local = next((Path(value) for value in item.get("files", []) if Path(value).exists()), None)
        if local is not None and os.name == "nt":
            subprocess.Popen(["explorer.exe", "/select,", str(local)])

    def choose_folder(self, target: str | None = None) -> None:
        is_music = (target == "music") or (target is None and self.work_mode == "music")
        current_var = self.music_folder_var if is_music else self.video_folder_var
        initial = Path(current_var.get().strip() or str(Path.home()))
        if not initial.exists():
            initial = Path.home()
        selected = filedialog.askdirectory(initialdir=str(initial))
        if selected:
            current_var.set(selected)
            active_is_music = self.work_mode == "music"
            if is_music == active_is_music:
                self.folder_var.set(selected)
            self._save_preferences()

    def choose_music_folder(self) -> None:
        self.choose_folder(target="music")

    def choose_video_folder(self) -> None:
        self.choose_folder(target="video")

    def open_current_download_folder(self) -> None:
        folder = self.music_folder_var.get() if self.work_mode == "music" else self.video_folder_var.get()
        target = Path(folder.strip() or str(Path.home() / "Downloads"))
        target.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(target))
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Não foi possível abrir a pasta: {exc}")

    def _on_preset_selected(self, _event=None) -> None:
        choice = self.preset_var.get()
        config = PRESETS.get(choice)
        if not config:
            return
        if "format" in config:
            fmt = config["format"]
            self.video_format_var.set(fmt)
            if self.work_mode == "video":
                self.resolution_var.set(fmt)
            self._on_video_format_selected()
        if "audio_bitrate" in config:
            self.audio_bitrate_mode_var.set(config["audio_bitrate"])
            self._update_audio_controls()
        if "chapters" in config and hasattr(self, "embed_chapters_var"):
            self.embed_chapters_var.set(config["chapters"])
        if "subs" in config and hasattr(self, "subtitles_embed_var"):
            self.subtitles_embed_var.set(config["subs"])
        self._save_preferences()
        self.queue_log(f"Perfil de download aplicado: {choice}")

    def _trigger_post_download_action(self) -> None:
        action = getattr(self, "post_download_action_var", None)
        if action is None:
            return
        choice = action.get()
        if choice == "Tocar som de alerta":
            try:
                import winsound
                winsound.MessageBeep(winsound.MB_ICONASTERISK)
            except Exception:
                try:
                    self.root.bell()
                except Exception:
                    pass
        elif choice == "Abrir pasta de downloads":
            try:
                self.open_current_download_folder()
            except Exception:
                pass
        elif choice == "Suspender computador" and os.name == "nt":
            try:
                self.queue_log("Executando suspensão do sistema após downloads...")
                subprocess.run(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"], check=False)
            except Exception as exc:
                self.queue_log(f"Falha ao suspender: {exc}")
        elif choice == "Desligar o computador (30s)" and os.name == "nt":
            try:
                self.queue_log("Agendando desligamento do sistema em 30 segundos (cancele via 'shutdown /a')...")
                subprocess.run(["shutdown", "/s", "/t", "30", "/c", "C² Video Downloader finalizou os downloads."], check=False)
            except Exception as exc:
                self.queue_log(f"Falha ao agendar desligamento: {exc}")

    def _update_detected_links_count(self) -> None:
        if not hasattr(self, "video_url_text") or not hasattr(self, "detected_links_var"):
            return
        content = self.video_url_text.get("1.0", "end-1c")
        lines = [line.strip() for line in content.splitlines() if line.strip() and not line.strip().startswith("#")]
        count = len(lines)
        if count == 0:
            self.detected_links_var.set("Nenhum link inserido")
        elif count == 1:
            self.detected_links_var.set("1 link detectado")
        else:
            self.detected_links_var.set(f"{count} links detectados")

    def _paste_clipboard_to_url_text(self) -> None:
        try:
            text = self.root.clipboard_get()
            if text.strip():
                current = self.video_url_text.get("1.0", "end-1c").strip()
                combined = f"{current}\n{text}".strip() if current else text.strip()
                self.video_url_text.delete("1.0", END)
                self.video_url_text.insert("1.0", combined)
                self._update_detected_links_count()
                self._schedule_video_preview_check()
        except Exception:
            pass

    def _import_urls_from_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Importar lista de links",
            filetypes=[("Arquivos de texto", "*.txt;*.m3u;*.list"), ("Todos os arquivos", "*.*")],
        )
        if not path:
            return
        try:
            content = Path(path).read_text(encoding="utf-8", errors="replace")
            urls = [line.strip() for line in content.splitlines() if line.strip() and not line.strip().startswith("#")]
            if urls:
                current = self.video_url_text.get("1.0", "end-1c").strip()
                combined = f"{current}\n" + "\n".join(urls) if current else "\n".join(urls)
                self.video_url_text.delete("1.0", END)
                self.video_url_text.insert("1.0", combined.strip())
                self._update_detected_links_count()
                self._schedule_video_preview_check()
                self.queue_log(f"{len(urls)} link(s) importado(s) de {Path(path).name}.")
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Erro ao importar arquivo: {exc}")

    def _export_urls_to_file(self) -> None:
        content = self.video_url_text.get("1.0", "end-1c").strip()
        if not content:
            messagebox.showinfo(APP_NAME, "Não há links para exportar.")
            return
        path = filedialog.asksaveasfilename(
            title="Salvar lista de links",
            defaultextension=".txt",
            filetypes=[("Arquivo de texto", "*.txt"), ("Todos os arquivos", "*.*")],
        )
        if path:
            try:
                Path(path).write_text(content, encoding="utf-8")
                self.queue_log(f"Links exportados para {Path(path).name}.")
            except Exception as exc:
                messagebox.showerror(APP_NAME, f"Erro ao salvar arquivo: {exc}")

    def _clear_url_text(self) -> None:
        self.video_url_text.delete("1.0", END)
        self._update_detected_links_count()
        self._schedule_video_preview_check()

    def _save_preferences(self) -> None:
        settings: dict[str, object] = {
            "download_folder": self.video_folder_var.get().strip() or str(Path.home() / "Downloads"),
            "video_download_folder": self.video_folder_var.get().strip() or str(Path.home() / "Downloads"),
            "music_download_folder": self.music_folder_var.get().strip() or str(Path.home() / "Downloads" / "Músicas"),
            "work_mode": self.work_mode,
            "music_structure": self.music_structure_var.get(),
            "music_filename_template": self.music_filename_var.get().strip() or DEFAULT_MUSIC_FILENAME,
            "music_search_type": self.music_search_type_var.get(),
            "format": self.video_format_var.get(),
            "video_format": self.video_format_var.get(),
            "music_format": self.music_format_var.get(),
            "audio_bitrate_mode": self.audio_bitrate_mode_var.get(),
            "audio_custom_bitrate": self.audio_custom_bitrate_var.get().strip() or "192",
            "playlist": bool(self.playlist_var.get()),
            "cookies_browser": self.cookies_browser_var.get(),
            "concurrent_fragments": fragment_count(self.fragments_var.get()),
            "deezer_arl": self.deezer_arl_var.get().strip(),
            "deezer_quality": self.deezer_quality_var.get(),
            "create_collection_zip": bool(self.create_zip_var.get()),
            "subtitles_enabled": bool(self.subtitles_enabled_var.get()),
            "subtitles_embed": bool(self.subtitles_embed_var.get()),
            "subtitles_auto": bool(self.subtitles_auto_var.get()),
            "subtitles_langs": self.subtitles_langs_var.get().strip() or "pt,pt-BR,en",
            "sponsorblock": bool(self.sponsorblock_var.get()),
            "embed_chapters": bool(self.embed_chapters_var.get()),
            "split_chapters": bool(self.split_chapters_var.get()),
            "rate_limit": self.rate_limit_var.get().strip() or "Ilimitado",
            "proxy_url": self.proxy_url_var.get().strip(),
            "clipboard_monitor": bool(self.clipboard_monitor_var.get()),
            "preset": self.preset_var.get() if hasattr(self, "preset_var") else "Personalizado",
            "post_download_action": self.post_download_action_var.get() if hasattr(self, "post_download_action_var") else "Nenhuma ação",
        }
        try:
            save_user_settings(settings)
            self.user_settings = settings
        except OSError as exc:
            self.queue_log(f"Aviso: não foi possível salvar as preferências ({exc}).")

    def _on_close(self) -> None:
        if self.busy:
            if not messagebox.askyesno(
                APP_NAME, "Há um trabalho em andamento. Sair e interrompê-lo?\n\n"
                "Os arquivos parciais serão mantidos para uma nova tentativa.",
            ):
                return
            try:
                self.download_control.cancel()
            except Exception as exc:
                messagebox.showerror(APP_NAME, f"Não foi possível interromper o trabalho: {exc}")
                return
        self._save_preferences()
        if getattr(self, "_clipboard_after_id", None) is not None:
            try:
                self.root.after_cancel(self._clipboard_after_id)
            except Exception:
                pass
            self._clipboard_after_id = None
        self._stop_music_search_workers()
        try:
            self.music_player.stop()
            self.video_player.release()
        except Exception:
            pass
        self.root.destroy()

    def _stop_music_search_workers(self) -> None:
        if self._music_search_shutdown.is_set():
            return
        self._music_search_shutdown.set()
        for _worker in self._music_search_workers:
            self._music_search_tasks.put(None)

    def _on_root_destroy(self, event) -> None:
        if event.widget == self.root:
            self._stop_music_search_workers()

    def toggle_pause(self) -> None:
        if not self.busy:
            return
        try:
            if self.download_control.paused:
                self.download_control.resume()
                self.pause_button.configure(text="Pausar")
                for button in self.context_pause_buttons:
                    button.configure(text="⏸")
                self.download_metrics_var.set(self._metrics_before_pause)
                self.queue_log("Continuando o trabalho do ponto em que foi pausado.")
            else:
                self.download_control.pause()
                self._metrics_before_pause = self.download_metrics_var.get()
                self._stop_progress_preserving_value()
                self.pause_button.configure(text="Continuar")
                for button in self.context_pause_buttons:
                    button.configure(text="▶")
                self.download_metrics_var.set(
                    "PAUSADO — arquivos preservados. Clique em Continuar para retomar.\n"
                    + self._metrics_before_pause
                )
                self.queue_log("Trabalho pausado. O tempo da pausa não entra nas estimativas.")
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Não foi possível pausar/continuar: {exc}")

    def _download_clock(self) -> float:
        return self.download_control.clock()

    def choose_cookies_file(self) -> None:
        selected = filedialog.askopenfilename(
            title="Selecionar cookies.txt",
            filetypes=[("Cookies", "*.txt"), ("Todos os arquivos", "*.*")],
        )
        if selected:
            self.cookies_file_var.set(selected)

    def _paste_deezer_arl(self) -> None:
        try:
            text = self.root.clipboard_get().strip()
            if text:
                self.deezer_arl_var.set(text)
                self._save_preferences()
                if hasattr(self, "_settings_snapshot"):
                    self._settings_snapshot["deezer_arl_var"] = text
                self._check_deezer_arl(show_dialog=False)
        except Exception:
            pass

    def _toggle_show_arl(self) -> None:
        shown = self.deezer_show_arl_var.get()
        self.deezer_show_arl_var.set(not shown)
        if hasattr(self, "deezer_arl_entry"):
            self.deezer_arl_entry.configure(show="" if not shown else "*")
        if hasattr(self, "deezer_toggle_btn"):
            self.deezer_toggle_btn.configure(text="Ocultar" if not shown else "Mostrar")

    def _clear_deezer_arl(self) -> None:
        self.deezer_arl_var.set("")
        self.deezer_status_var.set("● Não autenticado (baixando prévias de 30s)")
        self._save_preferences()
        if hasattr(self, "_settings_snapshot"):
            self._settings_snapshot["deezer_arl_var"] = ""

    def _selected_deezer_quality(self) -> str:
        choice = self.deezer_quality_var.get().strip().lower()
        if "flac" in choice:
            return "flac"
        if "320" in choice:
            return "mp3_320"
        if "128" in choice:
            return "mp3_128"
        return "auto"

    def _check_deezer_arl(self, show_dialog: bool = False) -> None:
        arl = self.deezer_arl_var.get().strip()
        if not arl:
            self.deezer_status_var.set("● Não autenticado (baixando prévias de 30s)")
            self._save_preferences()
            if hasattr(self, "_settings_snapshot"):
                self._settings_snapshot["deezer_arl_var"] = ""
            if show_dialog:
                messagebox.showinfo(
                    APP_NAME,
                    "Nenhum cookie ARL inserido.\n\n"
                    "O aplicativo continuará funcionando normalmente com as prévias oficiais de 30s.",
                )
            return

        self._save_preferences()
        if hasattr(self, "_settings_snapshot"):
            self._settings_snapshot["deezer_arl_var"] = arl

        self.deezer_status_var.set("● Verificando credenciais na Deezer...")

        try:
            from deezer_auth import validate_deezer_arl
        except ImportError as exc:
            self.deezer_status_var.set("● Recurso opcional indisponível")
            if show_dialog:
                messagebox.showwarning(
                    APP_NAME,
                    f"O módulo opcional de autenticação não está disponível:\n{exc}",
                )
            return

        def worker():
            info = validate_deezer_arl(arl)

            def callback():
                if info.get("valid"):
                    self.deezer_status_var.set(f"● Conectado: {info['user_name']} ({info['plan']})")
                    self._save_preferences()
                    if hasattr(self, "_settings_snapshot"):
                        self._settings_snapshot["deezer_arl_var"] = arl
                    if show_dialog:
                        messagebox.showinfo(
                            APP_NAME,
                            f"Autenticado com sucesso na Deezer!\n\n"
                            f"Usuário: {info['user_name']}\n"
                            f"Plano identificado: {info['plan']}\n\n"
                            f"Os downloads de faixas completas agora estão habilitados.",
                        )
                else:
                    err = info.get("error") or "Cookie ARL inválido ou expirado."
                    self.deezer_status_var.set(f"● Falha de autenticação: {err}")
                    if show_dialog:
                        messagebox.showwarning(
                            APP_NAME,
                            f"Não foi possível autenticar o cookie ARL:\n{err}\n\n"
                            f"Dica: Faça login no deezer.com pelo navegador > F12 > Armazenamento/Cookies > copie o valor de 'arl'.",
                        )

            self.root.after(0, callback)

        threading.Thread(target=worker, daemon=True).start()

    def clear_log(self) -> None:
        try:
            while True:
                self.log_queue.get_nowait()
        except queue.Empty:
            pass
        self.log.configure(state="normal")
        self.log.delete("1.0", END)
        self.log.configure(state="disabled")
        try:
            with self._activity_log_lock:
                ACTIVITY_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
                ACTIVITY_LOG_FILE.write_text("", encoding="utf-8")
        except OSError:
            pass

    def write_log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert(END, text.rstrip() + "\n")
        self.log.see(END)
        self.log.configure(state="disabled")

    def queue_log(self, text: str) -> None:
        try:
            with self._activity_log_lock:
                ACTIVITY_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
                if ACTIVITY_LOG_FILE.exists() and ACTIVITY_LOG_FILE.stat().st_size > 5 * 1024 * 1024:
                    previous = ACTIVITY_LOG_FILE.with_name("activity.previous.log")
                    previous.unlink(missing_ok=True)
                    ACTIVITY_LOG_FILE.replace(previous)
                timestamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
                with ACTIVITY_LOG_FILE.open("a", encoding="utf-8") as activity_log:
                    activity_log.write(f"[{timestamp}] {text.rstrip()}\n")
        except OSError:
            pass
        self.log_queue.put(text)

    def _set_indeterminate_progress(self, label: str) -> None:
        self.progress.stop()
        self.progress.configure(mode="indeterminate", maximum=100)
        self.progress_value_var.set(0)
        self.progress.start(10)
        self.download_item_var.set(label)
        self.download_metrics_var.set("Aguarde enquanto a operação é preparada...")

    def _stop_progress_preserving_value(self):
        value = self.progress_value_var.get()
        self.progress.stop()
        self.progress_value_var.set(value)

    def _begin_download_item(self, index: int, total: int, label: str) -> None:
        self.download_control.checkpoint()
        self.download_item_index = max(1, int(index))
        self.download_item_total = max(self.download_item_index, int(total))
        self.download_item_started_at = self._download_clock()
        self._last_media_progress_emit = 0.0
        self.event_queue.put(
            (
                "media_context",
                {
                    "index": self.download_item_index,
                    "total": self.download_item_total,
                    "label": label,
                    "queue_id": getattr(self, "active_queue_id", None),
                },
            )
        )

    def _emit_media_progress(self, payload: dict[str, object]) -> None:
        now = time.monotonic()
        percent = float(payload.get("percent") or 0.0)
        if percent < 100 and now - getattr(self, "_last_media_progress_emit", 0.0) < 0.2:
            return
        self._last_media_progress_emit = now
        enriched = dict(payload)
        enriched["index"] = self.download_item_index
        enriched["item_total"] = self.download_item_total
        enriched["queue_id"] = getattr(self, "active_queue_id", None)
        self.event_queue.put(("media_progress", enriched))

    def _report_direct_progress(self, downloaded: int = 0, total: int | None = None, *args, **kwargs) -> None:
        if "current_received" in kwargs:
            downloaded = kwargs["current_received"]
        if "current_total" in kwargs:
            total = kwargs["current_total"]
        self.download_control.checkpoint()
        elapsed = max(0.001, self._download_clock() - self.download_item_started_at)
        percent = downloaded * 100 / total if total else 0.0
        average_speed = downloaded / elapsed
        eta = (total - downloaded) / average_speed if total and average_speed > 0 else None
        self._emit_media_progress(
            {
                "downloaded": float(downloaded),
                "total": float(total) if total else None,
                "total_is_estimate": False,
                "speed": average_speed,
                "average_speed": average_speed,
                "eta": eta,
                "percent": percent,
            }
        )

    def _handle_media_context(self, payload: object) -> None:
        if not isinstance(payload, dict):
            return
        if self.download_control.paused:
            return
        index = max(1, int(payload.get("index") or 1))
        total = max(index, int(payload.get("total") or index))
        label = str(payload.get("label") or "Mídia")
        if getattr(self, "queue_running", False):
            selected = [item for item in self.queue_items if item["enabled"]]
            total = len(selected)
            index = next((position for position, item in enumerate(selected, 1) if item["id"] == payload.get("queue_id")), 1)
        self.progress.stop()
        self.progress.configure(mode="determinate", maximum=100)
        self.progress_value_var.set((index - 1) * 100 / total)
        if getattr(self, "queue_running", False):
            self.progress_value_var.set(queue_summary(self.queue_items)["overall"])
        self.download_item_var.set(f"Item {index}/{total} — {label}")
        self.download_metrics_var.set("Lendo tamanho e preparando o fluxo de mídia...")

    def _handle_media_progress(self, payload: object) -> None:
        if not isinstance(payload, dict):
            return
        if self.download_control.paused:
            return
        index = max(1, int(payload.get("index") or 1))
        item_total = max(index, int(payload.get("item_total") or index))
        percent = max(0.0, min(100.0, float(payload.get("percent") or 0.0)))
        overall = ((index - 1) + percent / 100) * 100 / item_total
        if getattr(self, "queue_running", False):
            summary = queue_summary(self.queue_items, payload.get("queue_id"), percent)
            overall = summary["overall"]
            item_total = summary["total"]
            item_id = payload.get("queue_id")
            if item_id and self.episode_tree.exists(item_id):
                self.episode_tree.set(item_id, "percent", f"{min(99, percent):.0f}")
        self.progress.stop()
        self.progress.configure(mode="determinate", maximum=100)
        self.progress_value_var.set(overall)

        downloaded = float(payload.get("downloaded") or 0.0)
        total_value = payload.get("total")
        total = float(total_value) if total_value is not None else None
        average_speed = payload.get("average_speed")
        average_speed_value = float(average_speed) if average_speed is not None else None
        current_speed = payload.get("speed")
        current_speed_value = (
            float(current_speed) if current_speed is not None else average_speed_value
        )
        file_eta_value = payload.get("eta")
        file_eta = float(file_eta_value) if file_eta_value is not None else None
        if file_eta is None and total and average_speed_value and average_speed_value > 0:
            file_eta = max(0.0, total - downloaded) / average_speed_value

        job_eta = None
        elapsed_job = self._download_clock() - self.download_job_started_at
        if self.download_job_started_at and overall > 0.01:
            job_eta = elapsed_job * (100 - overall) / overall
        if getattr(self, "queue_running", False):
            units = overall * item_total / 100 - self.queue_initial_done
            job_eta = elapsed_job * (item_total * (1 - overall / 100)) / units if units > 0.01 else None

        total_label = "Total estimado" if payload.get("total_is_estimate") else "Total"
        def rate(value):
            return f"{format_bytes(value)}/s" if value is not None else "calculando"
        percentage = f"{percent:.1f}%".replace(".", ",") if payload.get("percent_known", True) else "calculando"
        queue_percentage = f"{overall:.1f}%".replace(".", ",")
        self.download_metrics_var.set(
            f"Arquivo: {percentage}  •  Recebido: {format_bytes(downloaded)}  •  "
            f"{total_label}: {format_bytes(total)}\n"
            f"Velocidade atual: {rate(current_speed_value)}  •  Média: {rate(average_speed_value)}\n"
            f"Restante no arquivo: {format_duration(file_eta)}  •  "
            f"Fila: ~{queue_percentage}% / ~{format_duration(job_eta)} (estimativa; inclui itens processados)"
        )

    def _handle_conversion_progress(self, payload: object) -> None:
        if not isinstance(payload, dict) or self.download_control.paused:
            return
        percent = payload.get("percent")
        self._stop_progress_preserving_value()
        self.download_item_var.set(str(payload.get("label") or "Finalizando mídia"))
        if percent is None:
            if not getattr(self, "queue_running", False):
                self.progress.configure(mode="indeterminate", maximum=100)
                self.progress_value_var.set(0)
                self.progress.start(15)
            self.download_metrics_var.set("Download recebido. Finalizando o arquivo; aguarde...")
        else:
            if not getattr(self, "queue_running", False):
                self.progress.configure(mode="determinate", maximum=100)
                self.progress_value_var.set(float(percent))
            self.download_metrics_var.set(
                f"Finalização: {float(percent):.1f}%  •  "
                f"Tempo restante nesta etapa: ~{format_duration(payload.get('eta'))}\n"
                "Processamento local — não é transferência pela internet."
            )

    def _poll_queues(self) -> None:
        try:
            while True:
                self.write_log(self.log_queue.get_nowait())
        except queue.Empty:
            pass

        try:
            while True:
                event, payload = self.event_queue.get_nowait()
                if event == "maintenance_done":
                    self._maintenance_done(payload)
                elif event == "maintenance_error":
                    self._maintenance_error(str(payload))
                elif event == "update_available":
                    self._offer_application_update(payload)
                elif event == "download_progress":
                    received, total = payload
                    if total:
                        percentage = received * 100 / total
                        self.update_status_var.set(f"Baixando atualização: {percentage:.0f}%")
                        self.progress.stop()
                        self.progress.configure(mode="determinate", maximum=100)
                        self.progress_value_var.set(percentage)
                        self.download_item_var.set("Atualização do aplicativo")
                        self.download_metrics_var.set(
                            f"{percentage:.1f}%  •  {format_bytes(received)} / {format_bytes(total)}".replace(
                                ".", ","
                            )
                        )
                elif event == "media_context":
                    self._handle_media_context(payload)
                elif event == "media_progress":
                    self._handle_media_progress(payload)
                elif event == "conversion_progress":
                    self._handle_conversion_progress(payload)
                elif event == "application_installer_ready":
                    self._install_application_update(Path(str(payload)))
                elif event == "download_finished":
                    self._finish_download(payload)
                elif event == "queue_changed":
                    self._refresh_queue()
                elif event == "queue_prepared":
                    self._queue_prepared(payload)
                elif event == "queue_analysis_error":
                    self._set_queue_busy(False)
                    self.progress.stop()
                    self.download_item_var.set("Listagem interrompida; fila anterior preservada")
                    self.queue_log(str(payload))
                elif event == "application_update_error":
                    self.progress.stop()
                    self.update_button.configure(state="normal")
                    self.update_status_var.set("Falha ao baixar atualização")
                    messagebox.showerror(APP_NAME, str(payload))
                elif event == "music_search_results":
                    self._handle_music_search_results(payload)
                elif event == "music_drill_results":
                    self._handle_music_drill_results(payload)
                elif event == "catalog_cover_ready":
                    self._apply_catalog_cover(payload)
                elif event == "music_cover_ready":
                    self._apply_music_cover(payload)
                elif event == "video_preview_ready":
                    self._apply_video_preview(payload)
                elif event == "video_source_ready":
                    self._start_video_source(payload)
                elif event == "video_player_error":
                    self._handle_video_player_error(payload)
                elif event == "music_player_status":
                    self.download_metrics_var.set(str(payload))
                    self.music_player_status_var.set(str(payload))
                elif event == "music_player_error":
                    messagebox.showerror(APP_NAME, str(payload))
        except queue.Empty:
            pass

        self._update_player_buttons()
        self._update_video_player()
        self._update_music_player()
        self.root.after(150, self._poll_queues)

    def start_maintenance(self, force: bool = False) -> None:
        if self.maintenance_busy:
            return
        self.maintenance_busy = True
        self.update_button.configure(state="disabled")
        self.update_status_var.set("Verificando componentes e atualizações...")
        if not self.busy:
            self._set_indeterminate_progress("Verificando componentes e atualizações")
        threading.Thread(target=self._maintenance_worker, args=(force,), daemon=True).start()

    def _maintenance_worker(self, force: bool) -> None:
        try:
            status = self.dependencies.ensure(self.queue_log, force=force)
            self.event_queue.put(("maintenance_done", status))
            try:
                update = self.app_updater.check(self.queue_log)
                if update:
                    self.event_queue.put(("update_available", update))
            except Exception as exc:
                self.queue_log(f"Aviso: não foi possível verificar a versão do aplicativo ({exc}).")
        except Exception as exc:
            self.event_queue.put(("maintenance_error", exc))

    def _maintenance_done(self, payload: object) -> None:
        self.maintenance_busy = False
        if not self.busy:
            self.progress.stop()
            self.progress.configure(mode="determinate")
            self.progress_value_var.set(0)
            self.download_item_var.set("Nenhum download em andamento")
            self.download_metrics_var.set(
                "Progresso, velocidade, tamanho e tempo restante aparecerão aqui."
            )
        self.update_button.configure(state="normal")
        if isinstance(payload, DependencyStatus):
            self.dependency_status = payload
            deno = payload.deno_version or "não instalado"
            self.update_status_var.set(f"Componentes: yt-dlp {payload.yt_dlp_version} | Deno {deno}")
            self.queue_log(f"Componentes prontos: yt-dlp {payload.yt_dlp_version}; Deno {deno}.")

    def _maintenance_error(self, message: str) -> None:
        self.maintenance_busy = False
        if not self.busy:
            self.progress.stop()
            self.progress.configure(mode="determinate")
            self.progress_value_var.set(0)
            self.download_item_var.set("Falha ao verificar componentes")
            self.download_metrics_var.set(message)
        self.update_button.configure(state="normal")
        self.update_status_var.set("Falha na preparação dos componentes")
        self.queue_log(f"Erro na atualização de componentes: {message}")

    def _offer_application_update(self, payload: object) -> None:
        if not isinstance(payload, AppUpdate):
            return
        if self.available_update and self.available_update.version == payload.version:
            return
        self.available_update = payload
        self.update_status_var.set(f"Nova versão disponível: {payload.version}")
        if self.busy:
            self.queue_log("Atualização disponível. Conclua ou pare a fila antes de instalar.")
            return
        answer = messagebox.askyesno(
            APP_NAME,
            f"A versão {payload.version} está disponível.\n\n"
            "Deseja baixar e instalar a atualização agora?",
        )
        if answer:
            self._download_application_update(payload)

    def _download_application_update(self, update: AppUpdate) -> None:
        self.update_button.configure(state="disabled")
        self.progress.stop()
        self.progress.configure(mode="determinate", maximum=100)
        self.progress_value_var.set(0)
        self.download_item_var.set("Atualização do aplicativo")
        self.download_metrics_var.set("Preparando o download do instalador...")
        self.update_status_var.set(f"Baixando versão {update.version}...")

        def worker() -> None:
            try:
                installer = self.app_updater.download(
                    update,
                    progress=lambda received, total: self.event_queue.put(
                        ("download_progress", (received, total))
                    ),
                )
                self.event_queue.put(("application_installer_ready", str(installer)))
            except Exception as exc:
                self.event_queue.put(("application_update_error", exc))

        threading.Thread(target=worker, daemon=True).start()

    def _install_application_update(self, installer: Path) -> None:
        if self.busy:
            self.queue_log("Instalador pronto; conclua ou pare a fila antes de atualizar.")
            return
        try:
            self.update_status_var.set("Abrindo instalador da atualização...")
            self._save_preferences()
            self.app_updater.launch_installer(installer)
            self.queue_log(
                "Atualização iniciada. Após autorizar o Windows, o programa será "
                "reaberto automaticamente."
            )
            self.root.after(250, self._on_close)
        except Exception as exc:
            self.progress.stop()
            self.update_button.configure(state="normal")
            messagebox.showerror(APP_NAME, f"Não foi possível iniciar a atualização:\n{exc}")

    def start_download(self) -> None:
        QueueUI.start_download(self)

    def _get_urls(self) -> list[str]:
        if self.work_mode == "music":
            query = self.music_search_var.get().strip()
            return [query] if query else []
        raw = self.video_url_text.get("1.0", END)
        return [line.strip() for line in raw.splitlines() if line.strip()]

    def _download(self, urls: list[str], folder: Path, format_choice: str) -> None:
        failures = 0
        self.download_completed_files = 0
        try:
            status = self.dependencies.ensure(self.queue_log, force=False)
            self.dependency_status = status
            for index, url in enumerate(urls, start=1):
                self.queue_log(f"[{index}/{len(urls)}] Processando: {url}")
                self._begin_download_item(index, len(urls), url)
                command = self._build_command(status.yt_dlp_path, folder, format_choice, url)
                return_code, output_files = self._run_downloader(command)
                if not self._finalize_downloaded_files(return_code, output_files, format_choice):
                    failures += 1
            if failures:
                self.queue_log(f"Concluído com falha em {failures} de {len(urls)} item(ns).")
            elif not self.download_completed_files:
                self.queue_log("Nenhum arquivo disponível para baixar nesta fila.")
            else:
                self.queue_log("Concluído com sucesso.")
        except Exception as exc:
            failures += 1
            self.queue_log(f"Erro: {exc}")
        finally:
            self.event_queue.put(("download_finished", {
                "failures": failures, "completed": self.download_completed_files,
            }))

    def _finalize_downloaded_files(self, return_code: int, output_files: list[Path], format_choice: str) -> bool:
        # yt-dlp can return 1 for ONE unavailable playlist entry after saving
        # other entries successfully. Those files must still be finalized.
        failed = return_code != 0
        self.finalized_files = []
        for output_file in dict.fromkeys(output_files):
            try:
                self.download_control.checkpoint()
                if not is_audio_format(format_choice):
                    output_file = self._ensure_player_compatibility(output_file) or output_file
                self.finalized_files.append(output_file)
                self.download_completed_files = getattr(self, "download_completed_files", 0) + 1
            except DownloadCancelled:
                raise
            except Exception as exc:
                failed = True
                self.queue_log(f"Erro ao tornar o vídeo compatível ({output_file.name}): {exc}")
        if return_code != 0:
            self.queue_log(
                "Um ou mais itens não puderam ser baixados. Os arquivos concluídos foram preservados; "
                "a fila continua com os próximos disponíveis. Consulte os detalhes acima."
            )
        return not failed

    def _build_command(
        self,
        engine: Path,
        folder: Path,
        format_choice: str,
        url: str,
        *,
        output_template: str | None = None,
        include_cookies: bool = True,
        audio_bitrate: int | None = None,
    ) -> list[str]:
        def compatible_selector(height: int | None = None) -> str:
            height_filter = f"[height<={height}]" if height else ""
            return (
                f"bv*{height_filter}[ext=mp4][vcodec~='^(avc1|h264)']"
                "+ba[ext=m4a][acodec~='^(mp4a|aac)']/"
                f"b{height_filter}[ext=mp4][vcodec~='^(avc1|h264)']"
                "[acodec~='^(mp4a|aac)']/"
                f"bv*{height_filter}+ba/b{height_filter}/best"
            )

        format_map = {
            "Melhor qualidade": "bv*+ba/best",
            "Melhor MP4 compatível": compatible_selector(),
            "2160p (4K)": compatible_selector(2160),
            "1440p (2K)": compatible_selector(1440),
            "1080p": compatible_selector(1080),
            "720p": compatible_selector(720),
            "480p": compatible_selector(480),
            "360p": compatible_selector(360),
            AUDIO_ORIGINAL_FORMAT: "ba/bestaudio/best",
            "Apenas áudio (M4A)": "ba/bestaudio/best",
            "Apenas áudio (MP3)": "ba/bestaudio/best",
            "Apenas áudio (Opus)": "ba/bestaudio/best",
            "Apenas áudio (FLAC)": "ba/bestaudio/best",
            "Apenas áudio (WAV)": "ba/bestaudio/best",
        }
        selected_format = format_map.get(format_choice, compatible_selector())
        selected_output_template = output_template or (
            "%(playlist_index|)s%(playlist_index& - )s"
            "%(title).180s [%(id)s].%(ext)s"
        )

        command = [
            str(engine),
            "--ignore-config",
            "--no-abort-on-error",
            "--newline",
            "--no-color",
            "--progress",
            "--no-quiet",
            "--progress-delta",
            "0.5",
            "--concurrent-fragments",
            str(fragment_count(getattr(self, "download_fragments", 4))),
            "--progress-template",
            PROGRESS_TEMPLATE,
            "--progress-template",
            f"postprocess:{POSTPROCESS_MARKER}%(progress.status)s|%(progress.postprocessor)s",
            "--encoding",
            "utf-8",
            "--windows-filenames",
            "--continue",
            "--retries",
            "5",
            "--fragment-retries",
            "5",
            "--socket-timeout",
            "30",
            "--extractor-retries",
            "2",
            "--abort-on-unavailable-fragments",
            "--remote-components",
            "ejs:github",
            "--print",
            f"after_move:{OUTPUT_MARKER}%(filepath)s",
            "-P",
            str(folder),
            "-o",
            selected_output_template,
            "-f",
            selected_format,
        ]

        options = getattr(self, "download_options", None)
        if options is None and self.playlist_var.get():
            command.extend(["--yes-playlist", "--compat-options", "no-youtube-unavailable-videos"])
        else:
            command.append("--no-playlist")
        if FFMPEG_PATH:
            command.extend(["--ffmpeg-location", FFMPEG_PATH])
        deno = getattr(getattr(self, "dependencies", None), "deno_path", None)
        if deno and Path(deno).is_file():
            command.extend(["--js-runtimes", f"deno:{deno}"])

        # Capítulos
        embed_chapters = (
            options.get("embed_chapters", True)
            if options is not None
            else (self.embed_chapters_var.get() if hasattr(self, "embed_chapters_var") else True)
        )
        split_chapters = (
            options.get("split_chapters", False)
            if options is not None
            else (self.split_chapters_var.get() if hasattr(self, "split_chapters_var") else False)
        )

        codec = audio_codec(format_choice)
        if format_choice == AUDIO_ORIGINAL_FORMAT:
            # Keep the downloaded audio bitstream untouched; even metadata or
            # thumbnail embedding could force a container rewrite.
            pass
        elif is_audio_format(format_choice):
            if codec:
                command.extend([
                    "--extract-audio", "--audio-format", codec,
                    "--audio-quality", f"{audio_bitrate}K" if audio_bitrate else "0",
                ])
            command.extend([
                "--embed-metadata", "--embed-thumbnail", "--convert-thumbnails", "jpg",
            ])
            if split_chapters:
                command.append("--split-chapters")
            elif embed_chapters:
                command.append("--embed-chapters")
            else:
                command.append("--no-embed-chapters")
        else:
            command.extend(["--merge-output-format", "mp4"])
            if split_chapters:
                command.append("--split-chapters")
            elif embed_chapters:
                command.append("--embed-chapters")
            else:
                command.append("--no-embed-chapters")

            # Legendas
            subtitles_enabled = (
                options.get("subtitles_enabled", False)
                if options is not None
                else (self.subtitles_enabled_var.get() if hasattr(self, "subtitles_enabled_var") else False)
            )
            if subtitles_enabled:
                subtitles_embed = (
                    options.get("subtitles_embed", True)
                    if options is not None
                    else (self.subtitles_embed_var.get() if hasattr(self, "subtitles_embed_var") else True)
                )
                subtitles_auto = (
                    options.get("subtitles_auto", False)
                    if options is not None
                    else (self.subtitles_auto_var.get() if hasattr(self, "subtitles_auto_var") else False)
                )
                subtitles_langs = (
                    str(options.get("subtitles_langs") or "pt,pt-BR,en")
                    if options is not None
                    else (self.subtitles_langs_var.get().strip() or "pt,pt-BR,en" if hasattr(self, "subtitles_langs_var") else "pt,pt-BR,en")
                )
                command.extend(["--write-subs", "--sub-langs", subtitles_langs])
                if subtitles_auto:
                    command.append("--write-auto-subs")
                if subtitles_embed:
                    command.append("--embed-subs")
                else:
                    command.extend(["--convert-subs", "srt"])

        # SponsorBlock
        sponsorblock = (
            options.get("sponsorblock", False)
            if options is not None
            else (self.sponsorblock_var.get() if hasattr(self, "sponsorblock_var") else False)
        )
        if sponsorblock:
            command.extend(["--sponsorblock-remove", "all"])

        # Limite de taxa de download (Bandwidth Limiter)
        rate_limit = (
            options.get("rate_limit")
            if options is not None
            else (self.rate_limit_var.get() if hasattr(self, "rate_limit_var") else "")
        )
        rate_str = str(rate_limit or "").strip()
        if rate_str and rate_str.lower() not in {"ilimitado", "none", "0", ""}:
            match = re.search(r"(\d+(?:\.\d+)?)\s*([KkMmGg])?", rate_str)
            if match:
                val = match.group(1)
                unit = (match.group(2) or "M").upper()
                command.extend(["--limit-rate", f"{val}{unit}"])

        # Proxy de rede
        proxy_url = (
            options.get("proxy_url")
            if options is not None
            else (self.proxy_url_var.get().strip() if hasattr(self, "proxy_url_var") else "")
        )
        if proxy_url:
            command.extend(["--proxy", proxy_url])

        if include_cookies:
            if options is not None:
                command.extend(cookie_arguments(options))
            else:
                browser = self.cookies_browser_var.get() if hasattr(self, "cookies_browser_var") else "Nenhum"
                cfile = self.cookies_file_var.get() if hasattr(self, "cookies_file_var") else ""
                command.extend(cookie_arguments({"cookies_browser": browser, "cookies_file": cfile}))

        command.extend(["--", url])
        return command

    def _run_downloader(self, command: list[str]) -> tuple[int, list[Path]]:
        for attempt in range(2):
            try:
                return self._run_downloader_once(command)
            except ProcessInactivityError:
                if attempt:
                    raise RuntimeError(
                        "O mecanismo de download parou de responder após duas tentativas.",
                    )
                self.queue_log(
                    "Download sem resposta; reiniciando automaticamente e preservando o arquivo parcial (1/1)...",
                )
        raise RuntimeError("O mecanismo de download não respondeu.")

    def _run_downloader_once(self, command: list[str]) -> tuple[int, list[Path]]:
        output_files: list[Path] = []
        tracker = ProgressTracker()
        watchdog = {"seconds": DOWNLOAD_START_INACTIVITY_SECONDS}
        process = self.download_control.popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=CREATE_NO_WINDOW,
            env=self.dependencies.runtime_environment(),
        )
        try:
            assert process.stdout is not None
            for line in monitored_lines(process, self.download_control, lambda: watchdog["seconds"]):
                cleaned = line.rstrip()
                if not cleaned:
                    continue
                if cleaned.startswith(OUTPUT_MARKER):
                    output_path = cleaned[len(OUTPUT_MARKER):].strip()
                    if output_path:
                        output_files.append(Path(output_path))
                    continue
                if cleaned.startswith(POSTPROCESS_MARKER):
                    watchdog["seconds"] = None
                    self.event_queue.put(("conversion_progress", {"label": "Finalizando mídia"}))
                    continue
                progress = tracker.parse(cleaned, now=self._download_clock())
                if progress is not None:
                    watchdog["seconds"] = None
                    self._emit_media_progress(progress)
                    continue
                self.queue_log(cleaned)
            self.download_control.checkpoint()
            return process.wait(timeout=10), output_files
        finally:
            close_process(process, self.download_control)

    @staticmethod
    def _stream_details(media_path: Path) -> tuple[str, str, float | None]:
        if not FFMPEG_PATH:
            raise RuntimeError("FFmpeg não está disponível.")
        completed = subprocess.run(
            [FFMPEG_PATH, "-hide_banner", "-i", str(media_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=CREATE_NO_WINDOW,
            timeout=90,
        )
        lines = completed.stdout.splitlines()
        video_line = next((line.strip().lower() for line in lines if "video:" in line.lower()), "")
        audio_line = "\n".join(line.strip().lower() for line in lines if "audio:" in line.lower())
        return video_line, audio_line, duration_from_probe(completed.stdout)

    def _ensure_player_compatibility(self, media_path: Path) -> Path:
        if not media_path.exists() or media_path.suffix.lower() not in VIDEO_EXTENSIONS:
            return media_path
        if not FFMPEG_PATH:
            raise RuntimeError("FFmpeg não foi encontrado para validar o vídeo.")

        self.download_control.checkpoint()
        video_line, audio_line, duration = self._stream_details(media_path)
        if not video_line:
            return media_path

        video_ok, audio_ok = stream_compatibility(video_line, audio_line)
        if video_ok and audio_ok and media_path.suffix.lower() == ".mp4":
            return media_path

        destination = media_path.with_suffix(".mp4")
        if destination != media_path and destination.exists():
            destination = destination.with_name(f"{destination.stem}.c2-{uuid.uuid4().hex[:8]}.mp4")
        temporary = destination.with_name(f".c2-{uuid.uuid4().hex}.mp4")
        if video_ok and audio_ok:
            phase = "Empacotando MP4 sem recodificar"
        elif video_ok:
            phase = "Convertendo apenas o áudio; preservando o vídeo"
        else:
            phase = "Convertendo vídeo para H.264"
        self.queue_log(f"{phase}: {media_path.name}")
        self.event_queue.put(("conversion_progress", {"label": phase}))

        command = [
            FFMPEG_PATH,
            "-y",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-nostdin",
            "-nostats",
            "-progress",
            "pipe:1",
            "-i",
            str(media_path),
            "-map",
            "0:v:0",
            "-map",
            "0:a?",
            "-map_metadata",
            "0",
            *codec_arguments(video_ok, audio_ok),
            "-movflags",
            "+faststart",
            str(temporary),
        ]
        try:
            run_conversion(
                command, self.download_control, duration,
                lambda payload: self.event_queue.put(("conversion_progress", dict(payload, label=phase))),
                creationflags=CREATE_NO_WINDOW,
            )
            if not temporary.exists() or temporary.stat().st_size == 0:
                raise RuntimeError("O FFmpeg não produziu um arquivo válido.")
            self.download_control.checkpoint()
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)

        if media_path != destination:
            media_path.unlink(missing_ok=True)
        self.queue_log(f"Vídeo compatível gerado: {destination.name}")
        return destination

    def _finish_download(self, payload: object = None) -> None:
        self.busy = False
        self.download_control.resume()
        self.pause_button.configure(state="disabled", text="Pausar")
        self._stop_progress_preserving_value()
        failures = int(payload.get("failures", 0)) if isinstance(payload, dict) else 0
        completed = payload.get("completed") if isinstance(payload, dict) else None
        stopped = bool(payload.get("stopped")) if isinstance(payload, dict) else False
        cancelled = int(payload.get("cancelled", 0)) if isinstance(payload, dict) else 0
        self.progress.configure(mode="determinate", maximum=100)
        if not failures and not stopped:
            self.progress_value_var.set(0 if completed == 0 else 100)
        if stopped:
            result = "Fila interrompida — clique em Continuar fila para retomar"
        elif cancelled:
            result = f"Fila processada — {cancelled} vídeo(s) cancelado(s)"
        elif failures:
            result = "Trabalho finalizado com avisos" if completed else "Trabalho finalizado com falhas"
        elif completed == 0:
            result = "Nenhum arquivo disponível para baixar"
        else:
            result = "Trabalho finalizado com sucesso"
        self.download_item_var.set(result)
        self.download_metrics_var.set(
            (f"Arquivos concluídos: {completed}  •  " if completed is not None else "")
            + f"Tempo ativo: {format_duration(self._download_clock() - self.download_job_started_at)}"
            + ("  •  Consulte a atividade para ver os itens não baixados." if failures else "")
        )
        self.download_button.configure(state="normal")
        if hasattr(self, "queue_repository"):
            self.queue_running = False
            self.download_options = None
            self._set_queue_busy(False)
            self._refresh_queue()
        if completed and completed > 0 and not failures and not stopped:
            self._trigger_post_download_action()


def _acquire_single_instance_mutex():
    if os.name != "nt":
        return None
    import ctypes

    handle = ctypes.windll.kernel32.CreateMutexW(None, False, APP_MUTEX)
    if not handle:
        return None
    already_exists = ctypes.windll.kernel32.GetLastError() == 183
    if already_exists:
        ctypes.windll.kernel32.CloseHandle(handle)
        return False
    return handle


def main() -> None:
    restarted_after_update = "--updated" in sys.argv[1:]
    mutex = _acquire_single_instance_mutex()
    if mutex is False:
        root = Tk()
        root.withdraw()
        messagebox.showwarning(APP_NAME, "O C² Video Downloader já está aberto.")
        root.destroy()
        return

    root = Tk()
    app = DownloadApp(root)
    if restarted_after_update:
        root.after(
            900,
            lambda: messagebox.showinfo(
                APP_NAME,
                f"Atualização concluída com sucesso.\n\nVersão instalada: {APP_VERSION}",
            ),
        )
    root.mainloop()
    _ = app, mutex


if __name__ == "__main__":
    main()
