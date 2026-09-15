import subprocess
import sys
import time

import pytest

import queue_service
from download_control import DownloadControl
from process_monitor import ProcessInactivityError, close_process, monitored_lines


def test_silent_process_is_terminated_after_inactivity():
    control = DownloadControl()
    process = control.popen(
        [sys.executable, "-u", "-c", "import time; print('ready', flush=True); time.sleep(30)"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    started = time.monotonic()
    try:
        with pytest.raises(ProcessInactivityError, match="sem responder"):
            list(monitored_lines(process, control, 0.3))
    finally:
        close_process(process, control)
    assert time.monotonic() - started < 5
    assert process.poll() is not None


def test_metadata_retries_once_after_inactivity(monkeypatch):
    calls = []
    expected = {"id": "video", "title": "Video"}

    def read_once(*args):
        calls.append(1)
        if len(calls) == 1:
            raise ProcessInactivityError("silent")
        return expected

    monkeypatch.setattr(queue_service, "_read_metadata_once", read_once)
    logs = []
    result = queue_service.read_metadata("engine", "source", {}, None, {}, logs.append)
    assert result == expected
    assert len(calls) == 2
    assert any("reiniciando" in line for line in logs)
