from __future__ import annotations

import json
import math
import os
import queue
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from datetime import datetime
from pathlib import Path
from tkinter import BooleanVar, END, StringVar, Tk, Toplevel, filedialog, messagebox
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
from deezer_auth import validate_deezer_arl
from deezer_catalog import DeezerSearchResult, resolve_deezer_track, search_deezer_catalog
from download_control import DownloadCancelled, DownloadControl
from ui_layout import ScrollablePage, build_brand, configure_fonts, fit_window, wrapping_label
from download_queue import QueueRepository, queue_summary
from queue_ui import QueueUI
from queue_service import cookie_arguments
from media_conversion import codec_arguments, duration_from_probe, run_conversion, stream_compatibility
from music_player import MusicPlayer, MusicPlayerError
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
    "1080p",
    "720p",
    "480p",
    "360p",
    AUDIO_ORIGINAL_FORMAT,
    "Apenas áudio (M4A)",
    "Apenas áudio (MP3)",
    "Apenas áudio (Opus)",
]
MUSIC_DOWNLOAD_FORMATS = [
    AUDIO_ORIGINAL_FORMAT,
    "Apenas áudio (M4A)",
    "Apenas áudio (MP3)",
    "Apenas áudio (Opus)",
]
DOWNLOAD_FORMATS = VIDEO_DOWNLOAD_FORMATS
BROWSERS = ["Nenhum", "Chrome", "Edge", "Firefox", "Brave", "Opera", "Vivaldi"]
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
        self._catalog_cover_image = None
        self._catalog_cover_key = None
        self._playing_item_id = None
        self._catalog_playing_active = False
        self._video_preview_after = None
        self._last_video_preview_url = None
        self._video_cover_image = None
        self.download_fragments = fragment_count(self.fragments_var.get())
        self.download_control = DownloadControl()
        self.update_status_var = StringVar(value="Componentes ainda não verificados")
        self.download_item_var = StringVar(value="Nenhum download em andamento")
        self.download_metrics_var = StringVar(
            value="Aguardando início do download."
        )

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
        self.current_music_item_id = None
        self._music_cover_image = None

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
        self._poll_queues()
        self.root.after(700, self.start_maintenance)

    def _build_ui(self) -> None:
        shell = ttk.Frame(self.root, padding=10)
        shell.pack(fill="both", expand=True)

        header = ttk.Frame(shell)
        header.pack(fill="x", pady=(0, 8))
        try:
            self._build_site_logo(header).pack(side="left", padx=(0, 12))
        except Exception as exc:
            self.queue_log(f"Aviso: não foi possível carregar a logo ({exc}).")
        ttk.Separator(header, orient="vertical").pack(side="left", fill="y", padx=(0, 12))
        ttk.Label(
            header,
            text="C² Downloader",
            font=(self.display_family, 15, "bold"),
            foreground="#172b4d",
        ).pack(side="left")

        self.tabs = ttk.Notebook(shell)
        self.tabs.pack(fill="both", expand=True)

        self.music_page = ttk.Frame(self.tabs, padding=10)
        self.video_page = ttk.Frame(self.tabs, padding=10)
        self.queue_page = ttk.Frame(self.tabs, padding=10)
        self.completed_page = ttk.Frame(self.tabs, padding=10)
        self.settings_page = ScrollablePage(self.tabs)
        self.activity_page = ttk.Frame(self.tabs, padding=10)

        self.tabs.add(self.music_page, text="  Música  ")
        self.tabs.add(self.video_page, text="  Vídeo  ")
        self.tabs.add(self.queue_page, text="  Fila  ")
        self.tabs.add(self.completed_page, text="  Concluídos  ")
        self.tabs.add(self.settings_page, text="  Configurações  ")
        self.tabs.add(self.activity_page, text="  Atividade  ")

        # Música: pesquisa instantânea e resultados sem precisar de botão Buscar.
        search_frame = ttk.LabelFrame(self.music_page, text="Pesquisar na Deezer", padding=10)
        search_frame.pack(fill="x", pady=(0, 8))
        search_row = ttk.Frame(search_frame)
        search_row.pack(fill="x")
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
        ttk.Label(
            search_frame,
            textvariable=self.music_search_status_var,
            foreground="#596579",
        ).pack(anchor="w", pady=(6, 0))

        result_area = ttk.Panedwindow(self.music_page, orient="horizontal")
        result_area.pack(fill="both", expand=True, pady=(0, 8))

        result_list = ttk.Frame(result_area)
        result_detail = ttk.LabelFrame(result_area, text="Detalhes", padding=10)
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
        result_buttons.pack(fill="x", pady=(8, 0))
        self.catalog_load_button = ttk.Button(
            result_buttons,
            text="Adicionar à fila",
            command=self._load_selected_catalog_result,
            state="disabled",
        )
        self.catalog_load_button.pack(side="left")
        self.catalog_download_button = ttk.Button(
            result_buttons,
            text="Baixar",
            command=self._download_selected_catalog_result,
            state="disabled",
        )
        self.catalog_download_button.pack(side="left", padx=(6, 0))
        self.catalog_play_button = ttk.Button(
            result_buttons,
            text="▶ Reproduzir prévia",
            command=self._toggle_catalog_playback,
            state="disabled",
        )
        self.catalog_play_button.pack(side="left", padx=(6, 0))
        self.catalog_stop_button = ttk.Button(
            result_buttons,
            text="⏹ Parar",
            command=self.stop_music,
            state="disabled",
        )
        self.catalog_stop_button.pack(side="left", padx=(6, 0))
        self.catalog_open_button = ttk.Button(
            result_buttons,
            text="Abrir no Deezer",
            command=self._open_selected_catalog_result,
            state="disabled",
        )
        self.catalog_open_button.pack(side="left", padx=(6, 0))

        options = ttk.LabelFrame(self.music_page, text="Download e organização", padding=8)
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

        # Vídeo: entrada e opções em uma tela própria.
        video_input = ttk.LabelFrame(self.video_page, text="Links de vídeo / playlists", padding=10)
        video_input.pack(fill="x", pady=(0, 8))
        self.video_url_text = self._make_text(video_input, height=3)
        self.video_url_text.pack(fill="both", expand=True)
        wrapping_label(
            video_input,
            text="Cole um link por linha. YouTube, Kanal D, JW.ORG e outras fontes compatíveis com yt-dlp.",
            foreground="#596579",
        )
        self.video_url_text.bind("<KeyRelease>", self._schedule_video_preview_check)
        self.video_url_text.bind("<<Paste>>", lambda e: self.root.after(100, self._schedule_video_preview_check))

        # Card de prévia automática de vídeo
        self.video_preview_frame = ttk.LabelFrame(self.video_page, text="Prévia do vídeo", padding=8)
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

        video_options = ttk.LabelFrame(self.video_page, text="Opções", padding=8)
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
        ).pack(side="left", padx=(6, 0))

        # Fila em aba própria: elimina a maior parte do scroll da tela de trabalho.
        self._build_episode_list(self.queue_page)
        actions = self.episode_actions
        actions.pack(fill="x", pady=(0, 8))
        self.download_button = ttk.Button(actions, text="Baixar", command=self.start_download)
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
        self.progress = ttk.Progressbar(progress_frame, mode="determinate", maximum=100, value=0)
        self.progress.pack(fill="x", pady=(0, 6))
        wrapping_label(progress_frame, textvariable=self.download_metrics_var, foreground="#3f4f5f")

        self._build_completed_list(self.completed_page)

        settings = self.settings_page.body
        speed_frame = ttk.LabelFrame(settings, text="Desempenho", padding=10)
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
        ).pack(side="left")
        wrapping_label(
            speed_frame,
            text="HLS/DASH: use 4 normalmente; reduza para 1 ou 2 se houver falhas de conexão.",
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
            command=self._save_preferences,
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

        activity = self.activity_page
        ttk.Button(activity, text="Limpar atividade", command=self.clear_log).pack(anchor="e", pady=(0, 8))
        log_frame = ttk.Frame(activity)
        log_frame.pack(fill="both", expand=True)
        self.log = self._make_text(log_frame, height=20)
        self.log.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(log_frame, command=self.log.yview)
        scrollbar.pack(side="right", fill="y")
        self.log.configure(yscrollcommand=scrollbar.set, state="disabled")

        self._build_music_details(self.music_page)
        self._apply_work_mode(self.work_mode, initial=True)
        self.tabs.select(self.music_page if self.work_mode == "music" else self.video_page)
        self.tabs.bind("<<NotebookTabChanged>>", self._on_main_tab_changed)

        self.music_search_var.trace_add("write", self._schedule_music_search)
        self.music_search_type_var.trace_add("write", self._schedule_music_search)
        self.resolution_var.trace_add("write", self._on_resolution_var_changed)
        self.folder_var.trace_add("write", self._on_folder_var_changed)
        self.audio_bitrate_mode_var.trace_add("write", self._update_audio_controls)
        self.music_results_tree.bind("<<TreeviewSelect>>", self._show_selected_catalog_result)
        self.music_results_tree.bind("<Double-1>", lambda _event: self._load_selected_catalog_result())
        self.music_search_entry.bind("<Return>", lambda _event: self._load_selected_catalog_result())
        self.music_search_entry.focus_set()
        self._update_audio_controls()
        if self.deezer_arl_var.get().strip():
            self.root.after(400, lambda: self._check_deezer_arl(show_dialog=False))

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
            if self.resolution_var.get() not in MUSIC_DOWNLOAD_FORMATS:
                self.resolution_var.set(self.music_format_var.get() if hasattr(self, "music_format_var") else "Apenas áudio (MP3)")
            if hasattr(self, "music_folder_var"):
                self.folder_var.set(self.music_folder_var.get())
            if hasattr(self, "analyze_button"):
                self.analyze_button.configure(text="Atualizar fila")
            if not initial:
                self.download_item_var.set("Modo Música")
                self.download_metrics_var.set("Selecione um resultado da pesquisa para carregar na fila.")
        else:
            if self.resolution_var.get() not in VIDEO_DOWNLOAD_FORMATS:
                self.resolution_var.set(self.video_format_var.get() if hasattr(self, "video_format_var") else "Melhor MP4 compatível")
            if hasattr(self, "video_folder_var"):
                self.folder_var.set(self.video_folder_var.get())
            if hasattr(self, "analyze_button"):
                self.analyze_button.configure(text="Listar links")
            if not initial:
                self.download_item_var.set("Modo Vídeo")
                self.download_metrics_var.set("Cole links de vídeos ou playlists para começar.")
        self._update_audio_controls()

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

    def _on_main_tab_changed(self, _event=None) -> None:
        selected = self.tabs.select()
        if selected == str(self.music_page):
            if self.work_mode != "music":
                self.resolution_var.set(self.music_format_var.get())
                self.folder_var.set(self.music_folder_var.get())
                self._apply_work_mode("music")
        elif selected == str(self.video_page):
            if self.work_mode != "video":
                self.resolution_var.set(self.video_format_var.get())
                self.folder_var.set(self.video_folder_var.get())
                self._apply_work_mode("video")
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
        self.catalog_load_button.configure(state="disabled")
        if hasattr(self, "catalog_download_button"):
            self.catalog_download_button.configure(state="disabled")
        self.catalog_play_button.configure(state="disabled", text="▶ Reproduzir prévia")
        if hasattr(self, "catalog_stop_button"):
            self.catalog_stop_button.configure(state="disabled")
        self.catalog_open_button.configure(state="disabled")
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
        if query.lower().startswith(("http://", "https://")):
            self._clear_music_search_results()
            if "deezer.com/" in query.lower():
                self.music_search_status_var.set("Link Deezer detectado. Pressione Enter para carregar na fila.")
            else:
                self.music_search_status_var.set("Na aba Música, use nome de música, artista, álbum, playlist ou link Deezer.")
            return
        if len(query) < 2:
            self._clear_music_search_results()
            self.music_search_status_var.set("Digite pelo menos 2 caracteres.")
            return

        self.music_search_status_var.set("Pesquisando...")
        self._music_search_after = self.root.after(350, self._start_music_search)

    def _start_music_search(self) -> None:
        self._music_search_after = None
        query = self.music_search_var.get().strip()
        if len(query) < 2 or query.lower().startswith(("http://", "https://")):
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

        def worker():
            try:
                results = search_deezer_catalog(query, kind=kind, limit=20)
                self.event_queue.put(("music_search_results", {
                    "generation": generation,
                    "query": query,
                    "results": results,
                    "error": "",
                }))
            except Exception as exc:
                self.event_queue.put(("music_search_results", {
                    "generation": generation,
                    "query": query,
                    "results": (),
                    "error": str(exc),
                }))

        threading.Thread(target=worker, daemon=True).start()

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
        self.music_search_status_var.set(
            f"{count} resultado(s). Selecione ou dê duplo clique para carregar."
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
            self.catalog_load_button.configure(state="disabled")
            self.catalog_download_button.configure(state="disabled")
            self.catalog_play_button.configure(state="disabled", text="▶ Reproduzir prévia")
            if hasattr(self, "catalog_stop_button"):
                self.catalog_stop_button.configure(state="disabled")
            self.catalog_open_button.configure(state="disabled")
            return

        self.catalog_title_var.set(result.title)
        self.catalog_subtitle_var.set(result.subtitle)
        self.catalog_type_var.set(result.kind_label)
        self.catalog_load_button.configure(state="normal")
        self.catalog_download_button.configure(state="normal")
        self.catalog_open_button.configure(state="normal")
        catalog_id = f"catalog_{result.item_id}"
        is_current = getattr(self, "_playing_item_id", None) == catalog_id
        is_playing = is_current and self.music_player.is_playing()
        self.catalog_play_button.configure(
            state="normal" if result.kind == "track" else "disabled",
            text="⏸ Pausar prévia" if is_playing else "▶ Reproduzir prévia",
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
            if query and "deezer.com/" in query.lower():
                self._apply_work_mode("music", initial=True)
                self._prepare_sources([query], False)
            return
        self._apply_work_mode("music", initial=True)
        self._prepare_sources([result.page_url], False)

    def _download_selected_catalog_result(self) -> None:
        result = self._selected_catalog_result()
        if result is None:
            query = self.music_search_var.get().strip()
            if query and "deezer.com/" in query.lower():
                self._apply_work_mode("music", initial=True)
                self._prepare_sources([query], True)
            return
        self._apply_work_mode("music", initial=True)
        self._prepare_sources([result.page_url], True)

    def _play_selected_catalog_result(self) -> None:
        self._toggle_catalog_playback()

    def _open_selected_catalog_result(self) -> None:
        result = self._selected_catalog_result()
        if result is not None:
            webbrowser.open(result.page_url)

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
        self.music_play_button = ttk.Button(buttons, text="▶ Reproduzir", command=self.play_selected_music)
        self.music_play_button.pack(side="left")
        self.music_stop_button = ttk.Button(buttons, text="⏹ Parar", command=self.stop_music)
        self.music_stop_button.pack(side="left", padx=(6, 0))
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

        files = [Path(value) for value in item.get("files", [])]
        local = next((path for path in files if path.is_file()), None)
        item_copy = dict(item)

        def worker():
            try:
                if local is not None:
                    mode = self.music_player.play_file(local)
                    message = "Reproduzindo arquivo local." if mode == "internal" else "Áudio aberto no player padrão do Windows."
                else:
                    track = resolve_deezer_track(str(item_copy.get("media_id") or ""))
                    if not track.preview_url:
                        raise MusicPlayerError("A Deezer não disponibilizou prévia pública para esta faixa.")
                    self.music_player.play_preview(track.preview_url)
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
            return

        catalog_id = f"catalog_{result.item_id}"

        if getattr(self, "_playing_item_id", None) == catalog_id and self.music_player.is_playing():
            self.music_player.pause()
            self.download_metrics_var.set(f"Prévia pausada: {result.title}")
            self._update_player_buttons()
            return

        if getattr(self, "_playing_item_id", None) == catalog_id and self.music_player.is_paused():
            self.music_player.resume()
            self.download_metrics_var.set(f"Reproduzindo prévia: {result.title}")
            self._update_player_buttons()
            return

        def worker():
            try:
                track = resolve_deezer_track(result.item_id)
                if not track.preview_url:
                    raise MusicPlayerError("A Deezer não disponibilizou prévia pública para esta faixa.")
                self.music_player.play_preview(track.preview_url)
                self._playing_item_id = catalog_id
                self._catalog_playing_active = True
                self.event_queue.put(("music_player_status", f"Reproduzindo prévia: {result.title}"))
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
            self.download_metrics_var.set("Reprodução interrompida.")
            self._update_player_buttons()
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Não foi possível parar a reprodução:\n{exc}")

    def _update_player_buttons(self) -> None:
        is_playing = self.music_player.is_playing()
        cat_active = getattr(self, "_catalog_playing_active", False)
        if hasattr(self, "music_play_button"):
            self.music_play_button.configure(
                text="⏸ Pausar" if (is_playing and not cat_active) else "▶ Reproduzir"
            )
        if hasattr(self, "play_completed_button"):
            self.play_completed_button.configure(
                text="⏸ Pausar" if (is_playing and not cat_active) else "▶ Reproduzir"
            )
        if hasattr(self, "catalog_play_button"):
            self.catalog_play_button.configure(
                text="⏸ Pausar prévia" if (is_playing and cat_active) else "▶ Reproduzir prévia"
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
            self.folder_var.set(selected)
            self._save_preferences()

    def choose_music_folder(self) -> None:
        self.choose_folder(target="music")

    def choose_video_folder(self) -> None:
        self.choose_folder(target="video")

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
        self.root.destroy()

    def toggle_pause(self) -> None:
        if not self.busy:
            return
        try:
            if self.download_control.paused:
                self.download_control.resume()
                self.pause_button.configure(text="Pausar")
                self.download_metrics_var.set(self._metrics_before_pause)
                self.queue_log("Continuando o trabalho do ponto em que foi pausado.")
            else:
                self.download_control.pause()
                self._metrics_before_pause = self.download_metrics_var.get()
                self._stop_progress_preserving_value()
                self.pause_button.configure(text="Continuar")
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
            if show_dialog:
                messagebox.showinfo(
                    APP_NAME,
                    "Nenhum cookie ARL inserido.\n\n"
                    "O aplicativo continuará funcionando normalmente com as prévias oficiais de 30s.",
                )
            return

        self.deezer_status_var.set("● Verificando credenciais na Deezer...")

        def worker():
            info = validate_deezer_arl(arl)

            def callback():
                if info.get("valid"):
                    self.deezer_status_var.set(f"● Conectado: {info['user_name']} ({info['plan']})")
                    self._save_preferences()
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
        self.progress.configure(mode="indeterminate", maximum=100, value=0)
        self.progress.start(10)
        self.download_item_var.set(label)
        self.download_metrics_var.set("Aguarde enquanto a operação é preparada...")

    def _stop_progress_preserving_value(self):
        value = self.progress["value"]
        self.progress.stop()
        self.progress["value"] = value

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
        self.progress["value"] = (index - 1) * 100 / total
        if getattr(self, "queue_running", False):
            self.progress["value"] = queue_summary(self.queue_items)["overall"]
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
        self.progress["value"] = overall

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
                self.progress.configure(mode="indeterminate", maximum=100, value=0)
                self.progress.start(15)
            self.download_metrics_var.set("Download recebido. Finalizando o arquivo; aguarde...")
        else:
            if not getattr(self, "queue_running", False):
                self.progress.configure(mode="determinate", maximum=100, value=float(percent))
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
                        self.progress["value"] = percentage
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
                elif event == "catalog_cover_ready":
                    self._apply_catalog_cover(payload)
                elif event == "music_cover_ready":
                    self._apply_music_cover(payload)
                elif event == "video_preview_ready":
                    self._apply_video_preview(payload)
                elif event == "music_player_status":
                    self.download_metrics_var.set(str(payload))
                elif event == "music_player_error":
                    messagebox.showerror(APP_NAME, str(payload))
        except queue.Empty:
            pass

        self._update_player_buttons()
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
            self.progress.configure(mode="determinate", value=0)
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
            self.progress.configure(mode="determinate", value=0)
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
        self.progress.configure(mode="determinate", maximum=100, value=0)
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
            self.app_updater.launch_installer(installer)
            self.root.after(800, self.root.destroy)
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
            "1080p": compatible_selector(1080),
            "720p": compatible_selector(720),
            "480p": compatible_selector(480),
            "360p": compatible_selector(360),
            AUDIO_ORIGINAL_FORMAT: "ba/bestaudio/best",
            "Apenas áudio (M4A)": "ba/bestaudio/best",
            "Apenas áudio (MP3)": "ba/bestaudio/best",
            "Apenas áudio (Opus)": "ba/bestaudio/best",
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
                "--no-embed-chapters",
            ])
        else:
            command.extend(["--merge-output-format", "mp4"])

        if include_cookies:
            if options is not None:
                command.extend(cookie_arguments(options))
            else:
                command.extend(cookie_arguments({"cookies_browser": self.cookies_browser_var.get(), "cookies_file": self.cookies_file_var.get()}))

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
            self.progress["value"] = 0 if completed == 0 else 100
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
    mutex = _acquire_single_instance_mutex()
    if mutex is False:
        root = Tk()
        root.withdraw()
        messagebox.showwarning(APP_NAME, "O C² Video Downloader já está aberto.")
        root.destroy()
        return

    root = Tk()
    app = DownloadApp(root)
    root.mainloop()
    _ = app, mutex


if __name__ == "__main__":
    main()
