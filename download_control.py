from __future__ import annotations

import subprocess
import threading
import time

import psutil


class DownloadCancelled(RuntimeError):
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
            while self._paused_at is not None and not self._cancelled:
                self._condition.wait()
            if self._cancelled:
                raise DownloadCancelled("Trabalho interrompido; arquivos parciais preservados.")

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
            self._condition.notify_all()
