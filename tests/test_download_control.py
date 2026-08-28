import subprocess
import sys
import threading
import time

import psutil
import pytest

import download_control as module
from download_control import DownloadCancelled, DownloadControl


def test_pause_clock_and_checkpoint(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])
    control = DownloadControl()
    control.pause()
    now[0] = 160
    assert control.clock() == 100
    control.resume()
    now[0] = 165
    assert control.clock() == 105
    control.cancel()
    with pytest.raises(DownloadCancelled):
        control.checkpoint()


def test_does_not_start_a_process_while_paused():
    control = DownloadControl()
    control.pause()
    started = threading.Event()
    result = []

    def launch():
        process = control.popen([sys.executable, "-c", "print('ok')"], stdout=subprocess.PIPE)
        started.set()
        result.append(process.communicate(timeout=10)[0])
        control.release(process)

    worker = threading.Thread(target=launch)
    worker.start()
    try:
        assert not started.wait(0.2)
    finally:
        control.resume()
        worker.join(timeout=15)
    assert not worker.is_alive()
    assert result == [b"ok\r\n" if sys.platform == "win32" else b"ok\n"]


def test_pauses_and_resumes_owned_process_tree():
    control = DownloadControl()
    process = control.popen(
        [sys.executable, "-u", "-c",
         "import subprocess,sys,time; "
         "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); "
         "print(p.pid,flush=True); p.wait()"],
        stdout=subprocess.PIPE, text=True,
    )
    assert process.stdout
    child_pid = int(process.stdout.readline().strip())
    child = psutil.Process(child_pid)
    try:
        control.pause()
        assert control.paused
        assert psutil.Process(process.pid).status() == psutil.STATUS_STOPPED
        assert child.status() == psutil.STATUS_STOPPED
        before = control.clock()
        time.sleep(0.15)
        assert control.clock() == before
        control.resume()
        assert not control.paused
        assert psutil.Process(process.pid).status() != psutil.STATUS_STOPPED
        assert child.status() != psutil.STATUS_STOPPED
        control.pause()
        control.cancel()
        process.wait(timeout=10)
        assert not control.paused
    finally:
        control.cancel()
        process.wait(timeout=10)
        process.stdout.close()
        control.release(process)
