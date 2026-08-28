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
        buttons = ttk.Frame(parent)
        buttons.pack(fill="x", pady=(0, 8))
        self.episode_actions = buttons
        ttk.Button(buttons, text="Cancelar selecionados", command=self.cancel_selected).pack(side="right")
        ttk.Button(buttons, text="Repetir falhas", command=self.retry_failed).pack(side="right", padx=6)

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
        existing = set(self.episode_tree.get_children())
        for item in self.queue_items:
            values = ("✓" if item["enabled"] else "", item["title"], item.get("quality", "A definir"),
                      LABELS[item["status"]], "100" if item["status"] == "completed" else "—")
            if item["id"] in existing:
                self.episode_tree.item(item["id"], values=values)
                existing.remove(item["id"])
            else:
                self.episode_tree.insert("", END, iid=item["id"], values=values)
        for item_id in existing:
            self.episode_tree.delete(item_id)
        summary = queue_summary(self.queue_items)
        complete = sum(item["enabled"] and item["status"] == "completed" for item in self.queue_items)
        self.queue_count.configure(text=f"{len(self.queue_items)} vídeo(s) • {summary['total']} marcado(s) • {complete} concluído(s)")
        if self.queue_running and not self.active_queue_id:
            self.progress.configure(mode="determinate", value=summary["overall"])
        if not self.busy:
            self.download_button.configure(text="Continuar fila" if self.queue_items else "Baixar")
        self._show_episode_details()

    def _show_episode_details(self, _event=None):
        selected = self.episode_tree.selection()
        item = next((item for item in self.queue_items if selected and item["id"] == selected[0]), None)
        error = item.get("error", "") if item else ""
        self.episode_details.configure(text=error)
        if error:
            self.episode_details.pack(fill="x", pady=(0, 4), before=self.episode_actions)
        else:
            self.episode_details.pack_forget()

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
        if not job["items"] or self._get_urls() != job.get("sources", []):
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
