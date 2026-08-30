from __future__ import annotations

import threading
from pathlib import Path
from tkinter import END, messagebox, ttk

from download_control import DownloadCancelled, DownloadControl
from download_queue import ACTIVE, LABELS, RUNNABLE, queue_summary
from queue_service import discover, run_queue


class QueueUI:
    def _build_episode_list(self, parent):
        controls = ttk.Frame(parent)
        controls.pack(fill="x", pady=(0, 6))
        self.analyze_button = ttk.Button(controls, text="Listar vídeos", command=self.analyze_links)
        self.analyze_button.pack(side="left")
        ttk.Button(controls, text="Marcar todos", command=lambda: self._select_items(True)).pack(side="left", padx=4)
        ttk.Button(controls, text="Desmarcar", command=lambda: self._select_items(False)).pack(side="left")
        self.queue_count = ttk.Label(parent, text="Liste os vídeos para selecionar os episódios.")
        self.queue_count.pack(anchor="w", pady=(0, 4))
        table = ttk.Frame(parent)
        table.pack(fill="both", pady=(0, 6))
        columns = ("selected", "title", "quality", "status", "percent")
        self.episode_tree = ttk.Treeview(table, columns=columns, show="headings", height=4, selectmode="extended")
        for name, label, width in zip(columns, ("✓", "Vídeo / episódio", "Qualidade", "Situação", "%"), (32, 290, 100, 112, 48)):
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
            ("Vídeo / episódio", "Qualidade", "Arquivo salvo"),
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
            options = job["options"]
            needs_resume = any(item["kind"] != "unresolved" and item["status"] in (RUNNABLE | {"failed"}) for item in job["items"])
            if needs_resume:
                self.folder_var.set(options["folder"])
                self.resolution_var.set(options["format"])
                self.playlist_var.set(options["playlist"])
                self.fragments_var.set(str(options["fragments"]))
                self.cookies_browser_var.set(options.get("cookies_browser", "Nenhum"))
                self.cookies_file_var.set(options.get("cookies_file", ""))
            self.url_text.insert("1.0", "\n".join(job.get("sources", [])))
            if needs_resume:
                self.queue_log("Fila recuperada. Selecione os vídeos e clique em Continuar fila; nenhum download inicia automaticamente.")
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
        if self.queue_running and not self.active_queue_id:
            self.progress.configure(mode="determinate", value=summary["overall"])
        if not self.busy:
            self.download_button.configure(text="Continuar fila" if active_items else "Baixar")
        self._show_episode_details()
        self._show_completed_details()

    def _show_episode_details(self, _event=None):
        selected = self.episode_tree.selection()
        item = next((item for item in self.queue_items if selected and item["id"] == selected[0]), None)
        error = item.get("error", "") if item else ""
        self.episode_details.configure(text=error)
        if error:
            self.episode_details.pack(fill="x", pady=(0, 4), before=self.episode_actions)
        else:
            self.episode_details.pack_forget()

    def _show_completed_details(self, _event=None):
        selected = self.completed_tree.selection()
        item = next((item for item in self.queue_items if selected and item["id"] == selected[0]), None)
        files = item.get("files", []) if item else []
        self.completed_details.configure(
            text="Arquivos mantidos no computador:\n" + "\n".join(files) if files else "",
        )

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
        return dict(folder=self.folder_var.get().strip(), format=self.resolution_var.get(),
                    playlist=bool(self.playlist_var.get()), fragments=int(self.fragments_var.get()),
                    cookies_browser=self.cookies_browser_var.get(), cookies_file=self.cookies_file_var.get().strip())

    def analyze_links(self):
        self._prepare_queue(False)

    def _prepare_queue(self, auto_start):
        if self.busy or self.queue_repository is None:
            return
        sources = self._get_urls()
        if not sources:
            messagebox.showwarning("Fila de downloads", "Informe pelo menos um link.")
            return
        if any(item["status"] in RUNNABLE for item in self.queue_items):
            if not messagebox.askyesno("Fila de downloads", "Substituir a lista salva pelos links informados? Os arquivos já baixados serão preservados."):
                return
        options = self._capture_options()
        if not options["folder"]:
            options["folder"] = str(Path.home() / "Downloads")
            self.folder_var.set(options["folder"])
        self._save_preferences()
        self.download_control = DownloadControl()
        self._set_queue_busy(True)
        self._set_indeterminate_progress("Listando vídeos e episódios...")

        def worker():
            try:
                status = self.dependencies.ensure(self.queue_log, force=False)
                self.dependency_status = status
                items = discover(sources, options, status.yt_dlp_path, self.download_control,
                                 self.dependencies.runtime_environment(), self.queue_log)
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

    def _queue_prepared(self, auto_start):
        try:
            self.download_control.check_cancelled()
        except DownloadCancelled:
            auto_start = False
        self._set_queue_busy(False)
        self.progress.stop()
        self.progress.configure(mode="determinate", value=0)
        self._refresh_queue()
        self.download_item_var.set("Selecione os episódios e clique em Continuar fila")
        self.download_metrics_var.set("A lista e as seleções são salvas automaticamente.")
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
        current = self._capture_options()
        if current != options:
            messagebox.showinfo("Fila de downloads", "A fila usa a pasta, o formato e as opções definidos ao listar os vídeos. Para alterar, clique em Listar vídeos novamente.")
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
