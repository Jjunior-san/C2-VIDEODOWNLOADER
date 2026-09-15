"""Bounded, cancellable reading for child processes that may stop producing output."""
from __future__ import annotations

import queue
import subprocess
import threading
from collections.abc import Callable, Iterator


class ProcessInactivityError(RuntimeError):
    pass


def monitored_lines(
    process: subprocess.Popen,
    control,
    inactivity_seconds: float | Callable[[], float | None],
) -> Iterator[str]:
    """Yield output without letting a silent child block the worker forever."""
    if process.stdout is None:
        return
    events: queue.Queue[tuple[str, object]] = queue.Queue()

    def read_output() -> None:
        try:
            for line in process.stdout:
                events.put(("line", line))
        except Exception as exc:
            events.put(("error", exc))
        finally:
            events.put(("done", None))

    reader = threading.Thread(target=read_output, daemon=True, name="c2-process-output")
    reader.start()
    last_output = control.clock()
    while True:
        control.checkpoint()
        try:
            kind, payload = events.get(timeout=0.25)
        except queue.Empty:
            timeout = inactivity_seconds() if callable(inactivity_seconds) else inactivity_seconds
            if timeout and control.clock() - last_output >= timeout:
                control.terminate_active()
                raise ProcessInactivityError(
                    f"O mecanismo ficou {int(timeout)} segundos sem responder.",
                )
            if process.poll() is not None and not reader.is_alive():
                break
            continue
        if kind == "line":
            last_output = control.clock()
            yield str(payload)
        elif kind == "error":
            raise RuntimeError(f"Falha ao ler a saída do mecanismo: {payload}") from payload
        else:
            break


def close_process(process: subprocess.Popen, control, timeout: float = 10) -> None:
    """Release the complete child tree, escalating only if graceful stop fails."""
    try:
        if process.poll() is None:
            control.terminate_active()
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            control.terminate_active(force=True)
            process.wait(timeout=timeout)
    finally:
        if process.stdout:
            process.stdout.close()
        control.release(process)
