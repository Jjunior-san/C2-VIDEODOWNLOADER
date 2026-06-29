from __future__ import annotations

from tkinter import PhotoImage, messagebox
from tkinter import ttk

import youtube_downloader_app as app
from app_config import APP_NAME, APP_VERSION


_original_maintenance_done = app.DownloadApp._maintenance_done


def _build_fixed_site_logo(self, parent) -> ttk.Frame:
    """Exibe a identidade C² sem coordenadas compartilhadas ou sobreposição."""
    brand = ttk.Frame(parent)

    symbol_holder = ttk.Frame(brand, width=72, height=72)
    symbol_holder.pack(side="left", anchor="n")
    symbol_holder.pack_propagate(False)

    logo_path = app.resource_path("assets/c2_logo_horizontal.png")
    if not logo_path.exists():
        raise FileNotFoundError(logo_path)

    # O arquivo usado no site contém o primeiro quadro da animação. Somente o
    # símbolo é recortado; nome e slogan ficam em widgets independentes. Isso
    # impede sobreposição em diferentes escalas de DPI do Windows.
    self.logo_source_image = PhotoImage(file=str(logo_path))
    self.logo_image = PhotoImage(width=68, height=68)
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
        0,
        0,
        "-subsample",
        4,
        4,
    )
    ttk.Label(symbol_holder, image=self.logo_image).pack(anchor="center", pady=(2, 0))

    wordmark = ttk.Frame(brand)
    wordmark.pack(side="left", padx=(10, 0), anchor="center")
    ttk.Label(
        wordmark,
        text="C² SISTEMAS",
        font=("Segoe UI", 17, "bold"),
        foreground="#0b1730",
    ).pack(anchor="w")
    ttk.Label(
        wordmark,
        text="SOLUÇÕES EM TECNOLOGIA",
        font=("Segoe UI", 7, "bold"),
        foreground="#0056b3",
    ).pack(anchor="w", pady=(2, 0))

    return brand


def _maintenance_worker_with_feedback(self, force: bool) -> None:
    """Verifica dependências e deixa explícito quando o app já está atualizado."""
    try:
        status = self.dependencies.ensure(self.queue_log, force=force)
        update = None
        check_succeeded = False
        try:
            update = self.app_updater.check(self.queue_log)
            check_succeeded = True
        except Exception as exc:
            self.queue_log(
                f"Aviso: não foi possível verificar a versão do aplicativo ({exc})."
            )

        self._c2_no_update = check_succeeded and update is None
        self._c2_force_check = force
        self.event_queue.put(("maintenance_done", status))
        if update:
            self.event_queue.put(("update_available", update))
    except Exception as exc:
        self.event_queue.put(("maintenance_error", exc))


def _maintenance_done_with_feedback(self, payload: object) -> None:
    _original_maintenance_done(self, payload)
    if not getattr(self, "_c2_no_update", False):
        return

    message = f"Você já está usando a versão mais recente ({APP_VERSION})."
    self.update_status_var.set(message)
    self.queue_log(f"Aplicativo: {message}")
    self._c2_no_update = False

    if getattr(self, "_c2_force_check", False):
        messagebox.showinfo(APP_NAME, message)
    self._c2_force_check = False


app.DownloadApp._build_site_logo = _build_fixed_site_logo
app.DownloadApp._maintenance_worker = _maintenance_worker_with_feedback
app.DownloadApp._maintenance_done = _maintenance_done_with_feedback


if __name__ == "__main__":
    app.main()
