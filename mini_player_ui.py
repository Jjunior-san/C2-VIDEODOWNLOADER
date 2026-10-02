"""Apple Music-style compact floating MiniPlayer window."""
from __future__ import annotations

from tkinter import DoubleVar, Frame, Label, StringVar, Toplevel, ttk

from ui_layout import (
    APPLE_CARD_BG,
    APPLE_DARK_BG,
    APPLE_TEXT_MUTED,
    APPLE_TEXT_PRIMARY,
    APPLE_TEXT_SECONDARY,
    PROGRAM_BLUE,
)


def _fmt_time(ms: int) -> str:
    secs = max(0, ms // 1000)
    m, s = divmod(secs, 60)
    return f"{m:02d}:{s:02d}"


class MiniPlayer(Toplevel):
    def __init__(
        self,
        master,
        music_player,
        text_family: str = "Segoe UI",
        display_family: str = "Segoe UI",
        on_restore=None,
    ) -> None:
        super().__init__(master)
        self.music_player = music_player
        self.text_family = text_family
        self.display_family = display_family
        self.on_restore = on_restore

        self.title("MiniPlayer — C² Downloader")
        self.geometry("380x160")
        self.resizable(False, False)
        self.attributes("-topmost", True)
        self.configure(bg=APPLE_DARK_BG)

        self.track_var = StringVar(value="Nenhuma faixa")
        self.artist_var = StringVar(value="")
        self.time_var = StringVar(value="00:00 / 00:00")
        self.seek_var = DoubleVar(value=0.0)
        self.volume_var = DoubleVar(value=int(self.music_player.get_volume() * 100))
        self._dragging_seek = False
        self._poll_job = None

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._update_loop()

    def _build_ui(self) -> None:
        main_frame = Frame(self, bg=APPLE_DARK_BG, padx=12, pady=10)
        main_frame.pack(fill="both", expand=True)

        top_row = Frame(main_frame, bg=APPLE_DARK_BG)
        top_row.pack(fill="x", pady=(0, 6))

        # Mini artwork box
        art_box = Frame(top_row, bg=APPLE_CARD_BG, width=44, height=44)
        art_box.pack(side="left", padx=(0, 10))
        art_box.pack_propagate(False)
        Label(
            art_box,
            text="🎵",
            font=(self.display_family, 16),
            bg=APPLE_CARD_BG,
            fg=PROGRAM_BLUE,
        ).pack(expand=True)

        info_box = Frame(top_row, bg=APPLE_DARK_BG)
        info_box.pack(side="left", fill="x", expand=True)

        Label(
            info_box,
            textvariable=self.track_var,
            font=(self.display_family, 10, "bold"),
            fg=APPLE_TEXT_PRIMARY,
            bg=APPLE_DARK_BG,
            anchor="w",
        ).pack(fill="x")

        Label(
            info_box,
            textvariable=self.artist_var,
            font=(self.text_family, 9),
            fg=APPLE_TEXT_SECONDARY,
            bg=APPLE_DARK_BG,
            anchor="w",
        ).pack(fill="x")

        # Restore / Close button
        restore_btn = ttk.Button(
            top_row,
            text="⤢",
            width=3,
            command=self._restore_main,
        )
        restore_btn.pack(side="right")

        # Seek row
        seek_row = Frame(main_frame, bg=APPLE_DARK_BG)
        seek_row.pack(fill="x", pady=(2, 6))

        self.seek_scale = ttk.Scale(
            seek_row,
            from_=0.0,
            to=100.0,
            variable=self.seek_var,
            command=self._on_seek_change,
        )
        self.seek_scale.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.seek_scale.bind("<ButtonPress-1>", lambda _e: setattr(self, "_dragging_seek", True))
        self.seek_scale.bind("<ButtonRelease-1>", self._on_seek_release)

        Label(
            seek_row,
            textvariable=self.time_var,
            font=(self.text_family, 8),
            fg=APPLE_TEXT_MUTED,
            bg=APPLE_DARK_BG,
        ).pack(side="right")

        # Controls row
        ctrls_row = Frame(main_frame, bg=APPLE_DARK_BG)
        ctrls_row.pack(fill="x")

        ttk.Button(ctrls_row, text="⏮", width=3, command=self._seek_backward).pack(side="left")
        self.play_btn = ttk.Button(
            ctrls_row,
            text="▶",
            width=4,
            style="ApplePlay.TButton",
            command=self._toggle_play,
        )
        self.play_btn.pack(side="left", padx=4)
        ttk.Button(ctrls_row, text="⏹", width=3, command=self._stop).pack(side="left")
        ttk.Button(ctrls_row, text="⏭", width=3, command=self._seek_forward).pack(side="left", padx=(0, 10))

        # Volume slider
        Label(
            ctrls_row,
            text="🔊",
            font=(self.text_family, 9),
            fg=APPLE_TEXT_SECONDARY,
            bg=APPLE_DARK_BG,
        ).pack(side="left", padx=(6, 2))

        vol_scale = ttk.Scale(
            ctrls_row,
            from_=0,
            to=100,
            variable=self.volume_var,
            command=self._on_volume_change,
        )
        vol_scale.pack(side="left", fill="x", expand=True)

    def _toggle_play(self) -> None:
        if self.music_player.is_playing():
            self.music_player.pause()
        elif self.music_player.is_paused():
            self.music_player.resume()
        elif self.music_player.current_source:
            self.music_player.resume()

    def _stop(self) -> None:
        self.music_player.stop()

    def _seek_backward(self) -> None:
        cur = self.music_player.get_position()
        self.music_player.set_position(max(0.0, cur - 10.0))

    def _seek_forward(self) -> None:
        cur = self.music_player.get_position()
        dur = self.music_player.get_duration()
        self.music_player.set_position(min(dur, cur + 10.0))

    def _on_seek_change(self, _val) -> None:
        pass

    def _on_seek_release(self, _event) -> None:
        self._dragging_seek = False
        dur = self.music_player.get_duration()
        if dur > 0:
            target = (self.seek_var.get() / 100.0) * dur
            self.music_player.set_position(target)

    def _on_volume_change(self, val) -> None:
        try:
            self.music_player.set_volume(float(val) / 100.0)
        except Exception:
            pass

    def _restore_main(self) -> None:
        if callable(self.on_restore):
            self.on_restore()
        self.destroy()

    def _on_close(self) -> None:
        if self._poll_job is not None:
            try:
                self.after_cancel(self._poll_job)
            except Exception:
                pass
        self.destroy()

    def _update_loop(self) -> None:
        if not self.winfo_exists():
            return

        active = self.music_player.is_active()
        playing = self.music_player.is_playing()

        self.play_btn.configure(text="⏸" if playing else "▶")

        if active:
            pos_ms = self.music_player.get_position_ms()
            dur_ms = self.music_player.get_duration_ms()

            title = self.music_player.track_title or "Faixa em reprodução"
            artist = self.music_player.track_artist or ""
            self.track_var.set(title)
            self.artist_var.set(artist)

            if not self._dragging_seek:
                if dur_ms > 0:
                    pct = (pos_ms / dur_ms) * 100.0
                    self.seek_var.set(min(100.0, max(0.0, pct)))
                else:
                    self.seek_var.set(0.0)

            self.time_var.set(f"{_fmt_time(pos_ms)} / {_fmt_time(dur_ms)}")
        else:
            self.track_var.set("Nenhuma faixa ativa")
            self.artist_var.set("Pronto para tocar")
            if not self._dragging_seek:
                self.seek_var.set(0.0)
            self.time_var.set("00:00 / 00:00")

        self._poll_job = self.after(300, self._update_loop)
