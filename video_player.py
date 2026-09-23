from __future__ import annotations

import importlib
import os
import shutil
import threading
from pathlib import Path
from urllib.parse import urlparse


class VideoPlayerError(RuntimeError):
    """Raised when the embedded video player cannot be started."""


_DLL_HANDLES = []


def find_vlc_directory() -> Path | None:
    """Return the folder that contains libvlc on Windows, when available."""
    candidates = []
    configured = os.environ.get("VLC_HOME", "").strip()
    if configured:
        candidates.append(Path(configured))
    for variable in ("PROGRAMFILES", "PROGRAMFILES(X86)"):
        base = os.environ.get(variable, "").strip()
        if base:
            candidates.append(Path(base) / "VideoLAN" / "VLC")
    candidates.extend(
        (
            Path(r"C:\Program Files\VideoLAN\VLC"),
            Path(r"C:\Program Files (x86)\VideoLAN\VLC"),
        )
    )
    executable = shutil.which("vlc")
    if executable:
        candidates.append(Path(executable).resolve().parent)

    seen = set()
    for candidate in candidates:
        normalized = str(candidate).casefold()
        if normalized in seen:
            continue
        seen.add(normalized)
        if (candidate / "libvlc.dll").is_file() or (candidate / "vlc.exe").is_file():
            return candidate
    return None


def _load_vlc():
    vlc_dir = find_vlc_directory()
    if os.name == "nt" and vlc_dir is not None:
        os.environ["PATH"] = str(vlc_dir) + os.pathsep + os.environ.get("PATH", "")
        plugins = vlc_dir / "plugins"
        if plugins.is_dir():
            os.environ["VLC_PLUGIN_PATH"] = str(plugins)
        add_directory = getattr(os, "add_dll_directory", None)
        if add_directory is not None:
            try:
                _DLL_HANDLES.append(add_directory(str(vlc_dir)))
            except OSError:
                pass
    try:
        return importlib.import_module("vlc")
    except (ImportError, OSError) as exc:
        raise VideoPlayerError(
            "O player integrado precisa do VLC Media Player instalado. "
            "Instale o VLC 3.x pelo site oficial e abra o C² Downloader novamente."
        ) from exc


def is_video_source(value: str | Path) -> bool:
    source = str(value).strip()
    if not source:
        return False
    path = Path(source)
    if path.is_file():
        return True
    parsed = urlparse(source)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


class EmbeddedVideoPlayer:
    """Small, lazy libVLC wrapper suitable for embedding in a Tk window."""

    def __init__(self) -> None:
        self._vlc = None
        self._instance = None
        self._player = None
        self._media = None
        self._lock = threading.RLock()
        self.source: str | None = None

    def _ensure(self, window_handle: int) -> None:
        if self._player is not None:
            if os.name == "nt":
                self._player.set_hwnd(window_handle)
            return
        vlc = _load_vlc()
        try:
            instance = vlc.Instance(
                "--no-video-title-show",
                "--quiet",
                "--network-caching=1500",
            )
            player = instance.media_player_new()
            if os.name == "nt":
                player.set_hwnd(window_handle)
            elif hasattr(player, "set_xwindow"):
                player.set_xwindow(window_handle)
        except Exception as exc:
            raise VideoPlayerError(
                "O VLC foi encontrado, mas o motor de reprodução não pôde ser iniciado."
            ) from exc
        self._vlc = vlc
        self._instance = instance
        self._player = player

    def play(self, source: str | Path, window_handle: int) -> None:
        value = str(source).strip()
        if not is_video_source(value):
            raise VideoPlayerError("A fonte de vídeo selecionada não é válida ou não existe.")
        with self._lock:
            self._ensure(window_handle)
            try:
                media = self._instance.media_new(value)
                if value.startswith(("http://", "https://")):
                    media.add_option(":network-caching=1500")
                self._player.set_media(media)
                result = self._player.play()
            except Exception as exc:
                raise VideoPlayerError("Não foi possível iniciar a reprodução deste vídeo.") from exc
            if isinstance(result, int) and result < 0:
                raise VideoPlayerError("O VLC recusou a fonte de vídeo selecionada.")
            self._media = media
            self.source = value

    def pause(self) -> None:
        with self._lock:
            if self._player is not None:
                self._player.set_pause(1)

    def resume(self) -> None:
        with self._lock:
            if self._player is not None:
                self._player.set_pause(0)

    def stop(self) -> None:
        with self._lock:
            if self._player is not None:
                self._player.stop()
            self.source = None

    def release(self) -> None:
        with self._lock:
            try:
                if self._player is not None:
                    self._player.stop()
                    self._player.release()
                if self._instance is not None:
                    self._instance.release()
            finally:
                self._player = None
                self._instance = None
                self._media = None
                self.source = None

    def _state_name(self) -> str:
        if self._player is None:
            return "stopped"
        try:
            return str(self._player.get_state()).split(".")[-1].lower()
        except Exception:
            return "stopped"

    def is_playing(self) -> bool:
        return bool(self._player is not None and self._player.is_playing())

    def is_paused(self) -> bool:
        return self._state_name() == "paused"

    def is_active(self) -> bool:
        return self._state_name() not in {
            "nothing_special", "nothingspecial", "stopped", "ended", "error",
        }

    def get_time(self) -> int:
        if self._player is None:
            return 0
        return max(0, int(self._player.get_time()))

    def get_length(self) -> int:
        if self._player is None:
            return 0
        return max(0, int(self._player.get_length()))

    def get_position(self) -> float:
        if self._player is None:
            return 0.0
        return max(0.0, min(1.0, float(self._player.get_position())))

    def set_position(self, position: float) -> None:
        if self._player is not None:
            self._player.set_position(max(0.0, min(1.0, float(position))))

    def set_volume(self, volume: int | float) -> None:
        if self._player is not None:
            self._player.audio_set_volume(max(0, min(100, int(float(volume)))))
