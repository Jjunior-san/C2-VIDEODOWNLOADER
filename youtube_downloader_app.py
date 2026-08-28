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
from pathlib import Path
from tkinter import BooleanVar, END, StringVar, Tk, filedialog, messagebox
from tkinter import ttk

from app_config import APP_MUTEX, APP_NAME, APP_VERSION
from download_control import DownloadCancelled, DownloadControl
from ui_layout import ScrollablePage, build_brand, configure_fonts, fit_window, wrapping_label
from download_queue import QueueRepository, queue_summary
from queue_ui import QueueUI
from queue_service import cookie_arguments
from media_conversion import codec_arguments, duration_from_probe, run_conversion, stream_compatibility
from c2_update import (
    ApplicationUpdater,
    AppUpdate,
    CREATE_NO_WINDOW,
    DATA_DIR,
    DependencyManager,
    DependencyStatus,
)

try:
    import imageio_ffmpeg

    FFMPEG_PATH = imageio_ffmpeg.get_ffmpeg_exe()
except (ImportError, RuntimeError):
    FFMPEG_PATH = None

SETTINGS_FILE = DATA_DIR / "settings.json"
QUEUE_FILE = DATA_DIR / "downloads.sqlite3"
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
DOWNLOAD_FORMATS = [
    "Melhor MP4 compatível",
    "Melhor qualidade",
    "1080p",
    "720p",
    "480p",
    "360p",
    "Apenas áudio (M4A)",
]
BROWSERS = ["Nenhum", "Chrome", "Edge", "Firefox", "Brave", "Opera", "Vivaldi"]

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
        saved_folder = str(
            self.user_settings.get("download_folder")
            or (Path.home() / "Downloads")
        )
        saved_format = str(self.user_settings.get("format") or "Melhor MP4 compatível")
        if saved_format not in DOWNLOAD_FORMATS:
            saved_format = "Melhor MP4 compatível"
        saved_browser = str(self.user_settings.get("cookies_browser") or "Nenhum")
        if saved_browser not in BROWSERS:
            saved_browser = "Nenhum"

        self.folder_var = StringVar(value=saved_folder)
        self.playlist_var = BooleanVar(value=bool(self.user_settings.get("playlist", True)))
        self.resolution_var = StringVar(value=saved_format)
        self.cookies_browser_var = StringVar(value=saved_browser)
        self.cookies_file_var = StringVar()
        self.fragments_var = StringVar(value=str(fragment_count(self.user_settings.get("concurrent_fragments", 4))))
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

        self.dependencies = DependencyManager()
        self.app_updater = ApplicationUpdater(self.dependencies)
        queue_error = None
        try:
            self.queue_repository = QueueRepository(QUEUE_FILE)
        except Exception as exc:
            self.queue_repository = None
            queue_error = str(exc)

        self._build_ui()
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
        shell = ttk.Frame(self.root, padding=12)
        shell.pack(fill="both", expand=True)
        header = ttk.Frame(shell)
        header.pack(fill="x", pady=(0, 10))
        try:
            self._build_site_logo(header).pack(side="left", padx=(0, 12))
        except Exception as exc:
            self.queue_log(f"Aviso: não foi possível carregar a logo ({exc}).")
        ttk.Separator(header, orient="vertical").pack(side="left", fill="y", padx=(0, 12))
        title = ttk.Label(header, text="Video Downloader", font=(self.display_family, 15, "bold"),
                          width=1, foreground="#172b4d", wraplength=300)
        title.pack(side="left", fill="x", expand=True)
        title.bind("<Configure>", lambda event: title.configure(wraplength=max(1, event.width)))

        self.tabs = ttk.Notebook(shell)
        self.tabs.pack(fill="both", expand=True)
        self.download_page = ScrollablePage(self.tabs)
        self.settings_page = ScrollablePage(self.tabs)
        self.activity_page = ScrollablePage(self.tabs)
        self.tabs.add(self.download_page, text="  Downloads  ")
        self.tabs.add(self.settings_page, text="  Configurações  ")
        self.tabs.add(self.activity_page, text="  Atividade  ")
        frame = self.download_page.body

        ttk.Label(frame, text="Links dos vídeos ou playlists", font=(self.text_family, 10, "bold")).pack(anchor="w")
        url_row = ttk.Frame(frame)
        url_row.pack(fill="x", pady=(4, 10))
        self.url_text = self._make_text(url_row, height=2)
        self.url_text.pack(side="left", fill="x", expand=True)
        url_scroll = ttk.Scrollbar(url_row, command=self.url_text.yview)
        url_scroll.pack(side="right", fill="y")
        self.url_text.configure(yscrollcommand=url_scroll.set)

        ttk.Label(frame, text="Pasta de destino").pack(anchor="w")
        folder_row = ttk.Frame(frame)
        folder_row.pack(fill="x", pady=(4, 10))
        ttk.Entry(folder_row, textvariable=self.folder_var).pack(side="left", fill="x", expand=True)
        ttk.Button(folder_row, text="Escolher", command=self.choose_folder).pack(side="left", padx=(8, 0))

        format_frame = ttk.Frame(frame)
        format_frame.pack(fill="x", pady=(0, 10))
        ttk.Label(format_frame, text="Formato:").pack(side="left", padx=(0, 8))
        ttk.Combobox(
            format_frame, textvariable=self.resolution_var,
            values=DOWNLOAD_FORMATS, state="readonly", width=24,
        ).pack(side="left")
        ttk.Checkbutton(format_frame, text="Baixar playlist/álbum", variable=self.playlist_var).pack(side="left", padx=(12, 0))

        self._build_episode_list(frame)

        actions = self.episode_actions
        actions.pack(fill="x", pady=(0, 12))
        self.download_button = ttk.Button(actions, text="Baixar", command=self.start_download)
        self.download_button.pack(side="left")
        self.pause_button = ttk.Button(actions, text="Pausar", command=self.toggle_pause, state="disabled")
        self.pause_button.pack(side="left", padx=(8, 0))
        self.stop_button = ttk.Button(actions, text="Parar fila", command=self.stop_queue, state="disabled")
        self.stop_button.pack(side="left", padx=(8, 0))

        progress_frame = ttk.LabelFrame(frame, text="Progresso", padding=10)
        progress_frame.pack(fill="x", pady=(0, 10))
        wrapping_label(progress_frame, textvariable=self.download_item_var, font=(self.text_family, 10, "bold"))
        self.progress = ttk.Progressbar(progress_frame, mode="determinate", maximum=100, value=0)
        self.progress.pack(fill="x", pady=(0, 8))
        wrapping_label(progress_frame, textvariable=self.download_metrics_var, foreground="#3f4f5f")

        activity = self.activity_page.body
        ttk.Button(activity, text="Limpar atividade", command=self.clear_log).pack(anchor="e", pady=(0, 8))
        log_frame = ttk.Frame(activity)
        log_frame.pack(fill="both", expand=True)
        self.log = self._make_text(log_frame, height=15)
        self.log.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(log_frame, command=self.log.yview)
        scrollbar.pack(side="right", fill="y")
        self.log.configure(yscrollcommand=scrollbar.set, state="disabled")

        settings = self.settings_page.body
        speed_frame = ttk.LabelFrame(settings, text="Desempenho", padding=10)
        speed_frame.pack(fill="x", pady=(0, 12))
        speed_row = ttk.Frame(speed_frame)
        speed_row.pack(fill="x", pady=(0, 8))
        ttk.Label(speed_row, text="Fragmentos simultâneos:").pack(side="left", padx=(0, 8))
        ttk.Combobox(speed_row, textvariable=self.fragments_var, values=FRAGMENT_CHOICES,
                     state="readonly", width=3).pack(side="left")
        wrapping_label(speed_frame, text="HLS/DASH: use 4 normalmente; reduza para 1 ou 2 se houver falhas de conexão.", foreground="#596579")

        cookies_frame = ttk.LabelFrame(settings, text="Acesso a sites com login", padding=10)
        cookies_frame.pack(fill="x", pady=(0, 12))
        browser_row = ttk.Frame(cookies_frame)
        browser_row.pack(fill="x", pady=(0, 8))
        ttk.Label(browser_row, text="Cookies do navegador:").pack(side="left", padx=(0, 8))
        ttk.Combobox(browser_row, textvariable=self.cookies_browser_var, values=BROWSERS,
                     state="readonly", width=14).pack(side="left")
        wrapping_label(cookies_frame, text="Feche o Chrome/Edge antes de usar seus cookies. Fontes públicas do Kanal D não precisam deles.", foreground="#596579")
        ttk.Label(cookies_frame, text="Ou arquivo cookies.txt:").pack(anchor="w")
        file_row = ttk.Frame(cookies_frame)
        file_row.pack(fill="x", pady=(4, 0))
        ttk.Entry(file_row, textvariable=self.cookies_file_var, width=12).pack(side="left", fill="x", expand=True)
        ttk.Button(file_row, text="Selecionar", command=self.choose_cookies_file).pack(side="left", padx=(8, 0))
        ttk.Button(file_row, text="Limpar", command=lambda: self.cookies_file_var.set("")).pack(side="left", padx=(8, 0))

        about_frame = ttk.LabelFrame(settings, text="Aplicativo", padding=10)
        about_frame.pack(fill="x")
        wrapping_label(about_frame, text=f"{APP_NAME} • Versão {APP_VERSION}", font=(self.text_family, 10, "bold"))
        wrapping_label(about_frame, text=f"Fontes: {self.text_family} / {self.display_family}", foreground="#596579")
        wrapping_label(about_frame, textvariable=self.update_status_var, foreground="#596579")
        self.update_button = ttk.Button(about_frame, text="Verificar atualizações", command=lambda: self.start_maintenance(True))
        self.update_button.pack(anchor="w", pady=(0, 12))
        wrapping_label(about_frame, text="Nas playlists, itens privados, removidos ou indisponíveis não interrompem os próximos vídeos. Veja os avisos no registro de atividade.", foreground="#596579")
        wrapping_label(about_frame, text="Cole um link por linha. Baixe apenas mídias suas, livres ou que você tenha permissão para baixar.", foreground="#596579")

    def _build_site_logo(self, parent) -> ttk.Frame:
        return build_brand(parent, resource_path("assets/c2_logo_horizontal.png"), self.display_family)

    def _make_text(self, parent, height: int):
        from tkinter import Text

        return Text(parent, height=height, width=1, wrap="word", font=(self.text_family, 10),
                    relief="solid", borderwidth=1, padx=8, pady=6)

    def choose_folder(self) -> None:
        initial = Path(self.folder_var.get().strip() or str(Path.home()))
        if not initial.exists():
            initial = Path.home()
        selected = filedialog.askdirectory(initialdir=str(initial))
        if selected:
            self.folder_var.set(selected)
            self._save_preferences()

    def _save_preferences(self) -> None:
        settings: dict[str, object] = {
            "download_folder": self.folder_var.get().strip() or str(Path.home() / "Downloads"),
            "format": self.resolution_var.get(),
            "playlist": bool(self.playlist_var.get()),
            "cookies_browser": self.cookies_browser_var.get(),
            "concurrent_fragments": fragment_count(self.fragments_var.get()),
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

    def clear_log(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", END)
        self.log.configure(state="disabled")

    def write_log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert(END, text.rstrip() + "\n")
        self.log.see(END)
        self.log.configure(state="disabled")

    def queue_log(self, text: str) -> None:
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

    def _report_direct_progress(self, downloaded: int, total: int | None) -> None:
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
        except queue.Empty:
            pass

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
        raw = self.url_text.get("1.0", END)
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
                if format_choice != "Apenas áudio (M4A)":
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
            "Apenas áudio (M4A)": "ba/bestaudio/best",
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
            "10",
            "--fragment-retries",
            "10",
            "--abort-on-unavailable-fragments",
            "--merge-output-format",
            "mp4",
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
        if format_choice == "Apenas áudio (M4A)":
            command.extend(["--extract-audio", "--audio-format", "m4a", "--audio-quality", "0"])

        if include_cookies:
            if options is not None:
                command.extend(cookie_arguments(options))
            else:
                command.extend(cookie_arguments({"cookies_browser": self.cookies_browser_var.get(), "cookies_file": self.cookies_file_var.get()}))

        command.extend(["--", url])
        return command

    def _run_downloader(self, command: list[str]) -> tuple[int, list[Path]]:
        output_files: list[Path] = []
        tracker = ProgressTracker()
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
            for line in process.stdout:
                self.download_control.checkpoint()
                cleaned = line.rstrip()
                if not cleaned:
                    continue
                if cleaned.startswith(OUTPUT_MARKER):
                    output_path = cleaned[len(OUTPUT_MARKER):].strip()
                    if output_path:
                        output_files.append(Path(output_path))
                    continue
                if cleaned.startswith(POSTPROCESS_MARKER):
                    self.event_queue.put(("conversion_progress", {"label": "Finalizando mídia"}))
                    continue
                progress = tracker.parse(cleaned, now=self._download_clock())
                if progress is not None:
                    self._emit_media_progress(progress)
                    continue
                self.queue_log(cleaned)
            self.download_control.checkpoint()
            return process.wait(), output_files
        finally:
            if process.poll() is None:
                self.download_control.terminate_active()
            process.wait()
            if process.stdout:
                process.stdout.close()
            self.download_control.release(process)

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
