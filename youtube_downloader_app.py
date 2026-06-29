from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path
from tkinter import BooleanVar, Canvas, END, PhotoImage, StringVar, Tk, filedialog, messagebox
from tkinter import ttk

from app_config import APP_MUTEX, APP_NAME, APP_VERSION
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
OUTPUT_MARKER = "__C2_OUTPUT__:"
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

SUPPORTED_HINT = (
    "YouTube, Instagram, Facebook, TikTok, Vimeo, X/Twitter, Twitch, "
    "Dailymotion e outros players suportados pelo yt-dlp."
)


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


class DownloadApp:
    def __init__(self, root: Tk) -> None:
        self.root = root
        self.root.title(f"{APP_NAME} {APP_VERSION}")
        self.root.geometry("860x720")
        self.root.minsize(760, 640)

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
        self.update_status_var = StringVar(value="Componentes ainda não verificados")

        self.busy = False
        self.maintenance_busy = False
        self.log_queue: queue.Queue[str] = queue.Queue()
        self.event_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.logo_image = None
        self.logo_source_image = None
        self.logo_canvas: Canvas | None = None
        self.logo_text_item: int | None = None
        self.logo_exponent_item: int | None = None
        self.logo_tagline_item: int | None = None
        self.logo_target = "SISTEMAS"
        self.logo_index = 0
        self.logo_cursor_visible = True
        self.logo_blink_count = 0
        self.available_update: AppUpdate | None = None
        self.dependency_status: DependencyStatus | None = None

        self.dependencies = DependencyManager()
        self.app_updater = ApplicationUpdater(self.dependencies)

        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._poll_queues()
        self.root.after(700, self.start_maintenance)

    def _build_ui(self) -> None:
        frame = ttk.Frame(self.root, padding=18)
        frame.pack(fill="both", expand=True)

        header = ttk.Frame(frame)
        header.pack(fill="x", pady=(0, 12))
        try:
            self._build_site_logo(header).pack(side="left", padx=(0, 14))
        except Exception as exc:
            self.queue_log(f"Aviso: não foi possível carregar a logo animada ({exc}).")
        title_box = ttk.Frame(header)
        title_box.pack(side="left", fill="x", expand=True)
        ttk.Label(title_box, text=APP_NAME, font=("Segoe UI", 18, "bold")).pack(anchor="w")
        ttk.Label(title_box, text=SUPPORTED_HINT, foreground="#555555", wraplength=650).pack(anchor="w")
        ttk.Label(title_box, text=f"Versão {APP_VERSION}", foreground="#666666").pack(anchor="w")

        ttk.Label(
            frame,
            text="Baixe apenas mídias que sejam suas, livres, ou que você tenha permissão para baixar.",
            foreground="#8a5a00",
        ).pack(anchor="w", pady=(0, 12))

        ttk.Label(frame, text="URLs de vídeos, posts, reels, playlists ou players (uma por linha)").pack(anchor="w")
        self.url_text = self._make_text(frame, height=5)
        self.url_text.pack(fill="x", pady=(4, 12))

        ttk.Label(frame, text="Pasta de destino").pack(anchor="w")
        folder_row = ttk.Frame(frame)
        folder_row.pack(fill="x", pady=(4, 12))
        ttk.Entry(folder_row, textvariable=self.folder_var).pack(side="left", fill="x", expand=True)
        ttk.Button(folder_row, text="Escolher", command=self.choose_folder).pack(side="left", padx=(8, 0))

        options = ttk.Frame(frame)
        options.pack(fill="x", pady=(0, 12))
        ttk.Checkbutton(options, text="Baixar playlist/álbum", variable=self.playlist_var).pack(side="left")

        format_frame = ttk.Frame(frame)
        format_frame.pack(fill="x", pady=(0, 12))
        ttk.Label(format_frame, text="Formato:").pack(side="left", padx=(0, 8))
        formats = DOWNLOAD_FORMATS
        ttk.Combobox(
            format_frame,
            textvariable=self.resolution_var,
            values=formats,
            state="readonly",
            width=28,
        ).pack(side="left")

        cookies_frame = ttk.LabelFrame(frame, text="Acesso a sites com login")
        cookies_frame.pack(fill="x", pady=(0, 12))

        browser_row = ttk.Frame(cookies_frame)
        browser_row.pack(fill="x", padx=10, pady=(8, 6))
        ttk.Label(browser_row, text="Usar cookies do navegador:").pack(side="left", padx=(0, 8))
        browsers = BROWSERS
        ttk.Combobox(
            browser_row,
            textvariable=self.cookies_browser_var,
            values=browsers,
            state="readonly",
            width=14,
        ).pack(side="left")
        ttk.Label(
            browser_row,
            text="Feche o navegador antes de usar cookies do Chrome/Edge.",
            foreground="#666666",
        ).pack(side="left", padx=(12, 0))

        file_row = ttk.Frame(cookies_frame)
        file_row.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Label(file_row, text="Ou arquivo cookies.txt:").pack(side="left", padx=(0, 8))
        ttk.Entry(file_row, textvariable=self.cookies_file_var).pack(side="left", fill="x", expand=True)
        ttk.Button(file_row, text="Selecionar", command=self.choose_cookies_file).pack(side="left", padx=(8, 0))
        ttk.Button(file_row, text="Limpar", command=lambda: self.cookies_file_var.set("")).pack(side="left", padx=(8, 0))

        actions = ttk.Frame(frame)
        actions.pack(fill="x", pady=(0, 8))
        self.download_button = ttk.Button(actions, text="Baixar", command=self.start_download)
        self.download_button.pack(side="left")
        ttk.Button(actions, text="Limpar log", command=self.clear_log).pack(side="left", padx=(8, 0))
        self.update_button = ttk.Button(actions, text="Verificar atualizações", command=lambda: self.start_maintenance(True))
        self.update_button.pack(side="right")

        ttk.Label(frame, textvariable=self.update_status_var, foreground="#555555").pack(anchor="w", pady=(0, 8))

        self.progress = ttk.Progressbar(frame, mode="indeterminate")
        self.progress.pack(fill="x", pady=(0, 12))

        ttk.Label(frame, text="Log").pack(anchor="w")
        log_frame = ttk.Frame(frame)
        log_frame.pack(fill="both", expand=True)
        self.log = self._make_text(log_frame, height=12)
        self.log.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(log_frame, command=self.log.yview)
        scrollbar.pack(side="right", fill="y")
        self.log.configure(yscrollcommand=scrollbar.set, state="disabled")

    def _build_site_logo(self, parent) -> Canvas:
        """Renderiza no desktop a mesma identidade animada usada no site da C²."""
        background = ttk.Style().lookup("TFrame", "background") or self.root.cget("background")
        canvas = Canvas(
            parent,
            width=238,
            height=76,
            background=background,
            highlightthickness=0,
            borderwidth=0,
            takefocus=False,
        )
        logo_path = resource_path("assets/c2_logo_horizontal.png")
        if not logo_path.exists():
            raise FileNotFoundError(logo_path)

        # A imagem original do site é animada. O PNG antigo foi capturado no
        # primeiro quadro (C_), por isso recortamos apenas o símbolo oficial e
        # recriamos a digitação abaixo em Tkinter.
        self.logo_source_image = PhotoImage(file=str(logo_path))
        self.logo_image = PhotoImage(width=76, height=76)
        self.logo_image.tk.call(
            self.logo_image,
            "copy",
            self.logo_source_image,
            "-from",
            58,
            48,
            305,
            327,
            "-to",
            7,
            3,
            "-subsample",
            4,
            4,
        )
        canvas.create_image(0, 38, image=self.logo_image, anchor="w")
        self.logo_exponent_item = canvas.create_text(
            57,
            7,
            text="2",
            anchor="nw",
            font=("Segoe UI", 13, "bold"),
            fill="#00aeda",
            state="hidden",
        )
        self.logo_text_item = canvas.create_text(
            82,
            28,
            text="_",
            anchor="w",
            font=("Consolas", 23, "bold"),
            fill="#0b1730",
        )
        self.logo_tagline_item = canvas.create_text(
            84,
            57,
            text="SOLUÇÕES EM TECNOLOGIA",
            anchor="w",
            font=("Segoe UI", 7, "bold"),
            fill="#0056b3",
            state="hidden",
        )
        self.logo_canvas = canvas
        self.root.after(350, self._logo_type_step)
        return canvas

    def _render_site_logo(self) -> None:
        if not self.logo_canvas or self.logo_text_item is None:
            return
        typed = self.logo_target[: self.logo_index]
        cursor = "_" if self.logo_cursor_visible else " "
        self.logo_canvas.itemconfigure(self.logo_text_item, text=f"{typed}{cursor}")
        if self.logo_exponent_item is not None:
            self.logo_canvas.itemconfigure(
                self.logo_exponent_item,
                state="normal" if self.logo_index else "hidden",
            )
        if self.logo_tagline_item is not None:
            self.logo_canvas.itemconfigure(
                self.logo_tagline_item,
                state="normal" if self.logo_index >= 2 else "hidden",
            )

    def _logo_type_step(self) -> None:
        if not self.root.winfo_exists():
            return
        self.logo_cursor_visible = True
        if self.logo_index < len(self.logo_target):
            self.logo_index += 1
            self._render_site_logo()
            self.root.after(105, self._logo_type_step)
            return
        self.logo_blink_count = 0
        self.root.after(500, self._logo_pause_blink)

    def _logo_pause_blink(self) -> None:
        if not self.root.winfo_exists():
            return
        self.logo_cursor_visible = not self.logo_cursor_visible
        self.logo_blink_count += 1
        self._render_site_logo()
        if self.logo_blink_count < 6:
            self.root.after(430, self._logo_pause_blink)
            return
        self.logo_index = 0
        self.logo_cursor_visible = True
        self._render_site_logo()
        self.root.after(500, self._logo_type_step)

    @staticmethod
    def _make_text(parent, height: int):
        from tkinter import Text

        return Text(parent, height=height, wrap="word")

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
        }
        try:
            save_user_settings(settings)
            self.user_settings = settings
        except OSError as exc:
            self.queue_log(f"Aviso: não foi possível salvar as preferências ({exc}).")

    def _on_close(self) -> None:
        self._save_preferences()
        self.root.destroy()

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
                elif event == "application_installer_ready":
                    self._install_application_update(Path(str(payload)))
                elif event == "download_finished":
                    self._finish_download()
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
        self.progress.start(10)
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
        answer = messagebox.askyesno(
            APP_NAME,
            f"A versão {payload.version} está disponível.\n\n"
            "Deseja baixar e instalar a atualização agora?",
        )
        if answer:
            self._download_application_update(payload)

    def _download_application_update(self, update: AppUpdate) -> None:
        self.update_button.configure(state="disabled")
        self.progress.start(10)
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
        try:
            self.update_status_var.set("Abrindo instalador da atualização...")
            self.app_updater.launch_installer(installer)
            self.root.after(800, self.root.destroy)
        except Exception as exc:
            self.progress.stop()
            self.update_button.configure(state="normal")
            messagebox.showerror(APP_NAME, f"Não foi possível iniciar a atualização:\n{exc}")

    def start_download(self) -> None:
        if self.busy:
            return

        urls = self._get_urls()
        folder = Path(self.folder_var.get().strip() or str(Path.home() / "Downloads"))
        if not urls:
            messagebox.showwarning(APP_NAME, "Informe pelo menos uma URL de mídia.")
            return

        cookies_file = self.cookies_file_var.get().strip()
        if cookies_file and not Path(cookies_file).exists():
            messagebox.showwarning(APP_NAME, "O arquivo cookies.txt selecionado não existe.")
            return

        folder.mkdir(parents=True, exist_ok=True)
        self.folder_var.set(str(folder))
        self._save_preferences()
        self.busy = True
        self.download_button.configure(state="disabled")
        self.progress.start(10)
        format_choice = self.resolution_var.get()
        self.queue_log(f"Iniciando {len(urls)} download(s) em: {folder} ({format_choice})")
        threading.Thread(target=self._download, args=(urls, folder, format_choice), daemon=True).start()

    def _get_urls(self) -> list[str]:
        raw = self.url_text.get("1.0", END)
        return [line.strip() for line in raw.splitlines() if line.strip()]

    def _download(self, urls: list[str], folder: Path, format_choice: str) -> None:
        failures = 0
        try:
            status = self.dependencies.ensure(self.queue_log, force=False)
            self.dependency_status = status
            for index, url in enumerate(urls, start=1):
                self.queue_log(f"[{index}/{len(urls)}] Processando: {url}")
                command = self._build_command(status.yt_dlp_path, folder, format_choice, url)
                return_code, output_files = self._run_downloader(command)
                if return_code != 0:
                    failures += 1
                    continue

                if format_choice != "Apenas áudio (M4A)":
                    conversion_failed = False
                    for output_file in output_files:
                        try:
                            self._ensure_player_compatibility(output_file)
                        except Exception as exc:
                            conversion_failed = True
                            self.queue_log(
                                f"Erro ao tornar o vídeo compatível ({output_file.name}): {exc}"
                            )
                    if conversion_failed:
                        failures += 1
            if failures:
                self.queue_log(f"Concluído com falha em {failures} de {len(urls)} item(ns).")
            else:
                self.queue_log("Concluído com sucesso.")
        except Exception as exc:
            self.queue_log(f"Erro: {exc}")
        finally:
            self.event_queue.put(("download_finished", None))

    def _build_command(self, engine: Path, folder: Path, format_choice: str, url: str) -> list[str]:
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
        output_template = "%(playlist_index|)s%(playlist_index& - )s%(title).180s [%(id)s].%(ext)s"

        command = [
            str(engine),
            "--newline",
            "--no-color",
            "--encoding",
            "utf-8",
            "--windows-filenames",
            "--continue",
            "--retries",
            "10",
            "--fragment-retries",
            "10",
            "--merge-output-format",
            "mp4",
            "--remote-components",
            "ejs:github",
            "--print",
            f"after_move:{OUTPUT_MARKER}%(filepath)s",
            "-P",
            str(folder),
            "-o",
            output_template,
            "-f",
            selected_format,
        ]

        if not self.playlist_var.get():
            command.append("--no-playlist")
        if FFMPEG_PATH:
            command.extend(["--ffmpeg-location", FFMPEG_PATH])
        if format_choice == "Apenas áudio (M4A)":
            command.extend(["--extract-audio", "--audio-format", "m4a", "--audio-quality", "0"])

        browser = self.cookies_browser_var.get().strip().lower()
        if browser and browser != "nenhum":
            command.extend(["--cookies-from-browser", browser])
        cookies_file = self.cookies_file_var.get().strip()
        if cookies_file:
            command.extend(["--cookies", cookies_file])

        command.extend(["--", url])
        return command

    def _run_downloader(self, command: list[str]) -> tuple[int, list[Path]]:
        output_files: list[Path] = []
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=CREATE_NO_WINDOW,
            env=self.dependencies.runtime_environment(),
        )
        assert process.stdout is not None
        for line in process.stdout:
            cleaned = line.rstrip()
            if not cleaned:
                continue
            if cleaned.startswith(OUTPUT_MARKER):
                output_path = cleaned[len(OUTPUT_MARKER):].strip()
                if output_path:
                    output_files.append(Path(output_path))
                continue
            self.queue_log(cleaned)
        return process.wait(), output_files

    @staticmethod
    def _stream_details(media_path: Path) -> tuple[str, str]:
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
        audio_line = next((line.strip().lower() for line in lines if "audio:" in line.lower()), "")
        return video_line, audio_line

    def _ensure_player_compatibility(self, media_path: Path) -> Path:
        if not media_path.exists() or media_path.suffix.lower() not in VIDEO_EXTENSIONS:
            return media_path
        if not FFMPEG_PATH:
            raise RuntimeError("FFmpeg não foi encontrado para validar o vídeo.")

        video_line, audio_line = self._stream_details(media_path)
        if not video_line:
            return media_path

        video_ok = "video: h264" in video_line and "yuv420p" in video_line
        audio_ok = not audio_line or (
            "audio: aac" in audio_line
            and "he-aac" not in audio_line
            and "he_aac" not in audio_line
        )
        if video_ok and audio_ok and media_path.suffix.lower() == ".mp4":
            return media_path

        destination = media_path.with_suffix(".mp4")
        temporary = destination.with_name(f".{destination.stem}.c2-convertendo.mp4")
        temporary.unlink(missing_ok=True)
        self.queue_log(
            f"Convertendo para MP4 compatível (H.264/AAC): {media_path.name}"
        )

        command = [
            FFMPEG_PATH,
            "-y",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-i",
            str(media_path),
            "-map",
            "0:v:0",
            "-map",
            "0:a?",
            "-map_metadata",
            "0",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-profile:a",
            "aac_low",
            "-b:a",
            "160k",
            "-movflags",
            "+faststart",
            str(temporary),
        ]
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=CREATE_NO_WINDOW,
            timeout=7200,
        )
        if completed.returncode != 0 or not temporary.exists() or temporary.stat().st_size == 0:
            temporary.unlink(missing_ok=True)
            details = completed.stdout.strip().splitlines()
            last_line = details[-1] if details else f"código {completed.returncode}"
            raise RuntimeError(last_line)

        os.replace(temporary, destination)
        if media_path != destination:
            media_path.unlink(missing_ok=True)
        self.queue_log(f"Vídeo compatível gerado: {destination.name}")
        return destination

    def _finish_download(self) -> None:
        self.busy = False
        if not self.maintenance_busy:
            self.progress.stop()
        self.download_button.configure(state="normal")


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
