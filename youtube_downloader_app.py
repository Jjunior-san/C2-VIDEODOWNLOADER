from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
from pathlib import Path
from tkinter import BooleanVar, END, StringVar, Tk, filedialog, messagebox
from tkinter import ttk

from app_config import APP_MUTEX, APP_NAME, APP_VERSION
from c2_update import (
    ApplicationUpdater,
    AppUpdate,
    CREATE_NO_WINDOW,
    DependencyManager,
    DependencyStatus,
)

try:
    import imageio_ffmpeg

    FFMPEG_PATH = imageio_ffmpeg.get_ffmpeg_exe()
except (ImportError, RuntimeError):
    FFMPEG_PATH = None

SUPPORTED_HINT = (
    "YouTube, Instagram, Facebook, TikTok, Vimeo, X/Twitter, Twitch, "
    "Dailymotion e outros players suportados pelo yt-dlp."
)


def resource_path(relative_path: str) -> Path:
    base_path = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base_path / relative_path


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

        self.folder_var = StringVar(value=str(Path.home() / "Downloads"))
        self.playlist_var = BooleanVar(value=True)
        self.resolution_var = StringVar(value="Melhor qualidade")
        self.cookies_browser_var = StringVar(value="Nenhum")
        self.cookies_file_var = StringVar()
        self.update_status_var = StringVar(value="Componentes ainda não verificados")

        self.busy = False
        self.maintenance_busy = False
        self.log_queue: queue.Queue[str] = queue.Queue()
        self.event_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.logo_image = None
        self.available_update: AppUpdate | None = None
        self.dependency_status: DependencyStatus | None = None

        self.dependencies = DependencyManager()
        self.app_updater = ApplicationUpdater(self.dependencies)

        self._build_ui()
        self._poll_queues()
        self.root.after(700, self.start_maintenance)

    def _build_ui(self) -> None:
        frame = ttk.Frame(self.root, padding=18)
        frame.pack(fill="both", expand=True)

        header = ttk.Frame(frame)
        header.pack(fill="x", pady=(0, 12))
        logo_path = resource_path("assets/c2_logo_horizontal.png")
        if logo_path.exists():
            try:
                self.logo_image = self._load_logo(logo_path)
                ttk.Label(header, image=self.logo_image).pack(side="left", padx=(0, 14))
            except Exception:
                pass
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
        formats = [
            "Melhor qualidade",
            "Melhor MP4 compatível",
            "1080p",
            "720p",
            "480p",
            "360p",
            "Apenas áudio (M4A)",
        ]
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
        browsers = ["Nenhum", "Chrome", "Edge", "Firefox", "Brave", "Opera", "Vivaldi"]
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

    def _load_logo(self, logo_path: Path):
        from tkinter import PhotoImage

        logo = PhotoImage(file=str(logo_path))
        width = logo.width()
        if width > 240:
            logo = logo.subsample(max(1, round(width / 240)))
        return logo

    @staticmethod
    def _make_text(parent, height: int):
        from tkinter import Text

        return Text(parent, height=height, wrap="word")

    def choose_folder(self) -> None:
        selected = filedialog.askdirectory(initialdir=self.folder_var.get() or str(Path.home()))
        if selected:
            self.folder_var.set(selected)

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
                return_code = self._run_downloader(command)
                if return_code != 0:
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
        format_map = {
            "Melhor qualidade": "bv*+ba/best",
            "Melhor MP4 compatível": "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/best",
            "1080p": "bv*[height<=1080]+ba/b[height<=1080]/best",
            "720p": "bv*[height<=720]+ba/b[height<=720]/best",
            "480p": "bv*[height<=480]+ba/b[height<=480]/best",
            "360p": "bv*[height<=360]+ba/b[height<=360]/best",
            "Apenas áudio (M4A)": "ba/bestaudio/best",
        }
        selected_format = format_map.get(format_choice, "bv*+ba/best")
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

    def _run_downloader(self, command: list[str]) -> int:
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
            if cleaned:
                self.queue_log(cleaned)
        return process.wait()

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
