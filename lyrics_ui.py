"""Apple Music-style synchronized karaoke lyrics viewer window."""
from __future__ import annotations

import threading
from pathlib import Path
from tkinter import Canvas, Frame, Label, StringVar, Toplevel, messagebox, ttk

from lyrics_service import fetch_lyrics, get_active_lyric_index, save_lrc_file
from ui_layout import APPLE_DARK_BG, APPLE_TEXT_MUTED, APPLE_TEXT_PRIMARY, APPLE_TEXT_SECONDARY, PROGRAM_BLUE


class LyricsDialog(Toplevel):
    def __init__(
        self,
        master,
        music_player,
        text_family: str = "Segoe UI",
        display_family: str = "Segoe UI",
    ) -> None:
        super().__init__(master)
        self.music_player = music_player
        self.text_family = text_family
        self.display_family = display_family

        self.title("Letras — C² Downloader")
        self.geometry("540x650")
        self.minsize(400, 500)
        self.configure(bg=APPLE_DARK_BG)

        self.synced_lines: list[tuple[int, str]] = []
        self.plain_lyrics: str = ""
        self.raw_lrc: str = ""
        self.active_index: int = -1
        self._line_labels: list[Label] = []
        self._poll_job = None

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.load_lyrics()

    def _build_ui(self) -> None:
        # Header container
        header = Frame(self, bg=APPLE_DARK_BG, padx=20, pady=16)
        header.pack(fill="x")

        self.track_var = StringVar(value="Carregando faixa...")
        self.artist_var = StringVar(value="")
        self.status_var = StringVar(value="Buscando letras sincronizadas...")

        Label(
            header,
            textvariable=self.track_var,
            font=(self.display_family, 15, "bold"),
            fg=APPLE_TEXT_PRIMARY,
            bg=APPLE_DARK_BG,
            anchor="w",
        ).pack(fill="x")

        Label(
            header,
            textvariable=self.artist_var,
            font=(self.text_family, 11),
            fg=PROGRAM_BLUE,
            bg=APPLE_DARK_BG,
            anchor="w",
        ).pack(fill="x", pady=(2, 4))

        meta_row = Frame(header, bg=APPLE_DARK_BG)
        meta_row.pack(fill="x", pady=(4, 0))

        Label(
            meta_row,
            textvariable=self.status_var,
            font=(self.text_family, 9),
            fg=APPLE_TEXT_SECONDARY,
            bg=APPLE_DARK_BG,
        ).pack(side="left")

        self.save_btn = ttk.Button(
            meta_row,
            text="💾 Salvar .lrc",
            command=self._save_lrc_clicked,
            state="disabled",
        )
        self.save_btn.pack(side="right")

        # Separator line
        Frame(self, bg="#2c2c2e", height=1).pack(fill="x")

        # Scrollable lyrics canvas
        canvas_frame = Frame(self, bg=APPLE_DARK_BG)
        canvas_frame.pack(fill="both", expand=True, padx=10, pady=10)

        self.canvas = Canvas(
            canvas_frame,
            bg=APPLE_DARK_BG,
            highlightthickness=0,
            borderwidth=0,
        )
        self.scrollbar = ttk.Scrollbar(
            canvas_frame, orient="vertical", command=self.canvas.yview
        )
        self.canvas.configure(yscrollcommand=self.scrollbar.set)

        self.scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)

        self.lyrics_container = Frame(self.canvas, bg=APPLE_DARK_BG)
        self.canvas_window = self.canvas.create_window(
            (0, 0), window=self.lyrics_container, anchor="nw"
        )

        self.lyrics_container.bind("<Configure>", self._on_container_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.bind("<MouseWheel>", self._on_mousewheel)

    def _on_container_configure(self, _event=None) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event) -> None:
        self.canvas.itemconfig(self.canvas_window, width=event.width)

    def _on_mousewheel(self, event) -> None:
        delta = -1 if event.delta > 0 else 1
        self.canvas.yview_scroll(delta * 2, "units")

    def load_lyrics(self) -> None:
        track = self.music_player.track_title or ""
        artist = self.music_player.track_artist or ""
        album = self.music_player.track_album or ""
        dur = self.music_player.get_duration()

        if not track:
            self.track_var.set("Nenhuma música em reprodução")
            self.status_var.set("Inicie uma música para carregar a letra.")
            return

        self.track_var.set(track)
        self.artist_var.set(f"{artist} — {album}" if album else artist)
        self.status_var.set("🔍 Consultando LRCLIB...")

        def _worker():
            res = fetch_lyrics(track, artist, album, dur)
            self.after(0, lambda: self._apply_lyrics_result(res))

        threading.Thread(target=_worker, daemon=True).start()

    def _apply_lyrics_result(self, res: dict) -> None:
        for lbl in self._line_labels:
            lbl.destroy()
        self._line_labels.clear()

        if not res.get("found"):
            self.status_var.set("Letra não encontrada para esta faixa.")
            lbl = Label(
                self.lyrics_container,
                text="Nenhuma letra encontrada nos servidores públicos.\n\nExperimente pesquisar com o nome original do artista e da música.",
                font=(self.text_family, 11),
                fg=APPLE_TEXT_MUTED,
                bg=APPLE_DARK_BG,
                justify="center",
                pady=60,
            )
            lbl.pack(fill="x")
            self._line_labels.append(lbl)
            return

        self.raw_lrc = res.get("raw_lrc") or ""
        self.plain_lyrics = res.get("plain") or ""
        self.synced_lines = res.get("synced") or []

        if self.raw_lrc:
            self.save_btn.configure(state="normal")

        if self.synced_lines:
            self.status_var.set("✨ Letra sincronizada em tempo real (Karaokê)")
            # Add top padding
            pad_top = Label(self.lyrics_container, text="", bg=APPLE_DARK_BG, height=2)
            pad_top.pack()

            for _, text in self.synced_lines:
                display_text = text if text.strip() else "♪"
                lbl = Label(
                    self.lyrics_container,
                    text=display_text,
                    font=(self.display_family, 13, "bold"),
                    fg=APPLE_TEXT_MUTED,
                    bg=APPLE_DARK_BG,
                    wraplength=480,
                    justify="center",
                    pady=8,
                )
                lbl.pack(fill="x", padx=16)
                self._line_labels.append(lbl)

            # Add bottom padding
            pad_bottom = Label(self.lyrics_container, text="", bg=APPLE_DARK_BG, height=8)
            pad_bottom.pack()

            self._poll_playback()
        else:
            self.status_var.set("📄 Letra estática (sem sincronização de tempo)")
            for line in self.plain_lyrics.splitlines():
                lbl = Label(
                    self.lyrics_container,
                    text=line or " ",
                    font=(self.text_family, 11),
                    fg=APPLE_TEXT_PRIMARY,
                    bg=APPLE_DARK_BG,
                    wraplength=480,
                    justify="center",
                    pady=3,
                )
                lbl.pack(fill="x", padx=16)
                self._line_labels.append(lbl)

    def _poll_playback(self) -> None:
        if not self.synced_lines or not self.winfo_exists():
            return

        pos_ms = self.music_player.get_position_ms()
        current_idx = get_active_lyric_index(self.synced_lines, pos_ms)

        if current_idx != self.active_index and 0 <= current_idx < len(self._line_labels):
            # Dim previous
            if 0 <= self.active_index < len(self._line_labels):
                self._line_labels[self.active_index].configure(
                    fg=APPLE_TEXT_MUTED,
                    font=(self.display_family, 13, "bold"),
                )

            # Highlight current with Apple Music glow
            self.active_index = current_idx
            active_lbl = self._line_labels[current_idx]
            active_lbl.configure(
                fg="#ffffff",
                font=(self.display_family, 16, "bold"),
            )

            # Scroll to keep active line centered
            self.after(50, lambda: self._scroll_to_active(active_lbl))

        self._poll_job = self.after(250, self._poll_playback)

    def _scroll_to_active(self, widget: Label) -> None:
        try:
            bbox = self.canvas.bbox("all")
            if not bbox:
                return
            total_height = bbox[3] - bbox[1]
            if total_height <= 0:
                return
            y = widget.winfo_y()
            canvas_h = self.canvas.winfo_height()
            target_fraction = max(0.0, min(1.0, (y - canvas_h / 2.5) / float(total_height)))
            self.canvas.yview_moveto(target_fraction)
        except Exception:
            pass

    def _save_lrc_clicked(self) -> None:
        current_path = getattr(self.music_player, "_current_path", None)
        if not current_path or not Path(current_path).is_file():
            messagebox.showinfo(
                "Salvar .lrc",
                "A faixa atual é uma prévia online. Baixe a música primeiro para salvar o arquivo .lrc junto a ela.",
            )
            return

        saved = save_lrc_file(current_path, self.raw_lrc)
        if saved:
            messagebox.showinfo(
                "Letra Salva",
                f"Arquivo de letras sincronizadas salvo com sucesso:\n{saved.name}",
            )
        else:
            messagebox.showwarning(
                "Salvar .lrc",
                "Não foi possível salvar o arquivo .lrc no diretório da música.",
            )

    def _on_close(self) -> None:
        if self._poll_job is not None:
            try:
                self.after_cancel(self._poll_job)
            except Exception:
                pass
        self.destroy()
