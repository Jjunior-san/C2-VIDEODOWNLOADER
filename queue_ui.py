from __future__ import annotations

import threading
from pathlib import Path
from tkinter import END, messagebox, ttk

from audio_library import AUDIO_AUTO_BITRATE, bitrate_from_options, is_audio_format
from download_control import DownloadCancelled, DownloadControl
from download_queue import ACTIVE, LABELS, RUNNABLE, queue_summary
from queue_service import discover, run_queue
from ui_layout import add_tooltip


def queue_options_compatible(current: dict, saved: dict) -> bool:
    """Compare only settings that can change the active queue's output."""
    mode = "music" if saved.get("work_mode") == "music" else "video"
    keys = {
        "folder", "format", "playlist", "fragments",
        "cookies_browser", "cookies_file", "work_mode",
    }
    if mode == "music":
        keys.update({
            "music_folder", "music_format", "audio_bitrate_mode",
            "audio_custom_bitrate", "music_structure", "music_filename_template",
            "deezer_arl", "deezer_quality", "create_collection_zip",
        })
    else:
        keys.update({"video_folder", "video_format"})
        if is_audio_format(str(saved.get("format") or "")):
            keys.update({"audio_bitrate_mode", "audio_custom_bitrate"})
    return all(current.get(key) == saved.get(key) for key in keys)


class QueueUI:
    def _build_episode_list(self, parent):
        controls = ttk.Frame(parent)
        controls.pack(fill="x", pady=(0, 6))
        self.analyze_button = ttk.Button(controls, text="Listar mídias", command=self.analyze_links)
        self.analyze_button.pack(side="left")
        ttk.Button(controls, text="Marcar todos", command=lambda: self._select_items(True)).pack(side="left", padx=4)
        ttk.Button(controls, text="Desmarcar", command=lambda: self._select_items(False)).pack(side="left")
        self.queue_count = ttk.Label(parent, text="Liste as mídias para selecionar o que deseja baixar.")
        self.queue_count.pack(anchor="w", pady=(0, 4))
        table = ttk.Frame(parent)
        table.pack(fill="both", expand=True, pady=(0, 6))
        columns = ("selected", "title", "quality", "status", "percent")
        self.episode_tree = ttk.Treeview(table, columns=columns, show="headings", height=11, selectmode="extended")
        for name, label, width in zip(columns, ("✓", "Mídia", "Qualidade", "Situação", "%"), (32, 290, 100, 112, 48)):
            self.episode_tree.heading(name, text=label)
            self.episode_tree.column(name, width=width, minwidth=width if name != "title" else 130,
                                     stretch=name == "title", anchor="w" if name == "title" else "center")
        self.episode_tree.grid(row=0, column=0, sticky="nsew")
        table.columnconfigure(0, weight=1)
        ybar = ttk.Scrollbar(table, orient="vertical", command=self.episode_tree.yview)
        ybar.grid(row=0, column=1, sticky="ns")
        xbar = ttk.Scrollbar(table, orient="horizontal", command=self.episode_tree.xview)
        xbar.grid(row=1, column=0, sticky="ew")
        self.episode_tree.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        self.episode_tree.bind("<Button-1>", self._click_episode)
        self.episode_tree.bind("<space>", self._toggle_selected)
        self.episode_tree.bind("<<TreeviewSelect>>", self._show_episode_details)
        self.episode_details = ttk.Label(parent, text="", width=1, wraplength=600, foreground="#596579")
        self.episode_details.bind("<Configure>", lambda event: self.episode_details.configure(wraplength=max(1, event.width)))
        management = ttk.Frame(parent)
        management.pack(fill="x", pady=(0, 6))
        ttk.Button(management, text="Cancelar selecionados", command=self.cancel_selected).pack(side="right")
        ttk.Button(management, text="Repetir falhas", command=self.retry_failed).pack(side="right", padx=6)
        self.remove_queue_button = ttk.Button(management, text="Remover da fila", command=self.remove_queue_selected)
        self.remove_queue_button.pack(side="right")
        self.clear_queue_button = ttk.Button(management, text="Limpar fila", command=self.clear_queue)
        self.clear_queue_button.pack(side="right", padx=6)
        self.episode_actions = ttk.Frame(parent)
        self.episode_actions.pack(fill="x", pady=(0, 8))

    def _build_completed_list(self, parent):
        ttk.Label(parent, text="Downloads concluídos", font=(self.text_family, 11, "bold")).pack(anchor="w")
        self.completed_count = ttk.Label(
            parent, text="Nenhum download concluído.", foreground="#596579",
        )
        self.completed_count.pack(anchor="w", pady=(2, 8))
        table = ttk.Frame(parent)
        table.pack(fill="both", expand=True, pady=(0, 8))
        columns = ("title", "quality", "file")
        self.completed_tree = ttk.Treeview(
            table, columns=columns, show="headings", height=12, selectmode="extended",
        )
        for name, label, width in zip(
            columns,
            ("Mídia", "Qualidade", "Arquivo salvo"),
            (320, 130, 260),
        ):
            self.completed_tree.heading(name, text=label)
            self.completed_tree.column(
                name, width=width, minwidth=120, stretch=name in {"title", "file"}, anchor="w",
            )
        self.completed_tree.grid(row=0, column=0, sticky="nsew")
        table.rowconfigure(0, weight=1)
        table.columnconfigure(0, weight=1)
        ybar = ttk.Scrollbar(table, orient="vertical", command=self.completed_tree.yview)
        ybar.grid(row=0, column=1, sticky="ns")
        xbar = ttk.Scrollbar(table, orient="horizontal", command=self.completed_tree.xview)
        xbar.grid(row=1, column=0, sticky="ew")
        self.completed_tree.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        self.completed_tree.bind("<<TreeviewSelect>>", self._show_completed_details)
        actions = ttk.Frame(parent)
        actions.pack(fill="x", pady=(0, 8))
        self.clear_completed_button = ttk.Button(
            actions, text="Limpar concluídos", command=self.clear_completed,
        )
        self.clear_completed_button.pack(side="right")
        self.remove_completed_button = ttk.Button(
            actions, text="Remover selecionados", command=self.remove_completed_selected,
        )
        self.remove_completed_button.pack(side="right", padx=(0, 6))
        self.play_completed_button = ttk.Button(
            actions, text="▶", width=3, command=lambda: self.play_selected_music(completed=True),
        )
        self.play_completed_button.pack(side="left")
        self.stop_completed_button = ttk.Button(
            actions, text="■", width=3, command=self.stop_music,
        )
        self.stop_completed_button.pack(side="left", padx=(6, 0))
        add_tooltip(self.play_completed_button, "Reproduzir ou pausar")
        add_tooltip(self.stop_completed_button, "Parar a reprodução")
        self.edit_completed_button = ttk.Button(
            actions, text="Editar metadados", command=lambda: self.edit_selected_music_metadata(completed=True),
        )
        self.edit_completed_button.pack(side="left", padx=(6, 0))
        self.cover_completed_button = ttk.Button(
            actions, text="Alterar capa", command=lambda: self.change_selected_music_cover(completed=True),
        )
        self.cover_completed_button.pack(side="left", padx=(6, 0))
        self.open_folder_button = ttk.Button(
            actions, text="Abrir pasta", command=self.open_completed_folder,
        )
        self.open_folder_button.pack(side="left", padx=(6, 0))
        self.completed_details = ttk.Label(
            parent, text="", width=1, wraplength=600, foreground="#596579", justify="left",
        )
        self.completed_details.pack(fill="x")
        self.completed_details.bind(
            "<Configure>", lambda event: self.completed_details.configure(wraplength=max(1, event.width)),
        )

    def _restore_queue(self):
        job = self.queue_repository.recover()
        if job["items"]:
            options = dict(job["options"])
            upgraded = False
            for key, value in (
                ("audio_bitrate_mode", AUDIO_AUTO_BITRATE),
                ("audio_custom_bitrate", "192"),
                ("music_structure", "Artista\\Álbum"),
                ("music_filename_template", "{faixa:02} - {titulo}"),
            ):
                if key not in options:
                    options[key] = value
                    upgraded = True
            if "work_mode" not in options:
                sources = [str(source).lower() for source in job.get("sources", []) if str(source).strip()]
                options["work_mode"] = (
                    "music"
                    if sources and all(source.startswith("deezer:") or "deezer.com/" in source for source in sources)
                    else "video"
                )
                upgraded = True
            queue_mode = "music" if options.get("work_mode") == "music" else "video"
            if "music_folder" not in options:
                options["music_folder"] = (
                    options.get("folder") if queue_mode == "music"
                    else self.music_folder_var.get().strip()
                )
                upgraded = True
            if "video_folder" not in options:
                options["video_folder"] = (
                    options.get("folder") if queue_mode == "video"
                    else self.video_folder_var.get().strip()
                )
                upgraded = True
            if "music_format" not in options:
                options["music_format"] = (
                    options.get("format") if queue_mode == "music"
                    else self.music_format_var.get()
                )
                upgraded = True
            if "video_format" not in options:
                options["video_format"] = (
                    options.get("format") if queue_mode == "video"
                    else self.video_format_var.get()
                )
                upgraded = True
            if upgraded:
                self.queue_repository.replace(job["items"], options, job.get("sources", []))
            needs_resume = any(item["kind"] != "unresolved" and item["status"] in (RUNNABLE | {"failed"}) for item in job["items"])
            if needs_resume:
                if queue_mode == "music":
                    self.music_folder_var.set(options["music_folder"])
                    self.music_format_var.set(options["music_format"])
                else:
                    self.video_folder_var.set(options["video_folder"])
                    self.video_format_var.set(options["video_format"])
                self._apply_work_mode(queue_mode, initial=True)
                self.audio_bitrate_mode_var.set(options["audio_bitrate_mode"])
                self.audio_custom_bitrate_var.set(str(options["audio_custom_bitrate"]))
                self.playlist_var.set(options["playlist"])
                self.fragments_var.set(str(options["fragments"]))
                self.cookies_browser_var.set(options.get("cookies_browser", "Nenhum"))
                self.cookies_file_var.set(options.get("cookies_file", ""))
                if hasattr(self, "music_structure_var"):
                    self.music_structure_var.set(options.get("music_structure", "Artista\\Álbum"))
                if hasattr(self, "music_filename_var"):
                    self.music_filename_var.set(options.get("music_filename_template", "{faixa:02} - {titulo}"))
                if hasattr(self, "deezer_arl_var") and "deezer_arl" in options:
                    self.deezer_arl_var.set(options.get("deezer_arl", ""))
                if hasattr(self, "deezer_quality_var") and "deezer_quality" in options:
                    self.deezer_quality_var.set(options.get("deezer_quality", "Automática (melhor da conta)"))
                if hasattr(self, "create_zip_var") and "create_collection_zip" in options:
                    self.create_zip_var.set(bool(options.get("create_collection_zip", False)))
            if hasattr(self, "_set_source_text"):
                self._set_source_text(job.get("sources", []))
            else:
                self.url_text.insert("1.0", "\n".join(job.get("sources", [])))
            if needs_resume:
                self.queue_log("Fila recuperada. Selecione os itens e clique em Continuar fila; nenhum download inicia automaticamente.")
        self._refresh_queue()

    def _refresh_queue(self):
        if self.queue_repository is None:
            return
        self.queue_items = self.queue_repository.snapshot()["items"]
        active_items = [item for item in self.queue_items if item["status"] != "completed"]
        completed_items = [item for item in self.queue_items if item["status"] == "completed"]
        existing = set(self.episode_tree.get_children())
        for item in active_items:
            values = ("✓" if item["enabled"] else "", item["title"], item.get("quality", "A definir"),
                      LABELS[item["status"]], "—")
            if item["id"] in existing:
                self.episode_tree.item(item["id"], values=values)
                existing.remove(item["id"])
            else:
                self.episode_tree.insert("", END, iid=item["id"], values=values)
        for item_id in existing:
            self.episode_tree.delete(item_id)
        existing_completed = set(self.completed_tree.get_children())
        for item in completed_items:
            files = item.get("files", [])
            saved_file = Path(files[0]).name if files else "Arquivo não informado"
            values = (item["title"], item.get("quality", "A definir"), saved_file)
            if item["id"] in existing_completed:
                self.completed_tree.item(item["id"], values=values)
                existing_completed.remove(item["id"])
            else:
                self.completed_tree.insert("", END, iid=item["id"], values=values)
        for item_id in existing_completed:
            self.completed_tree.delete(item_id)
        summary = queue_summary(self.queue_items)
        active_selected = sum(item["enabled"] for item in active_items)
        self.queue_count.configure(
            text=f"{len(active_items)} na fila • {active_selected} marcado(s) • {len(completed_items)} concluído(s)",
        )
        self.completed_count.configure(
            text=f"{len(completed_items)} download(s) concluído(s). Os arquivos permanecem na pasta de destino.",
        )
        idle_state = "normal" if not self.busy else "disabled"
        self.clear_queue_button.configure(state=idle_state if self.queue_items else "disabled")
        self.remove_queue_button.configure(state=idle_state if active_items else "disabled")
        completed_state = idle_state if completed_items else "disabled"
        self.clear_completed_button.configure(state=completed_state)
        self.remove_completed_button.configure(state=completed_state)
        if hasattr(self, "play_completed_button"):
            selected_completed = self.completed_tree.selection()
            selected_item = next(
                (item for item in completed_items if selected_completed and item["id"] == selected_completed[0]),
                None,
            )
            music_selected = bool(selected_item and selected_item.get("kind") == "deezer_preview")
            self.play_completed_button.configure(state=idle_state if music_selected else "disabled")
            self.edit_completed_button.configure(state=idle_state if music_selected else "disabled")
            self.cover_completed_button.configure(state=idle_state if music_selected else "disabled")
            self.open_folder_button.configure(state=idle_state if selected_item else "disabled")
        if self.queue_running and not self.active_queue_id:
            self.progress.configure(mode="determinate")
            self.progress_value_var.set(summary["overall"])
        if not self.busy:
            self.download_button.configure(text="Continuar fila" if active_items else "Baixar")
            for button in getattr(self, "context_download_buttons", []):
                button.configure(text="Continuar" if active_items else "Baixar")
        self._refresh_context_queues(active_items)
        self._show_episode_details()
        self._show_completed_details()

    def _refresh_context_queues(self, active_items):
        trees = getattr(self, "context_queue_trees", {})
        queue_mode = "video"
        if self.queue_repository is not None:
            queue_mode = str(
                self.queue_repository.snapshot().get("options", {}).get("work_mode") or "video"
            )
        for mode, tree in trees.items():
            relevant = [
                item for item in active_items
                if (
                    item.get("kind") in {"deezer_preview", "deezer_full"}
                    or (item.get("kind") == "unresolved" and queue_mode == "music")
                ) == (mode == "music")
            ]
            existing = set(tree.get_children())
            for item in relevant:
                values = (item.get("title") or "Mídia", LABELS[item["status"]])
                if item["id"] in existing:
                    tree.item(item["id"], values=values)
                    existing.remove(item["id"])
                else:
                    tree.insert("", END, iid=item["id"], values=values)
            for item_id in existing:
                tree.delete(item_id)
            enabled = sum(bool(item.get("enabled")) for item in relevant)
            count = getattr(self, "context_queue_counts", {}).get(mode)
            if count is not None:
                count.configure(
                    text=(
                        f"{len(relevant)} item(ns) • {enabled} marcado(s)"
                        if relevant else "Nenhum item nesta fila."
                    )
                )

    def _show_episode_details(self, _event=None):
        selected = self.episode_tree.selection()
        item = next((item for item in self.queue_items if selected and item["id"] == selected[0]), None)
        error = item.get("error", "") if item else ""
        self.episode_details.configure(text=error)
        if error:
            self.episode_details.pack(fill="x", pady=(0, 4), before=self.episode_actions)
        else:
            self.episode_details.pack_forget()
        if hasattr(self, "_show_music_item"):
            self._show_music_item(item)

    def _show_completed_details(self, _event=None):
        selected = self.completed_tree.selection()
        item = next((item for item in self.queue_items if selected and item["id"] == selected[0]), None)
        files = item.get("files", []) if item else []
        details = "Arquivos mantidos no computador:\n" + "\n".join(files) if files else ""
        if item and item.get("kind") == "deezer_preview":
            details = (
                f"{item.get('track_title') or item.get('title') or 'Música'}\n"
                f"Artista: {item.get('artist') or 'Não informado'}\n"
                f"Álbum: {item.get('album') or 'Não informado'}\n\n"
                + details
            )
        self.completed_details.configure(text=details)
        if hasattr(self, "play_completed_button"):
            idle_state = "normal" if not self.busy else "disabled"
            music_selected = bool(item and item.get("kind") == "deezer_preview")
            self.play_completed_button.configure(state=idle_state if music_selected else "disabled")
            self.edit_completed_button.configure(state=idle_state if music_selected else "disabled")
            self.cover_completed_button.configure(state=idle_state if music_selected else "disabled")
            self.open_folder_button.configure(state=idle_state if item else "disabled")

    def remove_completed_selected(self):
        if self.busy or self.queue_repository is None:
            return
        completed_ids = {
            item["id"] for item in self.queue_items if item["status"] == "completed"
        }
        ids = completed_ids.intersection(self.completed_tree.selection())
        removed = self.queue_repository.remove_many(ids)
        if removed:
            self.queue_log(
                f"{removed} registro(s) concluído(s) removido(s) da lista; nenhum arquivo foi apagado.",
            )
        self._refresh_queue()

    def clear_completed(self):
        if self.busy or self.queue_repository is None:
            return
        ids = [item["id"] for item in self.queue_items if item["status"] == "completed"]
        if not ids or not messagebox.askyesno(
            "Limpar concluídos",
            "Remover todos os concluídos da lista?\n\nOs arquivos baixados não serão apagados.",
        ):
            return
        removed = self.queue_repository.remove_many(ids)
        self.queue_log(
            f"{removed} registro(s) concluído(s) removido(s) da lista; nenhum arquivo foi apagado.",
        )
        self._refresh_queue()

    def clear_queue(self):
        if self.busy or self.queue_repository is None or not self.queue_items:
            return
        if not messagebox.askyesno(
            "Limpar fila",
            "Remover todos os itens da fila e da lista de concluídos?\n\n"
            "Os arquivos baixados e parciais não serão apagados.",
        ):
            return
        removed = self.queue_repository.clear()
        self.queue_log(f"Fila limpa ({removed} registro(s)); nenhum arquivo foi apagado.")
        self._refresh_queue()

    def remove_queue_selected(self):
        if self.busy or self.queue_repository is None:
            return
        completed_ids = {
            item["id"] for item in self.queue_items if item["status"] == "completed"
        }
        ids = set(self.episode_tree.selection()) - completed_ids
        removed = self.queue_repository.remove_many(ids)
        if removed:
            self.queue_log(
                f"{removed} registro(s) removido(s) da fila; nenhum arquivo parcial ou baixado foi apagado.",
            )
        self._refresh_queue()

    def _click_episode(self, event):
        if self.episode_tree.identify_column(event.x) == "#1":
            item_id = self.episode_tree.identify_row(event.y)
            if item_id:
                self._toggle_items([item_id])
                return "break"

    def _toggle_selected(self, _event=None):
        self._toggle_items(self.episode_tree.selection())
        return "break"

    def _toggle_items(self, ids):
        if self.busy or self.queue_repository is None:
            return
        self.queue_repository.update_many({item["id"]: {"enabled": not item["enabled"]}
                                           for item in self.queue_items if item["id"] in ids and item["kind"] != "unresolved"})
        self._refresh_queue()

    def _select_items(self, enabled):
        if self.busy or self.queue_repository is None:
            return
        self.queue_repository.update_many({item["id"]: {"enabled": enabled}
                                           for item in self.queue_items if item["kind"] != "unresolved" and item["status"] != "skipped"})
        self._refresh_queue()

    def cancel_selected(self):
        if self.queue_repository is None:
            return
        if self.busy and not self.queue_running:
            return
        ids = self.episode_tree.selection()
        with self.queue_repository.lock:
            for item in self.queue_repository.snapshot()["items"]:
                if item["id"] not in ids:
                    continue
                if item["status"] in ACTIVE and item["id"] == self.active_queue_id:
                    self.download_control.skip()
                    self.pause_button.configure(text="Pausar")
                elif item["status"] in RUNNABLE:
                    self.queue_repository.update(item["id"], status="cancelled", error="Cancelado pelo usuário.")
        self._refresh_queue()

    def retry_failed(self):
        if self.busy or self.queue_repository is None:
            return
        ids = self.episode_tree.selection()
        allowed = {"failed", "cancelled", "skipped"} if ids else {"failed"}
        self.queue_repository.update_many({item["id"]: {"status": "pending", "enabled": True, "error": ""}
                                           for item in self.queue_items if (not ids or item["id"] in ids)
                                           and item["kind"] != "unresolved" and item["status"] in allowed})
        self._refresh_queue()

    def stop_queue(self):
        if self.busy:
            self.download_control.cancel()
            self.queue_log("Interrompendo. A fila e os arquivos parciais serão mantidos.")

    def _capture_options(self):
        work_mode = getattr(self, "work_mode", "video")
        music_folder_value = (
            self.music_folder_var.get().strip()
            if hasattr(self, "music_folder_var")
            else self.folder_var.get().strip()
        )
        video_folder_value = (
            self.video_folder_var.get().strip()
            if hasattr(self, "video_folder_var")
            else self.folder_var.get().strip()
        )
        default_music = Path.home() / "Music"
        if not default_music.exists():
            default_music = Path.home() / "Downloads" / "Músicas"
        music_folder = music_folder_value or str(default_music)
        video_folder = video_folder_value or str(Path.home() / "Downloads")
        music_format = (
            self.music_format_var.get()
            if hasattr(self, "music_format_var")
            else self.resolution_var.get()
        )
        video_format = (
            self.video_format_var.get()
            if hasattr(self, "video_format_var")
            else self.resolution_var.get()
        )
        active_folder = music_folder if work_mode == "music" else video_folder
        active_format = music_format if work_mode == "music" else video_format

        return dict(
            folder=active_folder,
            music_folder=music_folder,
            video_folder=video_folder,
            format=active_format or self.resolution_var.get(),
            music_format=music_format,
            video_format=video_format,
            audio_bitrate_mode=self.audio_bitrate_mode_var.get(),
            audio_custom_bitrate=self.audio_custom_bitrate_var.get().strip() or "192",
            playlist=bool(self.playlist_var.get()),
            fragments=int(self.fragments_var.get()),
            cookies_browser=self.cookies_browser_var.get(),
            cookies_file=self.cookies_file_var.get().strip(),
            work_mode=work_mode,
            music_structure=self.music_structure_var.get() if hasattr(self, "music_structure_var") else "Artista\\Álbum",
            music_filename_template=(
                self.music_filename_var.get().strip()
                if hasattr(self, "music_filename_var") and self.music_filename_var.get().strip()
                else "{faixa:02} - {titulo}"
            ),
            deezer_arl=self.deezer_arl_var.get().strip() if hasattr(self, "deezer_arl_var") else "",
            deezer_quality=self.deezer_quality_var.get().strip() if hasattr(self, "deezer_quality_var") else "auto",
            create_collection_zip=bool(self.create_zip_var.get()) if hasattr(self, "create_zip_var") else False,
        )

    def analyze_links(self):
        self._prepare_queue(False)

    def _prepare_queue(self, auto_start):
        self._prepare_sources(self._get_urls(), auto_start)

    def _prepare_sources(self, sources, auto_start=False):
        if self.busy or self.queue_repository is None:
            return
        sources = [str(source).strip() for source in sources if str(source).strip()]
        if not sources:
            messagebox.showwarning(
                "Fila de downloads",
                "Informe uma pesquisa, link ou mídia para continuar.",
            )
            return
        if any(item["status"] in RUNNABLE for item in self.queue_items):
            if not messagebox.askyesno(
                "Fila de downloads",
                "Substituir a lista salva pelos novos itens? Os arquivos já baixados serão preservados.",
            ):
                return
        options = self._capture_options()
        try:
            if is_audio_format(options["format"]):
                bitrate_from_options(options)
        except ValueError as exc:
            messagebox.showwarning("Taxa de bits", str(exc))
            return
        if not options["folder"]:
            options["folder"] = str(Path.home() / "Downloads")
            self.folder_var.set(options["folder"])
        self._save_preferences()
        self.download_control = DownloadControl()
        self._set_queue_busy(True)
        self._set_indeterminate_progress("Preparando a fila...")

        def worker():
            try:
                status = self.dependencies.ensure(self.queue_log, force=False)
                self.dependency_status = status
                items = discover(
                    sources,
                    options,
                    status.yt_dlp_path,
                    self.download_control,
                    self.dependencies.runtime_environment(),
                    self.queue_log,
                )
                self.download_control.checkpoint()
                self.queue_repository.replace(items, options, sources)
                self.event_queue.put(("queue_prepared", auto_start))
            except Exception as exc:
                self.event_queue.put(("queue_analysis_error", str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def _set_queue_busy(self, busy):
        self.busy = busy
        self.download_button.configure(state="disabled" if busy else "normal")
        self.analyze_button.configure(state="disabled" if busy else "normal")
        self.stop_button.configure(state="normal" if busy else "disabled")
        self.pause_button.configure(state="normal" if busy else "disabled", text="Pausar")
        state = "disabled" if busy else "normal"
        self.clear_queue_button.configure(state=state)
        self.remove_queue_button.configure(state=state)
        self.clear_completed_button.configure(state=state)
        self.remove_completed_button.configure(state=state)
        for button in getattr(self, "context_download_buttons", []):
            button.configure(state=state)
        for button in getattr(self, "context_pause_buttons", []):
            button.configure(state="normal" if busy else "disabled", text="⏸")
        for button in getattr(self, "context_stop_buttons", []):
            button.configure(state="normal" if busy else "disabled")

    def _queue_prepared(self, auto_start):
        try:
            self.download_control.check_cancelled()
        except DownloadCancelled:
            auto_start = False
        self._set_queue_busy(False)
        self.progress.stop()
        self.progress.configure(mode="determinate")
        self.progress_value_var.set(0)
        self._refresh_queue()
        self.download_item_var.set("Selecione os itens e clique em Continuar fila")
        self.download_metrics_var.set("A lista e as seleções são salvas automaticamente.")
        if getattr(self, "work_mode", "video") == "music":
            current_items = self.queue_repository.snapshot().get("items", [])
            if current_items and all(item.get("status") == "failed" for item in current_items):
                details = "\n".join(
                    str(item.get("error") or "Falha ao pesquisar na Deezer.")
                    for item in current_items[:3]
                )
                messagebox.showerror("Pesquisa Deezer", details)
        if auto_start:
            self._start_saved_queue()

    def start_download(self):
        if self.busy or self.queue_repository is None:
            return
        job = self.queue_repository.snapshot()
        has_active_items = any(item["status"] != "completed" for item in job["items"])
        if not has_active_items or self._get_urls() != job.get("sources", []):
            self._prepare_queue(True)
        else:
            self._start_saved_queue()

    def _start_saved_queue(self):
        job = self.queue_repository.snapshot()
        if not any(item["enabled"] and item["status"] in RUNNABLE for item in job["items"]):
            messagebox.showinfo("Fila de downloads", "Marque vídeos pendentes ou use Repetir falhas. Os concluídos não serão baixados novamente.")
            return
        options = job["options"]
        try:
            if is_audio_format(options["format"]):
                bitrate_from_options(options)
        except ValueError as exc:
            messagebox.showwarning("Taxa de bits", str(exc))
            return
        current = self._capture_options()
        if not queue_options_compatible(current, options):
            messagebox.showinfo("Fila de downloads", "A fila usa a pasta, o formato e as opções definidos ao listar os itens. Para alterar, carregue a fila novamente.")
            return
        if options.get("cookies_file") and not Path(options["cookies_file"]).is_file():
            messagebox.showwarning("Fila de downloads", "O arquivo de cookies da fila não foi encontrado. Atualize as configurações e liste os vídeos novamente.")
            return
        try:
            Path(options["folder"]).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showerror("Fila de downloads", f"Não foi possível abrir a pasta: {exc}")
            return
        self.download_options = dict(options)
        self.queue_running = True
        self.download_control = DownloadControl()
        self.download_fragments = options["fragments"]
        self.download_job_started_at = self._download_clock()
        self.queue_initial_done = queue_summary(job["items"])["done"]
        self._set_queue_busy(True)
        self._save_preferences()

        def worker():
            try:
                status = self.dependencies.ensure(self.queue_log, force=False)
                run_queue(self, self.queue_repository, options, status.yt_dlp_path)
            except Exception as exc:
                self.queue_log(f"Não foi possível iniciar a fila: {exc}")
                self.event_queue.put(("download_finished", {"failures": 1, "completed": 0, "stopped": True}))
        threading.Thread(target=worker, daemon=True).start()
