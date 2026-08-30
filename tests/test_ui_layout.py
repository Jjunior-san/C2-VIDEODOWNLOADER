from tkinter import TclError, Tk, Toplevel

import pytest

import c2_launcher  # Apply the same entry-point configuration as the installer.
import youtube_downloader_app as app
from ui_layout import window_dimensions


@pytest.mark.parametrize("area", [
    (0, 0, 1920, 1040), (0, 0, 1366, 728), (0, 0, 1024, 728),
    (0, 0, 800, 560), (0, 0, 640, 440), (-1280, 0, 0, 680),
])
def test_geometry_fits_work_area_including_native_frame(area):
    width, height, x, y = window_dimensions(area)
    assert area[0] <= x < x + width <= area[2]
    assert area[1] <= y < y + height + 40 <= area[3]


@pytest.fixture(scope="module")
def desktop():
    try:
        root = Tk()
    except TclError as exc:
        pytest.skip(f"Tk desktop unavailable: {exc}")
    root.withdraw()
    yield root
    root.destroy()


@pytest.fixture(params=[1.0, 1.5, 2.0])
def window(monkeypatch, request, tmp_path, desktop):
    monkeypatch.setattr(app, "load_user_settings", lambda: {})
    monkeypatch.setattr(app.DownloadApp, "start_maintenance", lambda *args: None)
    monkeypatch.setattr(app, "QUEUE_FILE", tmp_path / "downloads.sqlite3")
    root = Toplevel(desktop)
    root.withdraw()
    root.tk.call("tk", "scaling", request.param * 96 / 72)
    instance = app.DownloadApp(root)
    yield root, instance
    for timer in root.tk.call("after", "info"):
        root.after_cancel(timer)
    root.destroy()  # No writes to the user's settings or background downloads.


@pytest.mark.parametrize("geometry", ["820x680", "780x520", "560x420"])
def test_compact_window_keeps_controls_reachable(window, geometry):
    root, instance = window
    root.geometry(geometry)
    instance.download_item_var.set("Uzak Şehir 38. Bölüm — " * 12)
    instance.download_metrics_var.set("35,8% • 1,0 GB / 3,0 GB • Atual: 5,0 MB/s • Média: 4,0 MB/s\nRestante: 8min • Trabalho: 12,0%")
    root.deiconify()
    root.update()
    assert root.winfo_width() == int(geometry.split("x")[0])
    assert root.winfo_height() == int(geometry.split("x")[1])
    assert len(instance.tabs.tabs()) == 4
    for page in (instance.download_page, instance.completed_page, instance.settings_page, instance.activity_page):
        instance.tabs.select(page)
        root.update()
        assert page.winfo_width() <= root.winfo_width()
        assert page.winfo_height() <= root.winfo_height()
        canvas = page.canvas
        bounds = canvas.bbox(page.window)
        assert bounds[2] >= page.body.winfo_reqwidth()
        assert bounds[3] >= page.body.winfo_reqheight()
        if page.body.winfo_reqheight() > canvas.winfo_height():
            assert page.vertical.winfo_ismapped()
            canvas.yview_moveto(1)
            root.update()
            assert canvas.yview()[1] == 1
    instance.tabs.select(instance.activity_page)
    root.update()
    # Keyboard navigation also scrolls offscreen controls into view.
    instance.log.focus_force()
    root.update()
    assert instance.log.winfo_rooty() >= instance.activity_page.canvas.winfo_rooty()


def test_completion_distinguishes_partial_empty_and_failed_jobs(window):
    _, instance = window
    instance.download_job_started_at = instance._download_clock()
    for failures, completed, expected in [
        (1, 2, "Trabalho finalizado com avisos"),
        (1, 0, "Trabalho finalizado com falhas"),
        (0, 0, "Nenhum arquivo disponível para baixar"),
        (0, 2, "Trabalho finalizado com sucesso"),
    ]:
        instance._finish_download({"failures": failures, "completed": completed})
        assert instance.download_item_var.get() == expected
        assert f"Arquivos concluídos: {completed}" in instance.download_metrics_var.get()


def test_queue_table_selections_retry_and_stop_race(window):
    from download_queue import queue_item
    _, instance = window
    items = [queue_item(f"https://example.com/{i}", str(i)) for i in range(3)]
    items[1]["status"] = "failed"
    instance.queue_repository.replace(items, instance._capture_options(), [])
    instance._refresh_queue()
    instance._toggle_items([items[0]["id"]])
    assert not instance.queue_repository.snapshot()["items"][0]["enabled"]
    instance.episode_tree.selection_set(items[1]["id"])
    instance.retry_failed()
    assert instance.queue_repository.snapshot()["items"][1]["status"] == "pending"
    instance.episode_tree.selection_set(items[2]["id"])
    instance.cancel_selected()
    assert instance.queue_repository.snapshot()["items"][2]["status"] == "cancelled"
    instance.download_control.cancel()
    instance._start_saved_queue = lambda: pytest.fail("Stopping discovery must prevent automatic download")
    instance._queue_prepared(True)


def test_completed_tab_and_queue_cleanup_preserve_downloaded_files(window, tmp_path, monkeypatch):
    from download_queue import queue_item
    import queue_ui

    _, instance = window
    files = [tmp_path / "one.mp4", tmp_path / "two.mp4"]
    for path in files:
        path.write_bytes(path.name.encode())
    completed = []
    for index, path in enumerate(files):
        item = queue_item(f"https://example.com/done-{index}", f"Done {index}")
        item.update(status="completed", files=[str(path)])
        completed.append(item)
    pending = queue_item("https://example.com/pending", "Pending")
    instance.queue_repository.replace(completed + [pending], instance._capture_options(), [])
    instance._refresh_queue()

    assert instance.episode_tree.get_children() == (pending["id"],)
    assert set(instance.completed_tree.get_children()) == {item["id"] for item in completed}
    instance.episode_tree.selection_set(pending["id"])
    instance.remove_queue_selected()
    assert pending["id"] not in {item["id"] for item in instance.queue_repository.snapshot()["items"]}
    instance.completed_tree.selection_set(completed[0]["id"])
    instance.remove_completed_selected()
    assert files[0].is_file()
    assert completed[0]["id"] not in {item["id"] for item in instance.queue_repository.snapshot()["items"]}

    monkeypatch.setattr(queue_ui.messagebox, "askyesno", lambda *args, **kwargs: True)
    instance.clear_completed()
    assert files[1].is_file()
    assert instance.completed_tree.get_children() == ()
    pending = queue_item("https://example.com/pending-again", "Pending again")
    instance.queue_repository.replace([pending], instance._capture_options(), [])
    instance._refresh_queue()
    instance.clear_queue()
    assert instance.queue_repository.snapshot()["items"] == []
    assert pending["id"] not in instance.episode_tree.get_children()


def test_queue_progress_uses_episode_count_and_preserves_fraction_during_conversion(window):
    from download_queue import queue_item
    _, instance = window
    items = [queue_item(f"https://example.com/{i}", str(i)) for i in range(4)]
    items[0]["status"] = "completed"
    items[1]["status"] = "downloading"
    items[3]["enabled"] = False
    instance.queue_repository.replace(items, instance._capture_options(), [])
    instance._refresh_queue()
    instance.queue_running = True
    instance.queue_initial_done = 1
    instance.download_job_started_at = instance._download_clock() - 10
    instance._handle_media_context({"index": 1, "total": 1, "queue_id": items[1]["id"], "label": "Episode 2"})
    assert "2/3" in instance.download_item_var.get()
    instance._handle_media_progress({"index": 1, "item_total": 1, "queue_id": items[1]["id"], "percent": 50})
    assert float(instance.progress["value"]) == 50
    instance._handle_conversion_progress({"percent": 100})
    assert float(instance.progress["value"]) == 50
