from __future__ import annotations

from pathlib import Path
from tkinter import PhotoImage, messagebox
from tkinter import ttk

import youtube_downloader_app as app
from app_config import APP_NAME, APP_VERSION
from jw_org_downloader import (
    convert_to_m4a,
    download_item,
    is_jw_category_url,
    resolve_category_items,
)


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


def _download_with_jw_categories(
    self,
    urls: list[str],
    folder: Path,
    format_choice: str,
) -> None:
    """Processa categorias do JW.ORG e mantém o fluxo padrão para outras URLs."""
    failures = 0
    attempted = 0
    try:
        status = self.dependencies.ensure(self.queue_log, force=False)
        self.dependency_status = status

        for source_index, url in enumerate(urls, start=1):
            self.queue_log(f"[{source_index}/{len(urls)}] Processando: {url}")

            if is_jw_category_url(url):
                try:
                    items = resolve_category_items(
                        url,
                        format_choice,
                        include_subcategories=bool(self.playlist_var.get()),
                        logger=self.queue_log,
                    )
                    self.queue_log(
                        f"JW.ORG: {len(items)} mídia(s) encontrada(s) na categoria."
                    )
                except Exception as exc:
                    failures += 1
                    attempted += 1
                    self.queue_log(f"Erro ao consultar a categoria do JW.ORG: {exc}")
                    continue

                for item_index, item in enumerate(items, start=1):
                    attempted += 1
                    try:
                        output_file = download_item(
                            item,
                            folder,
                            item_index,
                            len(items),
                            logger=self.queue_log,
                        )
                        if format_choice == "Apenas áudio (M4A)":
                            convert_to_m4a(
                                output_file,
                                app.FFMPEG_PATH,
                                logger=self.queue_log,
                            )
                        else:
                            self._ensure_player_compatibility(output_file)
                    except Exception as exc:
                        failures += 1
                        self.queue_log(f"Erro no item '{item.title}': {exc}")
                continue

            attempted += 1
            command = self._build_command(
                status.yt_dlp_path,
                folder,
                format_choice,
                url,
            )
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
            self.queue_log(
                f"Concluído com falha em {failures} de {attempted} item(ns)."
            )
        else:
            self.queue_log("Concluído com sucesso.")
    except Exception as exc:
        self.queue_log(f"Erro: {exc}")
    finally:
        self.event_queue.put(("download_finished", None))


app.SUPPORTED_HINT = (
    "YouTube, Instagram, Facebook, TikTok, Vimeo, X/Twitter, Twitch, "
    "Dailymotion, categorias de vídeos do JW.ORG e outros players suportados."
)
app.DownloadApp._build_site_logo = _build_fixed_site_logo
app.DownloadApp._maintenance_worker = _maintenance_worker_with_feedback
app.DownloadApp._maintenance_done = _maintenance_done_with_feedback
app.DownloadApp._download = _download_with_jw_categories


if __name__ == "__main__":
    app.main()
