from __future__ import annotations

import subprocess
import threading
import time

import psutil


class DownloadCancelled(RuntimeError):
    pass


class DownloadSkipped(DownloadCancelled):
    pass


class DownloadControl:
    """Controls only the subprocess tree started by this download job."""

    def __init__(self) -> None:
        self._condition = threading.Condition(threading.RLock())
        self._process: subprocess.Popen | None = None
        self._suspended: list[psutil.Process] = []
        self._paused_at: float | None = None
        self._paused_total = 0.0
        self._cancelled = False
        self._skipped = False

    @property
    def paused(self) -> bool:
        with self._condition:
            return self._paused_at is not None

    def clock(self) -> float:
        """Monotonic job clock, excluding time spent paused."""
        with self._condition:
            now = time.monotonic()
            return (self._paused_at if self._paused_at is not None else now) - self._paused_total

    def checkpoint(self) -> None:
        with self._condition:
            while self._paused_at is not None and not self._cancelled and not self._skipped:
                self._condition.wait()
            self.check_cancelled()

    def check_cancelled(self) -> None:
        """Nonblocking check for use while committing a queue state transition."""
        with self._condition:
            if self._cancelled:
                raise DownloadCancelled("Trabalho interrompido; arquivos parciais preservados.")
            if self._skipped:
                raise DownloadSkipped("Vídeo cancelado; continuando os próximos.")

    def popen(self, command: list[str], **kwargs) -> subprocess.Popen:
        with self._condition:
            self.checkpoint()
            self._process = subprocess.Popen(command, **kwargs)
            return self._process

    def release(self, process: subprocess.Popen) -> None:
        with self._condition:
            if self._process is process:
                self._process = None

    def pause(self) -> None:
        with self._condition:
            if self._paused_at is not None or self._cancelled:
                return
            self._paused_at = time.monotonic()
            try:
                if self._process and self._process.poll() is None:
                    root = psutil.Process(self._process.pid)
                    # Stop parents before enumerating children to prevent new launches.
                    pending = [root]
                    while pending:
                        process = pending.pop()
                        try:
                            process.suspend()
                            self._suspended.append(process)
                            pending.extend(process.children())
                        except psutil.NoSuchProcess:
                            pass
            except Exception:
                self.resume()
                raise

    def resume(self) -> None:
        with self._condition:
            remaining: list[psutil.Process] = []
            error: Exception | None = None
            for process in reversed(self._suspended):
                try:
                    process.resume()
                except psutil.NoSuchProcess:
                    pass
                except psutil.Error as exc:
                    remaining.append(process)
                    error = exc
            self._suspended = remaining
            if error:
                raise error
            if self._paused_at is not None:
                self._paused_total += time.monotonic() - self._paused_at
                self._paused_at = None
            self._condition.notify_all()

    def cancel(self) -> None:
        with self._condition:
            self._cancelled = True
            self.resume()
            self.terminate_active()
            self._condition.notify_all()

    def skip(self) -> None:
        with self._condition:
            self._skipped = True
            self.resume()
            self.terminate_active()
            self._condition.notify_all()

    def finish_item(self) -> None:
        with self._condition:
            self._skipped = False

    def terminate_active(self) -> None:
        with self._condition:
            if self._process and self._process.poll() is None:
                try:
                    root = psutil.Process(self._process.pid)
                    for process in reversed([root, *root.children(recursive=True)]):
                        try:
                            process.terminate()
                        except psutil.NoSuchProcess:
                            pass
                except psutil.NoSuchProcess:
                    pass
