"""Small in-app music player for local audio and official public Deezer previews."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from app_config import APP_VERSION


MAX_PREVIEW_BYTES = 20 * 1024 * 1024
IN_APP_EXTENSIONS = {".mp3", ".ogg", ".wav", ".flac"}


class MusicPlayerError(RuntimeError):
    pass


def is_public_deezer_preview(url: str) -> bool:
    parsed = urlparse(str(url or "").strip())
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and host.endswith("dzcdn.net")


class MusicPlayer:
    def __init__(self, cache_dir: Path):
        self.cache_dir = Path(cache_dir)
        self._pygame = None
        self._paused = False
        self._current_source: str | None = None

    def _ensure_mixer(self):
        if self._pygame is not None:
            return self._pygame
        try:
            import pygame

            if not pygame.mixer.get_init():
                pygame.mixer.init()
        except Exception as exc:
            raise MusicPlayerError(f"Não foi possível iniciar o player de áudio: {exc}") from exc
        self._pygame = pygame
        return pygame

    def _preview_file(self, url: str) -> Path:
        if not is_public_deezer_preview(url):
            raise MusicPlayerError("A reprodução aceita somente a prévia pública oficial da Deezer.")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
        path = self.cache_dir / f"{digest}.mp3"
        if path.is_file() and path.stat().st_size > 0:
            return path

        request = Request(
            url,
            headers={"User-Agent": f"C2VideoDownloader/{APP_VERSION}"},
        )
        try:
            with urlopen(request, timeout=20) as response:
                data = response.read(MAX_PREVIEW_BYTES + 1)
        except OSError as exc:
            raise MusicPlayerError(f"Não foi possível carregar a prévia da Deezer: {exc}") from exc
        if not data or len(data) > MAX_PREVIEW_BYTES:
            raise MusicPlayerError("A prévia recebida da Deezer é inválida ou excede o limite permitido.")
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(data)
        temporary.replace(path)
        return path

    def play_preview(self, url: str) -> Path:
        path = self._preview_file(url)
        self.play_file(path, allow_external=False)
        self._current_source = str(url)
        return path

    def play_file(self, path: Path, *, allow_external: bool = True) -> str:
        path = Path(path)
        if not path.is_file():
            raise MusicPlayerError("O arquivo de áudio não foi encontrado.")
        if path.suffix.lower() not in IN_APP_EXTENSIONS and allow_external:
            if os.name == "nt":
                os.startfile(str(path))
                return "external"
            raise MusicPlayerError("Este formato deve ser aberto em um player externo.")

        pygame = self._ensure_mixer()
        try:
            pygame.mixer.music.load(str(path))
            pygame.mixer.music.play()
        except Exception as exc:
            if allow_external and os.name == "nt":
                os.startfile(str(path))
                return "external"
            raise MusicPlayerError(f"Não foi possível reproduzir o áudio: {exc}") from exc
        self._paused = False
        self._current_source = str(path.resolve())
        return "internal"

    def pause(self) -> None:
        if self._pygame is not None:
            self._pygame.mixer.music.pause()
        self._paused = True

    def resume(self) -> None:
        if self._pygame is not None:
            self._pygame.mixer.music.unpause()
        self._paused = False

    def stop(self) -> None:
        if self._pygame is not None:
            self._pygame.mixer.music.stop()
        self._paused = False
        self._current_source = None

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def current_source(self) -> str | None:
        return self._current_source

    def is_playing(self) -> bool:
        if self._pygame is None:
            return False
        try:
            if not self._pygame.mixer.get_init():
                return False
            return bool(self._pygame.mixer.music.get_busy()) and not self._paused
        except Exception:
            return False

    def is_paused(self) -> bool:
        return self._paused

    def is_active(self) -> bool:
        return self.is_playing() or self.is_paused()
